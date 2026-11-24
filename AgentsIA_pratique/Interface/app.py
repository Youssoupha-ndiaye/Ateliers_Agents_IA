"""Console conseiller Sama Kër : interface web temps réel de l'agent.

    python Interface/app.py            # vrai Gmail + vrais agendas Google
    python Interface/app.py --demo     # Gmail et agendas simulés (présentation, répétition)

Puis ouvrir http://127.0.0.1:8080 (le navigateur s'ouvre tout seul).
Voir Interface/README.md.
"""
import argparse
import asyncio
import sys
import threading
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path

ICI = Path(__file__).resolve().parent
sys.path.insert(0, str(ICI.parent / "agent"))
sys.path.insert(0, str(ICI))

import config  # noqa: E402
import calendar_tools  # noqa: E402
import gmail_tools  # noqa: E402
import validation  # noqa: E402
from regles import TZ  # noqa: E402

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import FileResponse, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from noyau.evenements import Bus, Etat  # noqa: E402
from noyau.moteur import Moteur  # noqa: E402
from noyau.sources import Lecteur, VeilleGmail, instrumenter_agenda, instrumenter_gmail  # noqa: E402
from noyau.validation_web import ApprobateurWeb  # noqa: E402

STATIQUE = ICI / "static"


class Decision(BaseModel):
    accord: bool
    motif: str = ""


class EmailDemo(BaseModel):
    de: str
    sujet: str
    corps: str
    thread_id: str | None = None


def _agenda_demo(cls):
    """Agenda simulé avec quelques rendez-vous déjà pris, pour que la semaine ne soit pas vide."""
    j = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    jours = [j + timedelta(days=k) for k in range(1, 8) if (j + timedelta(days=k)).weekday() < 6][:4]
    h = lambda d, hh, mm=0: d.replace(hour=hh, minute=mm)  # noqa: E731
    occupe = {
        "Awa": [(h(jours[0], 9), h(jours[0], 10, 30)), (h(jours[2], 15), h(jours[2], 16))],
        "Moussa": [(h(jours[1], 11), h(jours[1], 12)), (h(jours[3], 9, 30), h(jours[3], 11))],
        "Fatou": [(h(jours[0], 14), h(jours[0], 15, 30)), (h(jours[2], 10), h(jours[2], 11))],
    }
    cal = cls(occupe=occupe)
    for c, evs in cal.events.items():
        for e in evs.values():
            e["summary"] = "Rendez-vous (déjà pris)"
    return cal


def construire(args):
    bus = Bus()
    etat = Etat(bus, "demo" if args.demo else "gmail")
    moteur_ref = {}

    def reveiller():
        if "m" in moteur_ref:
            moteur_ref["m"].reveiller()

    if args.demo:
        GmailDemo = instrumenter_gmail(gmail_tools.FakeGmailBackend, etat, sur_reception=reveiller)
        gmail = GmailDemo()
        cal = _agenda_demo(instrumenter_agenda(calendar_tools.FakeCalendarBackend, etat))
        lecteur = Lecteur(gmail, cal)
        etat.maj_systeme(gmail={"ok": True, "texte": "simulé (mode démo)"},
                         agenda={"ok": True, "texte": "simulé (mode démo)"}, detection_s=0)
        veille = None
        noms = list(cal.events)
    else:
        print("Connexion à Gmail et aux agendas Google…")
        gmail = instrumenter_gmail(gmail_tools.GmailBackend, etat)()          # utilisé par l'agent
        conseillers = config.conseillers()
        cal = instrumenter_agenda(calendar_tools.GoogleCalendarBackend, etat)(conseillers)
        lecteur = Lecteur(gmail_tools.GmailBackend(), calendar_tools.GoogleCalendarBackend(conseillers))
        veille = VeilleGmail(gmail_tools.GmailBackend(), etat, reveiller, intervalle=args.detection)
        etat.maj_systeme(agenda={"ok": True, "texte": f"{len(conseillers)} agendas de conseillers"})
        noms = list(conseillers)

    etat.definir_conseillers(noms)
    etat.recharger_dossiers()
    approbateur = ApprobateurWeb(etat)
    validation.set_approbateur(approbateur)  # toutes les validations passent par la page « Validations »
    moteur = Moteur(etat, gmail, cal, keep_alive=args.keep_alive, mcp_persistant=not args.sans_mcp_persistant)
    moteur_ref["m"] = moteur
    return etat, gmail, lecteur, veille, moteur, approbateur


