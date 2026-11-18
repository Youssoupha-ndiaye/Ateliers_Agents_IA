"""Accès contrôlé à Google Calendar (Exercice 3).

Quatre actions mises à disposition de l'agent :
    lister_creneaux_libres · creer_visite · deplacer_visite · annuler_visite
Règles de l'agence appliquées par le CODE (regles.py) : visites de 45 min, lun-sam 9 h-18 h,
fuseau Africa/Dakar, adresse du bien dans le champ « Lieu » de l'événement.
Validation humaine (validation.demander) avant CHAQUE création, déplacement ou annulation.

Anti-doublon, trois protections :
  1. verrou autour de « vérifier → valider → écrire » ;
  2. revérification du créneau APRÈS la validation humaine (qui peut prendre des minutes) ;
  3. identifiant d'événement déterministe (conseiller + début) : Google refuse un 2e événement
     avec le même id (HTTP 409), même s'il vient d'un autre processus.

La logique (classe Agenda) ne dépend pas de LangChain : elle est testable seule
(tests/test_ex3_agenda.py). Les outils LangChain sont construits par construire_outils().
"""
import hashlib
import threading
from datetime import datetime, timedelta
from typing import Optional

from pydantic import BaseModel, Field

import regles
from regles import DUREE, TZ
from validation import demander

_verrou = threading.Lock()


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(TZ)


# =====================================================================
# Backends : le vrai (Google) et un faux en mémoire (tests, évaluation)
# =====================================================================
class GoogleCalendarBackend:
    """Lit les occupations avec events.list : couvert par le scope calendar.events
    (freebusy.query exigerait un scope supplémentaire, calendar.readonly ou calendar.freebusy)."""

    def __init__(self, conseillers: dict[str, str]):
        from google_auth import service
        self.svc = service("calendar", "v3")
        self.cal = conseillers  # nom -> calendarId

    def occupations(self, debut: datetime, fin: datetime) -> dict[str, list[tuple[datetime, datetime]]]:
        res = {}
        for nom, cid in self.cal.items():
            items, token = [], None
            while True:
                r = self.svc.events().list(calendarId=cid, timeMin=debut.isoformat(), timeMax=fin.isoformat(),
                                           singleEvents=True, orderBy="startTime", pageToken=token,
                                           timeZone="Africa/Dakar").execute()
                items += r.get("items", [])
                token = r.get("nextPageToken")
                if not token:
                    break
            occ = []
            for e in items:
                if e.get("status") == "cancelled" or e.get("transparency") == "transparent":
                    continue  # événement annulé ou marqué « disponible »
                s, f = e["start"], e["end"]
                if "dateTime" in s:
                    occ.append((_dt(s["dateTime"]), _dt(f["dateTime"])))
                else:  # événement « toute la journée » : bloque la journée
                    j0 = datetime.fromisoformat(s["date"]).replace(tzinfo=TZ)
                    occ.append((j0, datetime.fromisoformat(f["date"]).replace(tzinfo=TZ)))
            res[nom] = occ
        return res

    def lire(self, conseiller, event_id) -> Optional[dict]:
        from googleapiclient.errors import HttpError
        try:
            e = self.svc.events().get(calendarId=self.cal[conseiller], eventId=event_id).execute()
        except HttpError as err:
            if err.resp.status in (404, 410):
                return None
            raise
        if e.get("status") == "cancelled":
            return None
        return {"start": _dt(e["start"]["dateTime"]), "end": _dt(e["end"]["dateTime"]),
                "summary": e.get("summary", ""), "location": e.get("location", "")}

    def inserer(self, conseiller, event_id, body):
        from googleapiclient.errors import HttpError
        cid = self.cal[conseiller]
        try:
            return self.svc.events().insert(calendarId=cid, body={**body, "id": event_id}).execute()
        except HttpError as err:
            if err.resp.status != 409:
                raise
            # Un id supprimé reste réservé chez Google : si l'ancienne visite a été annulée, on la réactive.
            ancien = self.svc.events().get(calendarId=cid, eventId=event_id).execute()
            if ancien.get("status") == "cancelled":
                return self.svc.events().update(calendarId=cid, eventId=event_id,
                                                body={**body, "id": event_id, "status": "confirmed"}).execute()
            raise RuntimeError("DOUBLON : une visite existe déjà pour ce conseiller à cette heure.") from err

    def modifier(self, conseiller, event_id, body):
        return self.svc.events().patch(calendarId=self.cal[conseiller], eventId=event_id, body=body).execute()

    def supprimer(self, conseiller, event_id):
        self.svc.events().delete(calendarId=self.cal[conseiller], eventId=event_id).execute()


