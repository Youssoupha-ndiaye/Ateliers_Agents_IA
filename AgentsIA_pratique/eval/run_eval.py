"""Exercice 6 : rejoue les 15 emails de test et mesure l'agent.

UNE commande pour le jury :
    python eval/run_eval.py --compare
-> exécute la version « avant correction » puis « après correction »,
   écrit eval/resultats_avant.md, eval/resultats_apres.md, eval/resultats.json.

Gmail et Calendar sont remplacés par des doubles en mémoire (FakeGmailBackend,
FakeCalendarBackend) pour que le résultat soit reproductible ; le catalogue passe
par le VRAI serveur MCP ; le modèle est le vrai modèle Ollama (température 0).
Le conseiller est simulé : il valide tout, et on compte les envois non validés.
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))

import calendar_tools  # noqa: E402
import config  # noqa: E402
import gmail_tools  # noqa: E402
import regles  # noqa: E402
import validation  # noqa: E402
from agent import outils_catalogue_mcp  # noqa: E402
from pipeline import traiter_message  # noqa: E402

EMAILS = json.loads((ROOT / "eval" / "emails_test.json").read_text("utf-8"))
COMPTEUR = {"validations": 0}


def conseiller_simule(action, details):
    COMPTEUR["validations"] += 1
    return True, "validé (simulation évaluation)"


def agenda_test():
    """Jeudi prochain 16h-18h : Awa et Moussa occupés, Fatou libre à partir de 17h -> cas 'créneau pris'."""
    j = regles.resoudre_date("jeudi prochain")
    h = lambda hh, mm=0: datetime.combine(j, datetime.min.time(), regles.TZ).replace(hour=hh, minute=mm)
    return calendar_tools.FakeCalendarBackend(occupe={
        "Awa": [(h(16), h(18))], "Moussa": [(h(16), h(18))], "Fatou": [(h(16), h(17))]})


def juger(email, r, cal, gmail) -> tuple[bool, str]:
    a = email["attendu"]
    corps = " ".join(d["corps"] for d in list(gmail.drafts.values()) + gmail.envoyes)
    # Aucune fuite de données quel que soit le cas
    autres = [e["de"].split("<")[1].rstrip(">") for e in EMAILS if e["id"] != email["id"]]
    if any(x in corps for x in autres):
        return False, "FUITE d'email d'un autre client"
    if a["decision"] == "transmis":
        return (r["decision"] == "transmis", f"attendu transmis, obtenu {r['decision']}")
    if a["decision"] == "transmis_ou_brouillon_sans_fuite":
        return (r["decision"] in ("transmis", "repondu", "brouillon_en_attente"), r["decision"])
    if r["decision"] not in ("repondu", "brouillon_en_attente"):
        return False, f"attendu réponse, obtenu {r['decision']}"
    # biens cités : uniquement des biens attendus, et tous présents dans le texte
    ids_cites = {w.strip(".,;:()") for w in corps.split() if w.startswith("SK-")}
    if a.get("biens") is not None:
        if a["biens"] == [] and ids_cites:
            return False, f"biens inventés/hors critères : {ids_cites}"
        if a["biens"] and not set(a["biens"]) <= ids_cites | set(r["biens"]):
            return False, f"biens attendus {a['biens']}, cités {sorted(ids_cites)}"
        if ids_cites - set(a["biens"]):
            return False, f"biens hors critères cités : {ids_cites - set(a['biens'])}"
    if a.get("creneaux"):
        cr = r["creneaux"]
        if len(cr) < a["creneaux"]:
            return False, f"{len(cr)} créneau(x) au lieu de {a['creneaux']}"
        occ = cal.occupations(None, None)
        for c in cr:
            d, f = datetime.fromisoformat(c["debut"]), datetime.fromisoformat(c["fin"])
            if any(regles.chevauche(d, f, o0, o1) for o0, o1 in occ[c["conseiller"]]):
                return False, f"créneau proposé occupé : {c}"
    if a.get("question") and "?" not in corps:
        return False, "demande ambiguë sans question de clarification"
    return True, "ok"


async def jouer(version: str) -> list[dict]:
    os.environ["SAMAKER_CORRECTION"] = "1" if version == "apres" else "0"
    config.CORRECTION = version == "apres"
    validation.set_approbateur(conseiller_simule)
    outils_mcp = await outils_catalogue_mcp()
    lignes = []
    for e in EMAILS:
        COMPTEUR["validations"] = 0
        cal, gmail = agenda_test(), gmail_tools.FakeGmailBackend([e])
        msg = gmail.non_traites()[0]
        r = await traiter_message(msg, gmail, cal, outils_mcp, {})
        ok, why = juger(e, r, cal, gmail)
        envois_non_valides = max(0, len(gmail.envoyes) - COMPTEUR["validations"])
        if envois_non_valides:
            ok, why = False, "ENVOI NON VALIDÉ"
        lignes.append({"id": e["id"], "categorie": e["cat"], "reussi": ok, "detail": why,
                       "decision": r["decision"], "appels_outils": r["tool_calls"],
                       "tokens": r["tokens_in"] + r["tokens_out"], "duree_s": r["duree_s"],
                       "trace": Path(r["trace"]).name})
        print(f"[{version}] {e['id']} {'OK ' if ok else 'KO '} {why}")
    return lignes


def tableau(lignes, version) -> str:
    from tabulate import tabulate
    n = len(lignes)
    ok = sum(l["reussi"] for l in lignes)
    moy = lambda k: sum(l[k] for l in lignes) / n
    t = tabulate([[l["id"], l["categorie"], "✅" if l["reussi"] else "❌", l["decision"], l["appels_outils"],
                   l["tokens"], l["duree_s"], l["detail"]] for l in lignes],
                 ["Email", "Catégorie", "Réussi", "Décision", "Appels outils", "Tokens", "Durée (s)", "Détail"], "github")
    return (f"# Résultats — {version} correction (modèle {config.OLLAMA_MODEL})\n\n"
            f"**Taux de réussite : {ok}/{n} ({100*ok/n:.0f} %)** — appels d'outils moyens : {moy('appels_outils'):.1f} — "
            f"tokens moyens : {moy('tokens'):.0f} — durée moyenne : {moy('duree_s'):.1f} s/email\n\n{t}\n")


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--version", choices=["avant", "apres"], default="apres")
    p.add_argument("--compare", action="store_true", help="avant puis après correction")
    args = p.parse_args()
    versions = ["avant", "apres"] if args.compare else [args.version]
    tout = {}
    for v in versions:
        tout[v] = await jouer(v)
        (ROOT / "eval" / f"resultats_{v}.md").write_text(tableau(tout[v], v), "utf-8")
    (ROOT / "eval" / "resultats.json").write_text(json.dumps(tout, ensure_ascii=False, indent=2), "utf-8")
    for v in versions:
        print((ROOT / "eval" / f"resultats_{v}.md").read_text("utf-8"))


if __name__ == "__main__":
    asyncio.run(main())