def creer_app(etat, gmail, lecteur, moteur, approbateur, demo: bool) -> FastAPI:
    app = FastAPI(title="Sama Kër – console conseiller", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=STATIQUE), name="static")

    @app.get("/")
    def accueil():
        return FileResponse(STATIQUE / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/api/etat")
    def instantane():
        return etat.instantane()

    @app.get("/api/flux")
    async def flux(request: Request):
        """Server-Sent Events : chaque changement est poussé au navigateur à l'instant où il se produit."""
        abo = etat.bus.abonner(asyncio.get_running_loop())

        async def generer():
            try:
                yield "retry: 1500\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        message = await asyncio.wait_for(abo[1].get(), timeout=15)
                        yield f"data: {message}\n\n"
                    except asyncio.TimeoutError:
                        yield ": maintien\n\n"
            finally:
                etat.bus.desabonner(abo)

        return StreamingResponse(generer(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/fils/{tid}")
    def fil(tid: str):
        try:
            messages = lecteur.fil(tid)
        except Exception as e:
            raise HTTPException(502, f"Lecture du fil impossible : {e}")
        return {"messages": messages, "dossier": etat.dossiers.get(tid), "fil": etat.fils.get(tid)}

    @app.post("/api/fils/{tid}/relancer")
    def relancer(tid: str):
        if etat.fils.get(tid, {}).get("agent") == "en_cours":
            raise HTTPException(409, "L'agent traite déjà ce fil.")
        try:
            lecteur.etiqueter(tid, "a_traiter")
        except Exception as e:
            raise HTTPException(502, f"Impossible de remettre le fil à traiter : {e}")
        etat.maj_etat_fil(tid, "a_traiter")
        etat.noter("systeme", "Fil relancé par le conseiller", tid)
        moteur.relancer(tid)
        return {"ok": True}

    @app.post("/api/validations/{vid}")
    def decider(vid: str, d: Decision):
        if not approbateur.decider(vid, d.accord, d.motif):
            raise HTTPException(404, "Cette validation n'est plus en attente.")
        return {"ok": True}

    @app.post("/api/releve")
    def releve():
        moteur.reveiller()
        return {"ok": True}

    @app.get("/api/agenda")
    def agenda(debut: str, jours: int = 6):
        try:
            d0 = datetime.fromisoformat(debut).replace(tzinfo=TZ)
        except ValueError:
            raise HTTPException(400, "debut doit être au format AAAA-MM-JJ")
        try:
            evts = lecteur.evenements(d0, d0 + timedelta(days=max(1, min(jours, 14))))
            etat.maj_systeme(agenda={"ok": True, "texte": etat.systeme["agenda"]["texte"]})
        except Exception as e:
            etat.maj_systeme(agenda={"ok": False, "texte": f"lecture impossible : {e}"[:180]})
            raise HTTPException(502, f"Lecture de l'agenda impossible : {e}")
        return {"evenements": evts, "conseillers": etat.conseillers}

    @app.post("/api/demo/email")
    def email_demo(e: EmailDemo):
        if not demo:
            raise HTTPException(403, "Disponible uniquement en mode démo (--demo).")
        tid = gmail.recevoir(e.de, e.sujet, e.corps, thread_id=e.thread_id or None)
        return {"ok": True, "thread_id": tid}

    return app


def main():
    p = argparse.ArgumentParser(description="Console conseiller Sama Kër (interface web temps réel)")
    p.add_argument("--demo", action="store_true", help="Gmail et agendas simulés (le modèle et le MCP restent réels)")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--detection", type=float, default=2.0, help="intervalle de détection Gmail en secondes (défaut 2)")
    p.add_argument("--keep-alive", default="1h", help="durée pendant laquelle Ollama garde le modèle en mémoire")
    p.add_argument("--sans-mcp-persistant", action="store_true", help="un processus MCP par appel (comportement d'origine)")
    p.add_argument("--sans-navigateur", action="store_true", help="ne pas ouvrir le navigateur automatiquement")
    args = p.parse_args()

    etat, gmail, lecteur, veille, moteur, approbateur = construire(args)
    app = creer_app(etat, gmail, lecteur, moteur, approbateur, args.demo)
    moteur.demarrer()
    if veille:
        veille.start()

    url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '::') else args.host}:{args.port}"
    print(f"\n  Console Sama Kër prête : {url}   ({'mode démo' if args.demo else 'Gmail réel'})\n"
          "  Ctrl+C pour arrêter.\n")
    if not args.sans_navigateur:
        threading.Timer(1.5, webbrowser.open, [url]).start()

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning", timeout_graceful_shutdown=1)


if __name__ == "__main__":
    main()
