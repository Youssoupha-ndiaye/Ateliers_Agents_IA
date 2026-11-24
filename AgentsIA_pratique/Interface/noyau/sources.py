"""Gmail et Agenda côté interface.

- VeilleGmail : détecte un nouvel email toutes les 2 s (requête légère : identifiants seulement),
  l'affiche tout de suite dans l'interface et réveille le moteur de l'agent.
- instrumenter_gmail / instrumenter_agenda : sous-classes des backends du projet qui signalent
  à l'interface chaque brouillon, envoi, libellé et visite, sans modifier le code de l'agent.
- Lecteur : lectures pour les pages (conversation complète d'un fil, événements de l'agenda).

Chaque thread a sa propre connexion Google : le client googleapiclient n'est pas thread-safe.
"""
import threading
from datetime import datetime, timezone

import gmail_tools
from noyau.evenements import LIBELLES, maintenant
from regles import TZ


def _iso_ms(ms) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).astimezone(TZ).isoformat(timespec="seconds")


def _entetes(payload) -> dict:
    return {x["name"].lower(): x["value"] for x in payload.get("headers", [])}


# =====================================================================
# Instrumentation (le pipeline reçoit ces objets à la place des backends d'origine)
# =====================================================================
def instrumenter_gmail(base, etat, sur_reception=None):
    class GmailInstrumente(base):
        def brouillon(self, thread_id, a, sujet, corps, in_reply_to=""):
            did = super().brouillon(thread_id, a, sujet, corps, in_reply_to)
            self.__dict__.setdefault("_fils_brouillons", {})[did] = thread_id
            etat.etape(thread_id, "Brouillon créé dans Gmail (rien n'est encore envoyé)", "gmail")
            return did

        def envoyer(self, draft_id):
            tid = self.__dict__.get("_fils_brouillons", {}).get(draft_id) or etat.fil_courant
            resultat = super().envoyer(draft_id)
            etat.etape(tid, "Email envoyé au client", "succes")
            etat.noter("envoi", "Email envoyé au client", tid, "succes")
            return resultat

        def etiqueter(self, thread_id, etat_fil):
            super().etiqueter(thread_id, etat_fil)
            etat.maj_etat_fil(thread_id, etat_fil)
            etat.etape(thread_id, f"Libellé Gmail : {LIBELLES.get(etat_fil, etat_fil)}", "gmail")

        if hasattr(base, "recevoir"):  # mode démo : un email « reçu » apparaît instantanément
            def recevoir(self, de, sujet, corps, thread_id=None):
                tid = super().recevoir(de, sujet, corps, thread_id)
                m = self.threads[tid][-1]
                etat.ajouter_mail({"id": m["message_id"], "thread_id": tid, "de": de, "sujet": sujet,
                                   "corps": corps, "date": maintenant(), "etat": "a_traiter"}, nouveau=True)
                if sur_reception:
                    sur_reception()
                return tid

    GmailInstrumente.__name__ = f"{base.__name__}Instrumente"
    return GmailInstrumente


def instrumenter_agenda(base, etat):
    class AgendaInstrumente(base):
        def inserer(self, conseiller, event_id, body):
            r = super().inserer(conseiller, event_id, body)
            etat.etape(etat.fil_courant, f"Visite inscrite dans l'agenda de {conseiller}", "succes")
            etat.noter("visite", f"{body.get('summary', 'Visite')} inscrite chez {conseiller}", etat.fil_courant, "succes")
            etat.signal("agenda")
            return r

        def modifier(self, conseiller, event_id, body):
            r = super().modifier(conseiller, event_id, body)
            etat.noter("visite", f"Visite déplacée dans l'agenda de {conseiller}", etat.fil_courant, "info")
            etat.signal("agenda")
            return r

        def supprimer(self, conseiller, event_id):
            r = super().supprimer(conseiller, event_id)
            etat.noter("visite", f"Visite annulée dans l'agenda de {conseiller}", etat.fil_courant, "attention")
            etat.signal("agenda")
            return r

    AgendaInstrumente.__name__ = f"{base.__name__}Instrumente"
    return AgendaInstrumente


