"""État partagé de l'interface + diffusion temps réel (Server-Sent Events).

Tous les threads (veille Gmail, moteur de l'agent, validations) écrivent dans `Etat`.
Chaque modification est poussée immédiatement à tous les navigateurs ouverts via le `Bus` :
aucune page n'a besoin de se rafraîchir pour voir un nouvel email ou une validation.
"""
import asyncio
import itertools
import json
import threading
from collections import deque
from datetime import datetime
from email.utils import parseaddr

import config

LIBELLES = {
    "a_traiter": "À traiter",
    "attente_validation": "En attente de validation",
    "repondu": "Répondu",
    "transmis": "Transmis à un conseiller",
    "ignore": "Ignoré (email automatique)",
}


def maintenant() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Bus:
    """Une file asyncio par navigateur connecté ; publication possible depuis n'importe quel thread."""

    def __init__(self):
        self._abonnes = set()
        self._verrou = threading.Lock()

    def abonner(self, boucle):
        abo = (boucle, asyncio.Queue(maxsize=2000))
        with self._verrou:
            self._abonnes.add(abo)
        return abo

    def desabonner(self, abo):
        with self._verrou:
            self._abonnes.discard(abo)

    def diffuser(self, message: str):
        with self._verrou:
            abonnes = list(self._abonnes)
        for abo in abonnes:
            boucle, file = abo
            try:
                boucle.call_soon_threadsafe(_deposer, file, message)
            except RuntimeError:  # onglet fermé, boucle arrêtée
                self.desabonner(abo)


def _deposer(file, message):
    try:
        file.put_nowait(message)
    except asyncio.QueueFull:
        pass