class FakeCalendarBackend:
    """Agenda en mémoire, même interface que GoogleCalendarBackend (tests et évaluation hors ligne)."""

    def __init__(self, conseillers=("Awa", "Moussa", "Fatou"), occupe=None):
        self.events = {c: {} for c in conseillers}
        for c, lst in (occupe or {}).items():
            for i, (d, f) in enumerate(lst):
                self.events[c][f"busy{i}"] = {"start": d, "end": f, "summary": "Occupé"}

    def occupations(self, debut, fin):
        return {c: [(e["start"], e["end"]) for e in ev.values()] for c, ev in self.events.items()}

    def lire(self, conseiller, event_id):
        return self.events.get(conseiller, {}).get(event_id)

    def inserer(self, conseiller, event_id, body):
        if event_id in self.events[conseiller]:
            raise RuntimeError("DOUBLON : une visite existe déjà pour ce conseiller à cette heure.")
        self.events[conseiller][event_id] = {
            **body, "start": _dt(body["start"]["dateTime"]), "end": _dt(body["end"]["dateTime"])}
        return {"id": event_id, "htmlLink": f"fake://{conseiller}/{event_id}"}

    def modifier(self, conseiller, event_id, body):
        e = self.events[conseiller][event_id]
        e.update(start=_dt(body["start"]["dateTime"]), end=_dt(body["end"]["dateTime"]))
        return {"id": event_id}

    def supprimer(self, conseiller, event_id):
        self.events[conseiller].pop(event_id)


def event_id(conseiller: str, debut: datetime) -> str:
    """Id déterministe (caractères autorisés par Google : a-v et 0-9)."""
    return "sk" + hashlib.sha1(f"{conseiller}|{debut.astimezone(TZ).isoformat()}".encode()).hexdigest()[:24]


def _plage(t: datetime) -> dict:
    return {"start": {"dateTime": t.isoformat(), "timeZone": "Africa/Dakar"},
            "end": {"dateTime": (t + DUREE).isoformat(), "timeZone": "Africa/Dakar"}}


