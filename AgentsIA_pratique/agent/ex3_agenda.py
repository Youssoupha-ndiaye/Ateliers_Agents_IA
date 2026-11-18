"""Exercice 3 : l'agenda des conseillers (Google Calendar).

Démontre les trois points du critère d'acceptation :
  1. « jeudi prochain en fin d'après-midi » -> trois créneaux RÉELLEMENT libres ;
  2. deux demandes sur le même créneau -> aucun doublon ;
  3. l'événement validé apparaît dans l'agenda du bon conseiller, avec la bonne adresse.

Usage :
  python agent/ex3_agenda.py demo                     # les 3 points du critère, sur le VRAI Google Calendar
  python agent/ex3_agenda.py demo --fake              # idem, sur un agenda en mémoire (sans compte Google)
  python agent/ex3_agenda.py creneaux "jeudi prochain" "fin d'apres-midi"
  python agent/ex3_agenda.py deplacer <conseiller> <event_id> <nouveau_debut_iso>
  python agent/ex3_agenda.py annuler  <conseiller> <event_id>
  python agent/ex3_agenda.py agent "Je voudrais visiter le SK-001 jeudi prochain en fin d'après-midi. Awa Ba, awa.ba@example.com"

Toute écriture (création, déplacement, annulation) s'arrête pour demander l'accord d'un conseiller
dans le terminal. Option --auto (avec --fake uniquement) : conseiller simulé qui valide, pour les tests.
"""
import asyncio
import json
import sys
import threading
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp_server"))

import calendar_tools  # noqa: E402
import config  # noqa: E402
import regles  # noqa: E402
import tracing  # noqa: E402
import validation  # noqa: E402

FAKE = "--fake" in sys.argv
ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]


def backend():
    if FAKE:
        # Jeudi prochain : Awa occupée de 16 h à 18 h, Moussa de 16 h à 17 h -> il reste Fatou et Moussa à 17 h.
        j = regles.resoudre_date("jeudi prochain")
        h = lambda hh: datetime.combine(j, datetime.min.time(), regles.TZ).replace(hour=hh)
        return calendar_tools.FakeCalendarBackend(occupe={"Awa": [(h(16), h(18))], "Moussa": [(h(16), h(17))]})
    return calendar_tools.GoogleCalendarBackend(config.conseillers())


def afficher(titre, r):
    print(f"\n--- {titre}\n" + json.dumps(r, ensure_ascii=False, indent=2, default=str))


def adresse_du_bien(bien_id: str) -> str:
    import catalogue
    r = catalogue.detail(bien_id)
    if not r["ok"]:
        raise SystemExit(r["erreur"])
    return r["bien"]["adresse"]


def demo():
    ag = calendar_tools.Agenda(backend())
    tracing.demarrer("ex3_demo")

    # 1) trois créneaux réellement libres
    r = ag.lister_creneaux_libres("jeudi prochain", "fin d'apres-midi", 3)
    afficher("1. Créneaux libres « jeudi prochain en fin d'après-midi »", r)
    if not r.get("creneaux"):
        raise SystemExit("Aucun créneau libre : libérez l'agenda de test jeudi prochain 16 h-18 h et relancez.")
    c = r["creneaux"][0]
    bien, adresse = "SK-001", adresse_du_bien("SK-001")

    # 2) deux demandes simultanées sur le même créneau -> une seule visite créée
    print("\n2. Deux demandes arrivent en même temps sur le même créneau "
          f"({c['conseiller']}, {c['debut']}). Validez la première ; la seconde doit être refusée.")
    resultats = []

    def demande(nom):
        resultats.append(ag.creer_visite(c["conseiller"], c["debut"], bien, adresse, nom, f"{nom.lower()}@example.com"))

    t1 = threading.Thread(target=demande, args=("Client-A",))
    t2 = threading.Thread(target=demande, args=("Client-B",))
    t1.start(); t2.start(); t1.join(); t2.join()
    for i, x in enumerate(resultats, 1):
        afficher(f"Demande {i}", x)
    crees = [x for x in resultats if x.get("ok")]
    print(f"\n=> Visites créées : {len(crees)} (attendu : 1 au plus). Doublon : {'NON ✅' if len(crees) <= 1 else 'OUI ❌'}")

    # 3) l'événement est dans le bon agenda, avec la bonne adresse
    if crees:
        ev = ag.b.lire(c["conseiller"], crees[0]["event_id"])
        afficher("3. Événement relu dans l'agenda", {"conseiller": c["conseiller"], "titre": ev.get("summary"),
                                                    "lieu": ev.get("location"), "debut": ev["start"], "fin": ev["end"]})
        print(f"Lieu = adresse du bien {bien} : {'✅' if ev.get('location') == adresse else '❌'}")
        print(f"\nPour nettoyer : python agent/ex3_agenda.py annuler {c['conseiller']} {crees[0]['event_id']}")


async def agent(demande: str):
    from langchain_core.messages import HumanMessage, SystemMessage
    from agent import construire_agent, outils_catalogue_mcp, run_config
    from zoneinfo import ZoneInfo
    outils = await outils_catalogue_mcp() + calendar_tools.construire_outils(backend())
    sysmsg = (f"Tu es l'assistant de l'agence Sama Kër Immobilier. Nous sommes le "
              f"{datetime.now(ZoneInfo('Africa/Dakar')):%A %d/%m/%Y %H:%M} (Africa/Dakar). Réponds en français. "
              "Pour proposer une visite : detail_bien (adresse), puis lister_creneaux_libres en recopiant l'expression "
              "de date du client (ne calcule pas la date). Propose les créneaux renvoyés, jamais d'horaire inventé. "
              "N'appelle creer_visite que si le client confirme explicitement un créneau déjà proposé.")
    tracing.demarrer("ex3_agent")
    r = await construire_agent(outils).ainvoke({"messages": [SystemMessage(sysmsg), HumanMessage(demande)]},
                                               run_config([tracing.TraceHandler()]))
    print(r["messages"][-1].content)


def main():
    if FAKE and "--auto" in sys.argv:
        validation.set_approbateur(lambda action, d: (True, "validé (conseiller simulé)"))
    elif sys.stdin.isatty():
        validation.set_approbateur(validation.approbateur_cli)
    cmd = ARGS[0] if ARGS else "demo"
    if cmd == "demo":
        demo()
    elif cmd == "creneaux":
        afficher("Créneaux", calendar_tools.Agenda(backend()).lister_creneaux_libres(
            ARGS[1] if len(ARGS) > 1 else "jeudi prochain", ARGS[2] if len(ARGS) > 2 else "fin d'apres-midi"))
    elif cmd == "deplacer":
        afficher("Déplacement", calendar_tools.Agenda(backend()).deplacer_visite(ARGS[1], ARGS[2], ARGS[3]))
    elif cmd == "annuler":
        afficher("Annulation", calendar_tools.Agenda(backend()).annuler_visite(ARGS[1], ARGS[2], "nettoyage démo"))
    elif cmd == "agent":
        asyncio.run(agent(" ".join(ARGS[1:])))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
