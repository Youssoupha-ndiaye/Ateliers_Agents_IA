"""Moteur de l'agent pour l'interface : traite chaque email dès qu'il est détecté.

Il réutilise tel quel pipeline.traiter_message (garde-fous, agent LLM, double validation).
Ce qui change par rapport à `python agent/pipeline.py` :
  1. réveil immédiat par la veille Gmail (plus d'attente de la relève de 30 s) ;
  2. modèle Ollama préchargé au démarrage et gardé en mémoire (keep_alive), sans rechargement à froid ;
  3. une seule session MCP ouverte pour toute la durée de l'interface, au lieu de lancer
     un nouveau processus serveur à chaque appel d'outil ;
  4. chaque étape (outil appelé, réflexion du modèle, validation) est envoyée en direct à l'interface.
"""
import asyncio
import contextlib
import json
import sys
import threading
import time
import urllib.request

import config

DECISIONS = {
    "visite_creee": "Visite créée et confirmée",
    "transmis": "Transmis à un conseiller",
    "ignore": "Ignoré (email automatique)",
    "repondu": "Réponse envoyée",
    "brouillon_en_attente": "Brouillon en attente",
    "aucune_action": "Aucune action",
    "erreur": "Erreur",
}


def _court(valeur, n: int = 220) -> str:
    if valeur is None:
        return ""
    if not isinstance(valeur, str):
        try:
            valeur = json.dumps(valeur, ensure_ascii=False, default=str)
        except Exception:
            valeur = str(valeur)
    valeur = " ".join(valeur.split())
    return valeur if len(valeur) <= n else valeur[: n - 1] + "…"