# =====================================================================
# Logique métier (sans LangChain)
# =====================================================================
class Agenda:
    def __init__(self, backend):
        self.b = backend

    def conseillers(self) -> list[str]:
        maintenant = datetime.now(TZ)
        return list(self.b.occupations(maintenant, maintenant + timedelta(minutes=1)))

    def _libre(self, conseiller, debut, ignorer_event=None) -> bool:
        if not regles.est_dans_horaires(debut):
            return False
        occ = self.b.occupations(debut - timedelta(hours=2), debut + timedelta(hours=2))[conseiller]
        moi = self.b.lire(conseiller, ignorer_event) if ignorer_event else None
        for a, b in occ:
            if moi and (a, b) == (moi["start"], moi["end"]):
                continue  # la visite que l'on déplace ne se bloque pas elle-même
            if regles.chevauche(debut, debut + DUREE, a, b):
                return False
        return True

    # ---- 1. lister les créneaux libres ----
    def lister_creneaux_libres(self, jour: str, moment: str = "journee", nombre: int = 3) -> dict:
        try:
            d = regles.resoudre_date(jour)
            if d.weekday() == 6:
                return {"ok": True, "date": d.isoformat(), "jour": "dimanche", "creneaux": [],
                        "message": "L'agence ne fait pas de visite le dimanche. Proposer un autre jour (lun-sam)."}
            debut = datetime.combine(d, regles.OUVERTURE, TZ)
            occ = self.b.occupations(debut, datetime.combine(d, regles.FERMETURE, TZ))
            cr = regles.creneaux_libres(d, moment, occ, max(1, min(int(nombre), 6)))
            return {"ok": True, "date": d.isoformat(), "jour": regles.JOURS[d.weekday()], "creneaux": cr,
                    "message": None if cr else
                    "Aucun créneau libre sur cette période (visites lun-sam, 9h-18h). Proposer un autre jour ou moment."}
        except Exception as e:
            return {"ok": False, "erreur": str(e)}

    # ---- 2. créer une visite ----
    def creer_visite(self, conseiller, debut, bien_id, adresse, client_nom, client_email,
                     action="Créer une visite", details_sup: Optional[dict] = None) -> dict:
        try:
            t = _dt(debut)
            if conseiller not in self.conseillers():
                return {"ok": False, "erreur": f"Conseiller '{conseiller}' inconnu. Valeurs : {', '.join(self.conseillers())}."}
            if not regles.est_dans_horaires(t):
                return {"ok": False, "erreur": "HORS_HORAIRES : visites du lundi au samedi, 9h-18h (45 min)."}
            body = {"summary": f"Visite {bien_id} – {client_nom}", "location": adresse,
                    "description": f"Client : {client_nom} <{client_email}>\nBien : {bien_id}\nConseiller : {conseiller}\n"
                                   "Créé par l'agent Sama Kër après validation d'un conseiller.",
                    **_plage(t)}
            with _verrou:
                if not self._libre(conseiller, t):
                    return {"ok": False, "erreur": "CRENEAU_PRIS : ce créneau n'est plus libre. Relancer lister_creneaux_libres."}
                ok, motif = demander(action, {"conseiller": conseiller, "debut": t.strftime("%A %d/%m/%Y %H:%M"),
                                              "fin": (t + DUREE).strftime("%H:%M"), "bien": bien_id, "adresse": adresse,
                                              "client": f"{client_nom} <{client_email}>", **(details_sup or {})})
                if not ok:
                    return {"ok": False, "erreur": f"REFUS_CONSEILLER : {motif}"}
                if not self._libre(conseiller, t):  # revérification après l'attente humaine
                    return {"ok": False, "erreur": "CRENEAU_PRIS entre-temps (pendant la validation). Proposer un autre créneau."}
                ev = self.b.inserer(conseiller, event_id(conseiller, t), body)
            return {"ok": True, "event_id": ev["id"], "conseiller": conseiller, "debut": t.isoformat(),
                    "fin": (t + DUREE).isoformat(), "adresse": adresse, "bien_id": bien_id, "lien": ev.get("htmlLink")}
        except Exception as e:
            return {"ok": False, "erreur": f"Erreur agenda : {e}"}

    # ---- 3. déplacer une visite ----
    def deplacer_visite(self, conseiller, event_id_, nouveau_debut) -> dict:
        try:
            t = _dt(nouveau_debut)
            ancien = self.b.lire(conseiller, event_id_)
            if not ancien:
                return {"ok": False, "erreur": f"Visite '{event_id_}' introuvable dans l'agenda de {conseiller}."}
            with _verrou:
                if not self._libre(conseiller, t, ignorer_event=event_id_):
                    return {"ok": False, "erreur": "CRENEAU_PRIS ou hors horaires (lun-sam 9h-18h)."}
                ok, motif = demander("Déplacer une visite", {
                    "conseiller": conseiller, "visite": ancien.get("summary", event_id_),
                    "ancien_debut": ancien["start"].strftime("%A %d/%m %H:%M"),
                    "nouveau_debut": t.strftime("%A %d/%m %H:%M")})
                if not ok:
                    return {"ok": False, "erreur": f"REFUS_CONSEILLER : {motif}"}
                if not self._libre(conseiller, t, ignorer_event=event_id_):
                    return {"ok": False, "erreur": "CRENEAU_PRIS entre-temps."}
                self.b.modifier(conseiller, event_id_, _plage(t))
            return {"ok": True, "event_id": event_id_, "debut": t.isoformat(), "fin": (t + DUREE).isoformat()}
        except Exception as e:
            return {"ok": False, "erreur": f"Erreur agenda : {e}"}

    # ---- 4. annuler une visite ----
    def annuler_visite(self, conseiller, event_id_, motif=None) -> dict:
        try:
            ev = self.b.lire(conseiller, event_id_)
            if not ev:
                return {"ok": False, "erreur": f"Visite '{event_id_}' introuvable dans l'agenda de {conseiller}."}
            ok, m = demander("Annuler une visite", {"conseiller": conseiller, "visite": ev.get("summary", event_id_),
                                                    "debut": ev["start"].strftime("%A %d/%m %H:%M"), "motif": motif})
            if not ok:
                return {"ok": False, "erreur": f"REFUS_CONSEILLER : {m}"}
            self.b.supprimer(conseiller, event_id_)
            return {"ok": True, "event_id": event_id_, "annulee": True}
        except Exception as e:
            return {"ok": False, "erreur": f"Erreur agenda : {e}"}