# =====================================================================
# Veille Gmail rapide
# =====================================================================
class VeilleGmail(threading.Thread):
    def __init__(self, backend, etat, sur_nouveau, intervalle: float = 2.0, nombre: int = 30):
        super().__init__(daemon=True, name="veille-gmail")
        self.b, self.etat, self.sur_nouveau = backend, etat, sur_nouveau
        self.intervalle, self.nombre = intervalle, nombre
        self.vus: set[str] = set()
        self.arret = threading.Event()
        self.etat_par_libelle = {v: k for k, v in backend.ids.items()}

    def lire(self, mid: str) -> dict:
        m = self.b.svc.messages().get(userId="me", id=mid, format="full").execute()
        h = _entetes(m["payload"])
        etat = next((self.etat_par_libelle[l] for l in m.get("labelIds", []) if l in self.etat_par_libelle), None)
        return {"id": mid, "thread_id": m["threadId"], "de": h.get("from", ""), "sujet": h.get("subject", ""),
                "corps": gmail_tools._texte(m["payload"])[:6000], "date": _iso_ms(m.get("internalDate", 0)),
                "etat": etat}

    def run(self):
        premier, erreurs = True, 0
        self.etat.maj_systeme(detection_s=self.intervalle)
        while not self.arret.is_set():
            try:
                # Requête très légère : uniquement les identifiants (5 unités de quota Gmail).
                r = self.b.svc.messages().list(userId="me", q="in:inbox -from:me", maxResults=self.nombre,
                                               fields="messages(id,threadId)").execute()
                nouveaux = [m["id"] for m in r.get("messages", []) if m["id"] not in self.vus]
                for mid in reversed(nouveaux):  # du plus ancien au plus récent
                    try:
                        self.etat.ajouter_mail(self.lire(mid), nouveau=not premier)
                        self.vus.add(mid)
                    except Exception as e:  # un message illisible ne bloque pas les autres
                        self.etat.noter("erreur", f"Email {mid} illisible : {e}", niveau="erreur")
                        self.vus.add(mid)
                if nouveaux and not premier:
                    self.sur_nouveau()  # l'agent démarre tout de suite, sans attendre la relève
                if erreurs or premier:
                    self.etat.maj_systeme(gmail={"ok": True, "texte": "connecté · détection 2 s"})
                self.etat.maj_systeme(publier=False, derniere_releve=maintenant())
                erreurs = 0
            except Exception as e:
                erreurs += 1
                self.etat.maj_systeme(gmail={"ok": False, "texte": f"erreur : {e}"[:180]})
            premier = False
            attente = self.intervalle if not erreurs else min(60, self.intervalle * 2 ** min(erreurs, 5))
            self.arret.wait(attente)


# =====================================================================
# Lectures pour les pages
# =====================================================================
class Lecteur:
    def __init__(self, gmail, agenda):
        self.g, self.a = gmail, agenda
        self.demo = isinstance(gmail, gmail_tools.FakeGmailBackend)
        self._v = threading.Lock()

    def fil(self, tid: str) -> list[dict]:
        with self._v:
            if self.demo:
                msgs = [{"id": m.get("message_id", f"{tid}-{i}"), "de": m["de"], "date": None, "corps": m["corps"],
                         "auteur": "client" if m.get("client") else "agence", "brouillon": False}
                        for i, m in enumerate(list(self.g.threads.get(tid, [])))]
                msgs += [{"id": did, "de": "Sama Kër Immobilier (brouillon)", "date": None, "corps": d["corps"],
                          "auteur": "agence", "brouillon": True}
                         for did, d in list(self.g.drafts.items()) if d["thread_id"] == tid]
                return msgs
            t = self.g.svc.threads().get(userId="me", id=tid, format="full").execute()
            out = []
            for m in t.get("messages", []):
                labels = set(m.get("labelIds", []))
                out.append({"id": m["id"], "de": _entetes(m["payload"]).get("from", ""),
                            "date": _iso_ms(m.get("internalDate", 0)),
                            "corps": gmail_tools._texte(m["payload"])[:6000],
                            "auteur": "agence" if labels & {"SENT", "DRAFT"} else "client",
                            "brouillon": "DRAFT" in labels})
            return out

    def etiqueter(self, tid: str, etat_fil: str):
        with self._v:
            self.g.etiqueter(tid, etat_fil)

    def evenements(self, debut: datetime, fin: datetime) -> list[dict]:
        out = []
        with self._v:
            if hasattr(self.a, "events"):  # agenda simulé (mode démo)
                for c, evs in self.a.events.items():
                    for eid, e in evs.items():
                        if e["end"] <= debut or e["start"] >= fin:
                            continue
                        out.append({"conseiller": c, "id": eid, "titre": e.get("summary", "Occupé"),
                                    "debut": e["start"].isoformat(), "fin": e["end"].isoformat(), "journee": False,
                                    "lieu": e.get("location", ""), "description": e.get("description", ""),
                                    "visite": eid.startswith("sk"), "lien": None})
                return out
            for nom, cid in self.a.cal.items():
                jeton = None
                while True:
                    r = self.a.svc.events().list(calendarId=cid, timeMin=debut.isoformat(), timeMax=fin.isoformat(),
                                                 singleEvents=True, orderBy="startTime", pageToken=jeton,
                                                 timeZone="Africa/Dakar", maxResults=250).execute()
                    for e in r.get("items", []):
                        if e.get("status") == "cancelled":
                            continue
                        s, f = e.get("start", {}), e.get("end", {})
                        out.append({"conseiller": nom, "id": e["id"], "titre": e.get("summary") or "(sans titre)",
                                    "debut": s.get("dateTime") or s.get("date"), "fin": f.get("dateTime") or f.get("date"),
                                    "journee": "dateTime" not in s, "lieu": e.get("location", ""),
                                    "description": e.get("description", ""), "visite": e["id"].startswith("sk"),
                                    "lien": e.get("htmlLink")})
                    jeton = r.get("nextPageToken")
                    if not jeton:
                        break
        return out