class Etat:
    def __init__(self, bus: Bus, mode: str):
        self.bus = bus
        self._v = threading.RLock()
        self._ids = itertools.count(1)
        self.mails: dict[str, dict] = {}
        self.fils: dict[str, dict] = {}
        self.validations: dict[str, dict] = {}
        self.activite = deque(maxlen=300)
        self.visites: list[dict] = []
        self.dossiers: dict[str, dict] = {}
        self.conseillers: list[dict] = []
        self.fil_courant = None  # fil que l'agent traite en ce moment (un seul à la fois)
        self.systeme = {
            "mode": mode,
            "demarre": maintenant(),
            "nom_modele": config.OLLAMA_MODEL,
            "gmail": {"ok": None, "texte": "connexion…"},
            "modele": {"ok": None, "texte": "en attente"},
            "mcp": {"ok": None, "texte": "en attente"},
            "agenda": {"ok": None, "texte": "en attente"},
            "detection_s": None,
            "releve_s": config.RELEVE_SECONDES,
            "derniere_releve": None,
            "en_cours": None,
            "file": 0,
        }

    # ------------------------------------------------------------------ diffusion
    def _pub(self, type_: str, data):
        with self._v:
            message = json.dumps({"type": type_, "data": data}, ensure_ascii=False, default=str)
        self.bus.diffuser(message)

    def signal(self, type_: str, **data):
        self._pub(type_, {"ts": maintenant(), **data})

    def instantane(self) -> dict:
        with self._v:
            mails = sorted(self.mails.values(), key=lambda m: m.get("date") or "", reverse=True)[:400]
            vals = sorted(self.validations.values(), key=lambda v: v["cree"], reverse=True)[:150]
            brut = {"systeme": self.systeme, "mails": mails, "fils": self.fils, "validations": vals,
                    "activite": list(reversed(self.activite))[:150], "visites": self.visites,
                    "dossiers": self.dossiers, "conseillers": self.conseillers}
            return json.loads(json.dumps(brut, ensure_ascii=False, default=str))

    # ------------------------------------------------------------------ mails et fils
    def _fil(self, tid: str) -> dict:
        f = self.fils.get(tid)
        if f is None:
            f = self.fils[tid] = {"thread_id": tid, "etat": None, "agent": None, "decision": None,
                                  "etapes": [], "maj": maintenant()}
        return f

    def ajouter_mail(self, m: dict, nouveau: bool) -> bool:
        nom, email = parseaddr(m.get("de", ""))
        corps = m.get("corps") or ""
        rec = {"id": m["id"], "thread_id": m["thread_id"], "de": m.get("de", ""),
               "nom": nom or email or "Expéditeur inconnu", "email": email,
               "sujet": m.get("sujet") or "(sans objet)", "corps": corps,
               "extrait": " ".join(corps.split())[:180], "date": m.get("date") or maintenant(),
               "nouveau": nouveau}
        with self._v:
            if rec["id"] in self.mails:
                return False
            self.mails[rec["id"]] = rec
            fil = self._fil(rec["thread_id"])
            if m.get("etat") and not fil["etat"]:
                fil["etat"] = m["etat"]
            self._pub("mail", rec)
            self._pub("fil", fil)
        if nouveau:
            self.noter("mail", f"Nouvel email de {rec['nom']} : {rec['sujet']}", rec["thread_id"], "info")
        return True

    def maj_etat_fil(self, tid: str, etat: str):
        if not tid:
            return
        with self._v:
            f = self._fil(tid)
            f["etat"], f["maj"] = etat, maintenant()
            self._pub("fil", f)
        if etat == "transmis":
            self.noter("transmis", "Demande transmise à un conseiller", tid, "attention")

    def statut_agent(self, tid: str, statut: str, decision=None):
        with self._v:
            f = self._fil(tid)
            f["agent"], f["maj"] = statut, maintenant()
            if decision is not None:
                f["decision"] = decision
            self._pub("fil", f)

    def etape(self, tid, texte: str, type_: str = "info", detail=None, **extra):
        if not tid:
            return
        with self._v:
            f = self._fil(tid)
            f["etapes"].append({"ts": maintenant(), "type": type_, "texte": texte, "detail": detail, **extra})
            del f["etapes"][:-150]
            f["maj"] = maintenant()
            self._pub("fil", f)

    def completer_etape(self, tid, **champs):
        if not tid:
            return
        with self._v:
            f = self._fil(tid)
            if f["etapes"]:
                f["etapes"][-1].update({k: v for k, v in champs.items() if v is not None})
                self._pub("fil", f)

    # ------------------------------------------------------------------ validations
    def nouvelle_validation(self, **champs) -> dict:
        with self._v:
            vid = f"v{next(self._ids)}"
            v = {"id": vid, "cree": maintenant(), "statut": "en_attente", "motif": None, "decide": None, **champs}
            self.validations[vid] = v
            self._pub("validation", v)
        self.noter("validation", f"Validation demandée : {v.get('titre')} — {v.get('client') or ''}".strip(" —"),
                   v.get("thread_id"), "attention")
        return v

    def decision_validation(self, vid: str, accord: bool, motif: str):
        with self._v:
            v = self.validations[vid]
            v.update(statut="validee" if accord else "refusee", motif=motif, decide=maintenant())
            self._pub("validation", v)
        self.noter("decision", f"{v.get('titre')} {'validée' if accord else 'refusée'} par le conseiller",
                   v.get("thread_id"), "succes" if accord else "attention")

    # ------------------------------------------------------------------ divers
    def noter(self, type_: str, texte: str, tid=None, niveau: str = "info"):
        e = {"ts": maintenant(), "type": type_, "texte": texte, "thread_id": tid, "niveau": niveau}
        with self._v:
            self.activite.append(e)
        self._pub("activite", e)

    def maj_systeme(self, publier: bool = True, **champs):
        with self._v:
            change = any(self.systeme.get(k) != v for k, v in champs.items())
            self.systeme.update(champs)
            if publier and change:
                self._pub("systeme", self.systeme)

    def definir_conseillers(self, noms):
        couleurs = ["#7C5CFC", "#0E9F6E", "#E8590C", "#2F80ED", "#D6336C", "#B7791F"]
        with self._v:
            self.conseillers = [{"nom": n, "couleur": couleurs[i % len(couleurs)]} for i, n in enumerate(noms)]
            self._pub("conseillers", self.conseillers)

    def recharger_dossiers(self):
        """Relit state/conversations.json (écrit par le pipeline) : dossiers des fils et visites créées."""
        chemin = config.STATE_DIR / "conversations.json"
        try:
            brut = json.loads(chemin.read_text("utf-8")) if chemin.exists() else {}
        except Exception:
            brut = {}
        visites, dossiers = [], {}
        for tid, d in brut.items():
            for v in d.get("visites", []) or []:
                visites.append({**v, "thread_id": tid, "client": d.get("client", "")})
            dossiers[tid] = {"client": d.get("client"), "biens_proposes": d.get("biens_proposes", []),
                             "creneaux_proposes": d.get("creneaux_proposes", []),
                             "visites": d.get("visites", []), "historique": (d.get("historique") or [])[-6:]}
        visites.sort(key=lambda v: v.get("debut", ""))
        with self._v:
            self.visites, self.dossiers = visites, dossiers
            self._pub("visites", visites)
            self._pub("dossiers", dossiers)