# =====================================================================
# Schémas d'arguments (lus par le modèle) et outils LangChain
# =====================================================================
class ArgsCreneaux(BaseModel):
    jour: str = Field(description="Jour souhaité, recopié tel que le client l'écrit : 'jeudi prochain', 'demain', "
                                  "'samedi', 'JJ/MM' ou 'AAAA-MM-JJ'. Ne calcule pas la date toi-même.")
    moment: str = Field("journee", description="matin, midi, apres-midi, fin d'apres-midi ou journee.")
    nombre: int = Field(3, description="Nombre de créneaux à proposer (défaut 3).")


class ArgsCreer(BaseModel):
    conseiller: str = Field(description="Nom du conseiller, recopié depuis lister_creneaux_libres.")
    debut: str = Field(description="Début ISO 8601 exact, recopié depuis lister_creneaux_libres.")
    bien_id: str = Field(description="Identifiant du bien visité, ex. SK-001.")
    adresse: str = Field(description="Adresse du bien, telle que renvoyée par detail_bien.")
    client_nom: str = Field(description="Nom du client.")
    client_email: str = Field(description="Email du client.")


class ArgsDeplacer(BaseModel):
    conseiller: str = Field(description="Conseiller de la visite.")
    event_id: str = Field(description="Identifiant de la visite renvoyé par creer_visite.")
    nouveau_debut: str = Field(description="Nouveau début ISO 8601, recopié depuis lister_creneaux_libres.")


class ArgsAnnuler(BaseModel):
    conseiller: str = Field(description="Conseiller de la visite.")
    event_id: str = Field(description="Identifiant de la visite renvoyé par creer_visite.")
    motif: Optional[str] = Field(None, description="Motif de l'annulation (demande du client…).")


def construire_outils(backend, inclure=("lister", "creer", "deplacer", "annuler")):
    from langchain_core.tools import StructuredTool
    ag = Agenda(backend)
    outils = {
        "lister": StructuredTool.from_function(
            ag.lister_creneaux_libres, name="lister_creneaux_libres", args_schema=ArgsCreneaux,
            description="Liste des créneaux de visite de 45 min RÉELLEMENT libres dans les agendas des conseillers "
                        "(lun-sam 9h-18h, heure de Dakar). Toujours l'appeler avant de proposer un horaire ; "
                        "ne jamais inventer d'horaire."),
        "creer": StructuredTool.from_function(
            lambda conseiller, debut, bien_id, adresse, client_nom, client_email:
                ag.creer_visite(conseiller, debut, bien_id, adresse, client_nom, client_email),
            name="creer_visite", args_schema=ArgsCreer,
            description="Inscrit une visite dans l'agenda d'un conseiller, UNIQUEMENT après confirmation explicite "
                        "du client. Un conseiller humain doit valider (demandé automatiquement)."),
        "deplacer": StructuredTool.from_function(
            lambda conseiller, event_id, nouveau_debut: ag.deplacer_visite(conseiller, event_id, nouveau_debut),
            name="deplacer_visite", args_schema=ArgsDeplacer,
            description="Déplace une visite existante vers un créneau libre (validation humaine automatique)."),
        "annuler": StructuredTool.from_function(
            lambda conseiller, event_id, motif=None: ag.annuler_visite(conseiller, event_id, motif),
            name="annuler_visite", args_schema=ArgsAnnuler,
            description="Annule une visite existante (validation humaine automatique)."),
    }
    return [outils[k] for k in inclure]