class Moteur:
    def __init__(self, etat, gmail, cal, keep_alive: str = "1h", mcp_persistant: bool = True):
        self.etat, self.gmail, self.cal = etat, gmail, cal
        self.keep_alive, self.mcp_persistant = keep_alive, mcp_persistant
        self._reveil = threading.Event()
        self._faits: dict[str, str] = {}  # message_id -> thread_id déjà traités pendant cette session
        self.outils = []
        self._pipeline = None

    # ------------------------------------------------------------------ commandes
    def reveiller(self):
        self._reveil.set()

    def relancer(self, tid: str):
        for mid, t in list(self._faits.items()):
            if t == tid:
                del self._faits[mid]
        self.reveiller()

    def demarrer(self):
        threading.Thread(target=self._executer, daemon=True, name="moteur-agent").start()

    # ------------------------------------------------------------------ boucle
    def _executer(self):
        try:
            asyncio.run(self._boucle())
        except Exception as e:
            self.etat.noter("erreur", f"Le moteur de l'agent s'est arrêté : {e!r}", niveau="erreur")
            self.etat.maj_systeme(modele={"ok": False, "texte": "moteur arrêté (voir l'activité)"})

    async def _boucle(self):
        try:
            import agent as module_agent
            import pipeline
            import tracing
        except ImportError as e:
            self.etat.maj_systeme(modele={"ok": False, "texte": f"module manquant : {e.name}"})
            self.etat.noter("erreur", f"Impossible de charger l'agent : {e}. Installez requirement.txt.",
                            niveau="erreur")
            return
        self._pipeline = pipeline
        self._brancher_traces(tracing)
        self._garder_modele_en_memoire(module_agent)
        threading.Thread(target=self._prechauffer, daemon=True, name="prechauffage-ollama").start()

        async with contextlib.AsyncExitStack() as pile:
            self.outils = await self._outils_mcp(module_agent, pile)
            self._reveil.set()  # première passe immédiate : emails déjà en attente
            while True:
                await asyncio.to_thread(self._reveil.wait, config.RELEVE_SECONDES)
                self._reveil.clear()
                await self._passe()

    async def _passe(self):
        while True:
            try:
                msgs = self.gmail.non_traites()
            except Exception as e:
                self.etat.maj_systeme(gmail={"ok": False, "texte": f"relève impossible : {e}"[:180]})
                return
            a_faire = [m for m in msgs if m["message_id"] not in self._faits]
            if not a_faire:
                self.etat.maj_systeme(file=0)
                return
            for m in a_faire:
                self.etat.statut_agent(m["thread_id"], "en_file")
            for i, m in enumerate(a_faire):
                self.etat.maj_systeme(file=len(a_faire) - i - 1)
                await self._traiter(m)
            # On reboucle : des emails ont pu arriver pendant le traitement.

    async def _traiter(self, m: dict):
        p, tid = self._pipeline, m["thread_id"]
        self._faits[m["message_id"]] = tid
        self.etat.ajouter_mail({"id": m["message_id"], "thread_id": tid, "de": m.get("de", ""),
                                "sujet": m.get("sujet", ""), "corps": m.get("corps", "")}, nouveau=False)
        self.etat.fil_courant = tid
        self.etat.maj_systeme(en_cours=tid)
        self.etat.statut_agent(tid, "en_cours")
        self.etat.etape(tid, "L'agent prend la demande en charge", "debut")
        t0 = time.time()
        try:
            dossiers = p.charger_etat()
            r = await p.traiter_message(m, self.gmail, self.cal, self.outils, dossiers)
            p.sauver_etat(dossiers)
            decision = r.get("decision")
            texte = DECISIONS.get(decision, decision)
            self.etat.statut_agent(tid, "erreur" if r.get("erreur") else "termine", decision)
            self.etat.etape(tid, f"Terminé : {texte} ({time.time() - t0:.0f} s)",
                            "erreur" if r.get("erreur") else "fin", _court(r.get("erreur")))
            niveau = {"visite_creee": "succes", "repondu": "succes", "transmis": "attention",
                      "erreur": "erreur"}.get(decision, "info")
            self.etat.noter("agent", f"Traitement terminé : {texte}", tid, niveau)
        except Exception as e:
            self.etat.statut_agent(tid, "erreur", "erreur")
            self.etat.etape(tid, "Erreur du moteur", "erreur", _court(repr(e), 400))
            self.etat.noter("erreur", f"Erreur pendant le traitement : {e!r}"[:300], tid, "erreur")
        finally:
            self.etat.fil_courant = None
            self.etat.maj_systeme(en_cours=None)
            self.etat.recharger_dossiers()

    # ------------------------------------------------------------------ accélérations
    def _garder_modele_en_memoire(self, module_agent):
        """Même modèle que agent.modele(), avec keep_alive : Ollama ne le décharge plus entre deux emails."""
        try:
            from langchain_ollama import ChatOllama

            def modele():
                return ChatOllama(model=config.OLLAMA_MODEL, base_url=config.OLLAMA_BASE_URL,
                                  temperature=0, num_ctx=8192, keep_alive=self.keep_alive)

            modele()  # vérifie que la version installée accepte keep_alive
            module_agent.modele = modele
        except Exception as e:
            self.etat.noter("systeme", f"keep_alive non appliqué ({e}) : modèle d'origine conservé", niveau="info")

    def _prechauffer(self):
        self.etat.maj_systeme(modele={"ok": None, "texte": f"chargement de {config.OLLAMA_MODEL}…"})
        try:
            corps = json.dumps({"model": config.OLLAMA_MODEL, "prompt": "", "keep_alive": self.keep_alive}).encode()
            req = urllib.request.Request(config.OLLAMA_BASE_URL.rstrip("/") + "/api/generate", data=corps,
                                         headers={"Content-Type": "application/json"})
            t0 = time.time()
            urllib.request.urlopen(req, timeout=600).read()
            self.etat.maj_systeme(modele={"ok": True, "texte": f"{config.OLLAMA_MODEL} prêt en mémoire"})
            self.etat.noter("systeme", f"Modèle {config.OLLAMA_MODEL} chargé en {time.time() - t0:.0f} s", niveau="succes")
        except Exception as e:
            self.etat.maj_systeme(modele={"ok": False, "texte": f"Ollama injoignable : {e}"[:180]})
            self.etat.noter("erreur", f"Ollama injoignable ({config.OLLAMA_BASE_URL}) : lancez « ollama serve ».",
                            niveau="erreur")

    async def _outils_mcp(self, module_agent, pile):
        if self.mcp_persistant:
            try:
                from langchain_mcp_adapters.client import MultiServerMCPClient
                from langchain_mcp_adapters.tools import load_mcp_tools
                if config.MCP_TRANSPORT == "http":
                    conf = {"catalogue": {"transport": "streamable_http", "url": config.MCP_HTTP_URL}}
                else:
                    serveur = module_agent.SERVER
                    conf = {"catalogue": {"transport": "stdio", "command": sys.executable,
                                          "args": [str(serveur)], "cwd": str(serveur.parent)}}
                session = await pile.enter_async_context(MultiServerMCPClient(conf).session("catalogue"))
                outils = await load_mcp_tools(session)
                self.etat.maj_systeme(mcp={"ok": True, "texte": f"session persistante · {len(outils)} outils"})
                return outils
            except Exception as e:
                self.etat.noter("systeme", f"Session MCP persistante impossible ({e}) : mode standard", niveau="info")
        try:
            outils = await module_agent.outils_catalogue_mcp()
            self.etat.maj_systeme(mcp={"ok": True, "texte": f"{len(outils)} outils (mode standard)"})
            return outils
        except Exception as e:
            self.etat.maj_systeme(mcp={"ok": False, "texte": f"serveur MCP indisponible : {e}"[:180]})
            self.etat.noter("erreur", f"Catalogue MCP indisponible : {e}", niveau="erreur")
            return []

    # ------------------------------------------------------------------ suivi en direct
    def _brancher_traces(self, tracing):
        """Chaque tracing.log(...) du projet est aussi traduit en étape lisible dans l'interface."""
        origine = tracing.log

        def log(evenement, **data):
            origine(evenement, **data)
            try:
                self._traduire(evenement, data)
            except Exception:
                pass

        tracing.log = log

    def _traduire(self, evt: str, d: dict):
        tid, e = self.etat.fil_courant, self.etat
        if evt == "email_recu":
            e.etape(tid, "Lecture de l'email et de l'historique du fil", "lecture")
        elif evt == "ignore_automatique":
            e.etape(tid, f"Email automatique : {d.get('motif')} → ignoré, sans appeler l'IA ni répondre", "alerte")
        elif evt == "transmis_conseiller":
            e.etape(tid, f"Garde-fou : {d.get('motif')} → transmis à un conseiller, sans appeler l'IA", "alerte")
        elif evt == "tool_start":
            e.etape(tid, f"Outil : {d.get('outil')}", "outil", _court(d.get("arguments")))
        elif evt == "tool_end":
            e.completer_etape(tid, resultat=_court(d.get("resultat"), 320), duree=d.get("duree_s"))
        elif evt == "tool_error":
            e.etape(tid, "Erreur d'outil", "erreur", _court(d.get("erreur")))
        elif evt == "llm_end":
            appels = [c.get("name") for c in (d.get("tool_calls") or []) if isinstance(c, dict)]
            texte = f"Le modèle décide d'appeler {', '.join(appels)}" if appels else "Le modèle rédige sa réponse"
            e.etape(tid, texte, "llm", None, duree=d.get("duree_s"))
        elif evt == "validation_humaine":
            ok = d.get("accord")
            e.etape(tid, f"Décision du conseiller : {'validé' if ok else 'refusé'}", "succes" if ok else "alerte",
                    _court(d.get("motif")))
        elif evt == "brouillon_pipeline":
            e.etape(tid, "Filet de sécurité : le pipeline crée le brouillon", "info")
        elif evt == "clarification_bien_requise":
            e.etape(tid, "Le client doit préciser le logement : question préparée", "info")
        elif evt == "confirmation_client_traitee":
            e.etape(tid, f"Choix du client reconnu : {d.get('bien_id')}", "info")
        elif evt == "confirmation_envoyee":
            e.etape(tid, "Confirmation de visite envoyée", "succes")
        elif evt == "erreur_pipeline":
            e.etape(tid, "Erreur pendant le traitement → transmis à un conseiller", "erreur", _court(d.get("erreur")))
