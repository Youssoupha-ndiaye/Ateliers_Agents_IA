"""Exercice 5 : le pipeline complet, de l'email reçu à la visite inscrite dans l'agenda.

Premier email du client :
    garde-fous -> agent : rechercher_biens -> detail_bien -> lister_creneaux_libres
               -> creer_brouillon -> envoyer_brouillon            [VALIDATION 1 : le conseiller relit l'email]
Réponse du client (« je prends jeudi 16 h 45 pour le SK-011 ») dans le même fil :
    agent (avec le fil + le dossier de la conversation) -> confirmer_visite
               = créer la visite + envoyer la confirmation        [VALIDATION 2 : le conseiller voit la visite ET l'email]
=> exactement deux validations humaines, aucune autre intervention.

État : state/conversations.json, une entrée par fil Gmail (thread_id) : biens et créneaux réellement proposés,
visites créées, décisions. Le fil Gmail reste la source de vérité ; le dossier ne contient que des résultats d'outils.

Usage :
  python agent/pipeline.py --once     # traite les emails en attente puis s'arrête
  python agent/pipeline.py            # tourne en continu (relève toutes les 30 s, RELEVE_SECONDES dans .env) — mode démo devant le jury
  python agent/pipeline.py --demo     # répétition hors ligne : Gmail/Agenda simulés, réponse du client simulée
"""
import argparse
import ast
import asyncio
import json
import re
import time
import unicodedata
from datetime import datetime
from email.utils import parseaddr

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

import calendar_tools
import config
import gmail_tools
import regles
import tracing
import validation
from agent import construire_agent, outils_catalogue_mcp, run_config
from garde_fous import classer, est_automatique
from prompts import message_email, systeme

ETAT = config.STATE_DIR / "conversations.json"


def charger_etat() -> dict:
    return json.loads(ETAT.read_text("utf-8")) if ETAT.exists() else {}


def sauver_etat(e: dict):
    config.STATE_DIR.mkdir(exist_ok=True)
    ETAT.write_text(json.dumps(e, ensure_ascii=False, indent=2), "utf-8")


def _resultat(m: ToolMessage):
    """Contenu d'un ToolMessage -> dict (formats possibles : JSON, repr Python, blocs MCP [{'type':'text',...}])."""
    c = m.content
    if isinstance(c, list):
        c = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in c)
    for f in (json.loads, ast.literal_eval):
        try:
            r = f(c)
            return r if isinstance(r, dict) else None
        except Exception:
            pass
    return None


def _texte_reponse_agent(res: dict) -> str:
    return next(
        (m.content.strip() for m in reversed(res["messages"])
         if isinstance(m, AIMessage) and isinstance(m.content, str) and m.content.strip()),
        "",
    )


def _texte_nouveau_message(msg: dict) -> str:
    lignes = []
    for ligne in msg.get("corps", "").splitlines():
        if re.match(r"^\s*(?:Le|On)\s+.*(?:a écrit|wrote)\s*:\s*$", ligne, re.IGNORECASE):
            break
        if ligne.lstrip().startswith(">"):
            break
        lignes.append(ligne)
    return "\n".join(lignes).strip()


def _normaliser(texte: str) -> str:
    texte = texte.replace("–", "-").replace("—", "-").replace("−", "-")
    return unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode().lower()


def _objet_retour(outil_resultat):
    if isinstance(outil_resultat, dict):
        return outil_resultat
    if isinstance(outil_resultat, list):
        outil_resultat = "".join(
            bloc.get("text", "") if isinstance(bloc, dict) else str(bloc)
            for bloc in outil_resultat
        )
    if isinstance(outil_resultat, str):
        try:
            resultat = json.loads(outil_resultat)
            return resultat if isinstance(resultat, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _garantir_validation_brouillon(msg: dict, gmail, ctx: dict, texte: str):
    """Crée puis soumet à validation le premier email si le modèle oublie les outils."""
    if ctx.get("transmis") or ctx.get("cloture") or ctx.get("visite"):
        return
    boite = gmail_tools.BoiteAgence(gmail, ctx)
    if not ctx.get("dernier_brouillon"):
        texte = (texte or "").strip()
        if len(texte) < 20:
            nom = msg["de"].split("<", 1)[0].strip().split()[0]
            texte = (
                f"Bonjour {nom},\n\n"
                "Nous avons bien reçu votre demande. Un conseiller va vérifier les informations "
                "et vous répondra prochainement.\n\n"
                "L'équipe Sama Kër Immobilier"
            )
        resultat = boite.creer_brouillon(ctx["thread_id"], texte)
        if not resultat.get("ok"):
            raise RuntimeError(f"Impossible de créer le brouillon : {resultat.get('erreur')}")
        tracing.log("brouillon_pipeline", draft_id=resultat["draft_id"])
    if ctx.get("dernier_brouillon") and not ctx.get("envoye") and not ctx.get("refus"):
        resultat = boite.envoyer_brouillon(ctx["dernier_brouillon"])
        if not resultat.get("ok") and not ctx.get("refus"):
            raise RuntimeError(f"Impossible de demander la validation : {resultat.get('erreur')}")


async def _traiter_confirmation_client(msg: dict, gmail, agenda, ctx: dict, dossier: dict, outils_mcp) -> bool:
    """Valide les choix explicites du client, ou demande le logement manquant."""
    biens = dossier.get("biens_proposes", [])
    creneaux = dossier.get("creneaux_proposes", [])
    if not biens or not creneaux:
        return False

    texte_client = _texte_nouveau_message(msg)
    normalise = _normaliser(texte_client)
    positif = bool(re.search(
        r"\b(?:oui|ok|d accord|je confirme|je prends|je choisis|ca devrait aller|ca me va|c est bon)\b",
        normalise,
    ))
    heure = re.search(
        r"\b(\d{1,2})[:h](\d{2})\s*(?:-|–|—|a)\s*(\d{1,2})[:h](\d{2})\b",
        normalise,
    )
    if not positif or not heure:
        return False

    debut_choisi = f"{int(heure.group(1)):02d}:{heure.group(2)}"
    fin_choisie = f"{int(heure.group(3)):02d}:{heure.group(4)}"
    creneaux_choisis = [
        c for c in creneaux
        if datetime.fromisoformat(c["debut"]).strftime("%H:%M") == debut_choisi
        and datetime.fromisoformat(c["fin"]).strftime("%H:%M") == fin_choisie
    ]
    if len(creneaux_choisis) != 1:
        return False

    ids_valides = {b.upper() for b in biens}
    ids_choisis = {
        b.upper() for b in re.findall(r"\bSK-\d{3}\b", texte_client, re.IGNORECASE)
        if b.upper() in ids_valides
    }
    if len(ids_choisis) != 1:
        conseiller = creneaux_choisis[0]["conseiller"]
        ids = " et ".join(biens)
        prenom = msg["de"].split("<", 1)[0].strip().split()[0]
        brouillon = (
            f"Bonjour {prenom},\n\n"
            f"Le créneau {debut_choisi}–{fin_choisie} avec {conseiller} est bien noté. "
            f"Pour finaliser la visite, lequel des logements souhaitez-vous visiter : {ids} ?\n\n"
            "L'équipe Sama Kër Immobilier"
        )
        _garantir_validation_brouillon(msg, gmail, ctx, brouillon)
        tracing.log("clarification_bien_requise", creneau=debut_choisi, biens=biens)
        return True

    bien_id = next(iter(ids_choisis))
    detail_tool = next((t for t in outils_mcp if t.name == "detail_bien"), None)
    if detail_tool is None:
        raise RuntimeError("Outil detail_bien absent : impossible de confirmer la visite.")
    detail = _objet_retour(await detail_tool.ainvoke({"bien_id": bien_id}))
    bien = (detail or {}).get("bien")
    if not bien or bien.get("id", "").upper() != bien_id:
        raise RuntimeError(f"Fiche du bien {bien_id} introuvable dans le catalogue.")

    outil = outil_confirmer_visite(
        calendar_tools.Agenda(agenda), gmail, ctx, creneaux
    )
    resultat = await outil.ainvoke({
        "conseiller": creneaux_choisis[0]["conseiller"],
        "debut": creneaux_choisis[0]["debut"],
        "bien_id": bien_id,
        "adresse": bien["adresse"],
    })
    tracing.log("confirmation_client_traitee", bien_id=bien_id, resultat=resultat)
    return True


# ---------------------------------------------------------------------
# Outil composite de l'exercice 5 : une seule validation pour « visite + confirmation »
# ---------------------------------------------------------------------
class ArgsConfirmer(BaseModel):
    conseiller: str = Field(description="Conseiller du créneau choisi, recopié depuis le dossier de la conversation.")
    debut: str = Field(description="Début ISO 8601 du créneau choisi, recopié depuis le dossier de la conversation.")
    bien_id: str = Field(description="Identifiant du bien choisi par le client, ex. SK-011.")
    adresse: str = Field(description="Adresse du bien, telle que renvoyée par detail_bien.")


def _texte_confirmation(prenom, t: datetime, conseiller, bien_id, adresse) -> str:
    fin = t + regles.DUREE
    return (f"Bonjour {prenom},\n\nNous avons le plaisir de vous confirmer votre visite :\n"
            f"- Date : {regles.JOURS[t.weekday()]} {t:%d/%m/%Y}, de {t:%H:%M} à {fin:%H:%M}\n"
            f"- Bien : {bien_id}\n- Adresse : {adresse}\n- Votre conseiller : {conseiller}\n\n"
            "Merci de vous munir d'une pièce d'identité. En cas d'empêchement, répondez simplement à cet email.\n\n"
            "À très bientôt,\nL'équipe Sama Kër Immobilier")


def outil_confirmer_visite(agenda: calendar_tools.Agenda, gmail, ctx: dict, proposes: list[dict]) -> StructuredTool:
    def confirmer_visite(conseiller: str, debut: str, bien_id: str, adresse: str) -> dict:
        if not proposes:
            return {"ok": False, "erreur": "AUCUN_CRENEAU_PROPOSE : aucun créneau n'a encore été proposé dans ce fil. "
                                           "Propose d'abord des créneaux (lister_creneaux_libres puis brouillon)."}
        try:
            t = datetime.fromisoformat(debut).astimezone(regles.TZ)
        except ValueError:
            return {"ok": False, "erreur": f"debut '{debut}' invalide : recopier l'ISO 8601 du dossier."}
        if not any(p["conseiller"] == conseiller and datetime.fromisoformat(p["debut"]) == t for p in proposes):
            return {"ok": False, "erreur": "CRENEAU_NON_PROPOSE : ce créneau ne fait pas partie de ceux proposés au client. "
                                           f"Créneaux proposés : {json.dumps(proposes, ensure_ascii=False)}"}
        nom, email = parseaddr(ctx.get("de", ""))
        texte = _texte_confirmation((nom or "Madame, Monsieur").split()[0], t, conseiller, bien_id, adresse)
        r = agenda.creer_visite(conseiller, t.isoformat(), bien_id, adresse, nom or email, email,
                                action="Créer la visite ET envoyer la confirmation au client",
                                details_sup={"email_de_confirmation_a": ctx.get("de"),
                                             "email_de_confirmation": texte,
                                             "message_recu": ctx.get("message_recu", "")})
        if not r["ok"]:
            return r
        ctx["visite"] = r
        try:
            did = gmail.brouillon(ctx["thread_id"], ctx.get("de", ""), ctx.get("sujet", "Votre visite"), texte,
                                  ctx.get("rfc_message_id", ""))
            gmail.envoyer(did)  # texte validé par le conseiller juste au-dessus (validation 2)
            gmail.etiqueter(ctx["thread_id"], "repondu")
            ctx["envoye"], ctx["cloture"] = True, True
            tracing.log("confirmation_envoyee", thread_id=ctx["thread_id"], event_id=r["event_id"])
        except Exception as e:
            gmail.etiqueter(ctx["thread_id"], "transmis")
            ctx["transmis"] = "echec_envoi_confirmation"
            return {**r, "confirmation_envoyee": False, "erreur": f"Visite créée mais email non envoyé : {e}. Transmis à un conseiller."}
        return {**r, "confirmation_envoyee": True, "consigne": "Terminé. N'écris et n'envoie aucun autre email."}

    return StructuredTool.from_function(
        confirmer_visite, name="confirmer_visite", args_schema=ArgsConfirmer,
        description="À utiliser quand le client CONFIRME un créneau déjà proposé : demande l'accord du conseiller, "
                    "inscrit la visite dans son agenda (adresse du bien dans l'événement) et envoie l'email de confirmation.")


def outil_lister(agenda: calendar_tools.Agenda, ctx: dict) -> StructuredTool:
    def lister_creneaux_libres(jour: str, moment: str = "journee", nombre: int = 3) -> dict:
        r = agenda.lister_creneaux_libres(jour, moment, nombre)
        if r.get("ok"):
            ctx["creneaux_run"] = r.get("creneaux", [])  # mémorisés si un email part avec eux
        return r
    return StructuredTool.from_function(
        lister_creneaux_libres, name="lister_creneaux_libres", args_schema=calendar_tools.ArgsCreneaux,
        description="Liste des créneaux de visite de 45 min RÉELLEMENT libres dans les agendas des conseillers "
                    "(lun-sam 9h-18h, heure de Dakar). Toujours l'appeler avant de proposer un horaire.")


# ---------------------------------------------------------------------
# Traitement d'un message
# ---------------------------------------------------------------------
async def traiter_message(msg: dict, gmail, cal, outils_mcp, etat: dict) -> dict:
    """Traite UN message entrant. Renvoie un résumé (utilisé aussi par eval/run_eval.py)."""
    t0 = time.time()
    tid = msg["thread_id"]
    fichier = tracing.demarrer(f"thread-{tid}")
    tracing.log("email_recu", thread_id=tid, de=msg["de"], sujet=msg["sujet"], corps=msg["corps"])
    dossier = etat.setdefault(tid, {"client": msg["de"], "historique": []})
    gmail.etiqueter(tid, "a_traiter")
    ctx = {"thread_id": tid, "de": msg["de"], "sujet": msg["sujet"],
           "rfc_message_id": msg.get("rfc_message_id", ""),
           "message_recu": _texte_nouveau_message(msg)}
    validations_avant = validation.COMPTEUR["n"]

    def resume(decision, erreur=None):
        dossier["historique"].append({"date": time.strftime("%Y-%m-%d %H:%M"), "decision": decision,
                                      "validations": validation.COMPTEUR["n"] - validations_avant})
        return {"thread_id": tid, "decision": decision, "erreur": erreur, "trace": fichier,
                "draft_id": ctx.get("dernier_brouillon"),
                "motif": ctx.get("ignore") or ctx.get("transmis") or ctx.get("refus"),
                "biens": dossier.get("biens_proposes", []),
                "creneaux": ctx.get("creneaux_run") or dossier.get("creneaux_proposes", []),
                "visite": ctx.get("visite"), "validations": validation.COMPTEUR["n"] - validations_avant,
                "outils": tracing.METRIQUES.get("outils", []), "duree_s": round(time.time() - t0, 2),
                **{k: tracing.METRIQUES.get(k, 0) for k in ("tool_calls", "tokens_in", "tokens_out")}}

    # 0) Email automatique (notification, newsletter, bounce) : ni LLM, ni brouillon, ni validation
    if motif_auto := est_automatique(msg["de"], msg.get("entetes")):
        gmail.etiqueter(tid, "ignore")
        ctx["ignore"] = motif_auto
        tracing.log("ignore_automatique", motif=motif_auto, de=msg["de"])
        return resume("ignore")

    # 1) Garde-fou déterministe (correction de l'exercice 6) : injection / hors périmètre -> humain, sans LLM
    verdict, motif = classer(msg["sujet"] + "\n" + msg["corps"]) if config.CORRECTION else ("ok", None)
    if verdict != "ok":
        gmail.etiqueter(tid, "transmis")
        ctx["transmis"] = motif
        tracing.log("transmis_conseiller", raison=verdict, motif=motif, par="garde_fous")
        return resume("transmis")

    # 2) Agent LLM avec le fil et le dossier de la conversation
    agenda = calendar_tools.Agenda(cal)
    proposes = list(dossier.get("creneaux_proposes", []))
    outils = (outils_mcp
              + [outil_lister(agenda, ctx)]
              + calendar_tools.construire_outils(cal, inclure=("deplacer", "annuler"))
              + gmail_tools.construire_outils(gmail, ctx, inclure=("brouillon", "envoyer", "transmettre"))
              + [outil_confirmer_visite(agenda, gmail, ctx, proposes)])
    memoire = {k: dossier[k] for k in ("biens_proposes", "creneaux_proposes", "visites") if dossier.get(k)}
    contenu = message_email(msg, gmail.fil(tid)[:-1], memoire)

    erreur = None
    try:
        confirmation_traitee = await _traiter_confirmation_client(
            msg, gmail, cal, ctx, dossier, outils_mcp
        )
        if not confirmation_traitee:
            res = await construire_agent(outils).ainvoke(
                {"messages": [SystemMessage(systeme(tid, pipeline=True)), HumanMessage(contenu)]},
                run_config([tracing.TraceHandler()]))
            for m in res["messages"]:
                if isinstance(m, ToolMessage) and m.name == "rechercher_biens" and (r := _resultat(m)) and r.get("ok"):
                    ctx.setdefault("biens_run", []).extend(b["id"] for b in r.get("biens", []))
            tracing.log("reponse_agent", texte=res["messages"][-1].content)
            _garantir_validation_brouillon(msg, gmail, ctx, _texte_reponse_agent(res))
    except Exception as e:  # dépassement du nombre d'étapes, Ollama indisponible...
        erreur = repr(e)
        tracing.log("erreur_pipeline", erreur=erreur)
        if not ctx.get("cloture"):
            gmail.etiqueter(tid, "transmis")  # sortie de route -> un humain reprend la main
            ctx["transmis"] = "erreur_agent"

    # 3) Mise à jour du dossier : uniquement ce qui a réellement été proposé / créé
    if ctx.get("dernier_brouillon"):
        cites = sorted(set(re.findall(r"SK-\d{3}", ctx.get("dernier_corps", ""))))
        dossier["biens_proposes"] = cites or sorted(set(ctx.get("biens_run", [])))
        if ctx.get("creneaux_run"):
            dossier["creneaux_proposes"] = ctx["creneaux_run"]
    if ctx.get("visite"):
        dossier.setdefault("visites", []).append(ctx["visite"])

    if ctx.get("visite") and ctx.get("cloture"):
        decision = "visite_creee"
    elif ctx.get("transmis"):
        decision = "transmis"
    elif ctx.get("envoye"):
        decision = "repondu"
    elif ctx.get("dernier_brouillon"):
        decision = "brouillon_en_attente"
    else:
        decision = "erreur" if erreur else "aucune_action"
    return resume(decision, erreur)


# ---------------------------------------------------------------------
# Boucles
# ---------------------------------------------------------------------
def _afficher(r):
    print(json.dumps({k: r[k] for k in ("thread_id", "decision", "validations", "duree_s", "trace")}, ensure_ascii=False))


async def boucle(once: bool):
    gmail = gmail_tools.GmailBackend()
    cal = calendar_tools.GoogleCalendarBackend(config.conseillers())
    outils_mcp = await outils_catalogue_mcp()
    print(f"Pipeline Sama Kër démarré ({config.OLLAMA_MODEL}). Relève Gmail {'une fois' if once else f'toutes les {config.RELEVE_SECONDES} s'}.")
    while True:
        etat = charger_etat()
        for msg in gmail.non_traites():
            r = await traiter_message(msg, gmail, cal, outils_mcp, etat)
            sauver_etat(etat)
            _afficher(r)
        if once:
            break
        await asyncio.sleep(config.RELEVE_SECONDES)


async def demo(auto: bool):
    """Répétition hors ligne du critère de l'exercice 5 (vrai modèle, vrai serveur MCP, Gmail/Agenda simulés)."""
    if auto:
        validation.set_approbateur(lambda a, d: (True, "validé (conseiller simulé)"))
    else:
        validation.set_approbateur(validation.approbateur_cli)
    gmail = gmail_tools.FakeGmailBackend([{"de": "Aminata Sow <aminata.demo@example.com>", "sujet": "Visite F3 Mermoz",
        "corps": "Bonjour, je cherche un F3 à Mermoz à moins de 400 000 F. Je suis disponible jeudi prochain "
                 "en fin d'après-midi. Merci, Aminata"}])
    cal = calendar_tools.FakeCalendarBackend()
    outils_mcp, etat = await outils_catalogue_mcp(), {}
    msg = gmail.non_traites()[0]
    r1 = await traiter_message(msg, gmail, cal, outils_mcp, etat); _afficher(r1)
    d = etat[msg["thread_id"]]
    if not d.get("creneaux_proposes") or not gmail.envoyes:
        print("Arrêt : aucune réponse envoyée au premier email (voir la trace)."); return
    c, bien = d["creneaux_proposes"][0], (d.get("biens_proposes") or ["SK-001"])[0]
    t = datetime.fromisoformat(c["debut"])
    reponse = f"Merci ! Je prends le {bien}, {regles.JOURS[t.weekday()]} à {t:%H:%M} avec {c['conseiller']}. Aminata"
    print(f"\n>>> Réponse simulée du client : {reponse}\n")
    gmail.recevoir(msg["de"], "Re: " + msg["sujet"], reponse, thread_id=msg["thread_id"])
    r2 = await traiter_message(gmail.non_traites()[0], gmail, cal, outils_mcp, etat); _afficher(r2)
    print(f"\nValidations humaines : {r1['validations']} + {r2['validations']} = {r1['validations'] + r2['validations']} (attendu : 2)")
    print(f"Emails envoyés : {len(gmail.envoyes)} | Visites dans l'agenda : "
          f"{[(c, e['summary'], e['location']) for c, ev in cal.events.items() for e in ev.values()]}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true", help="une seule passe")
    p.add_argument("--demo", action="store_true", help="répétition hors ligne (Gmail et Agenda simulés)")
    p.add_argument("--auto", action="store_true", help="avec --demo : conseiller simulé qui valide tout")
    a = p.parse_args()
    asyncio.run(demo(a.auto) if a.demo else boucle(a.once))
