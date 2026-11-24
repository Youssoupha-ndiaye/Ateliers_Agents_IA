"""Approbateur web : remplace la validation en console (ou Gradio) par la page « Validations ».

Le pipeline appelle validation.demander(action, détails) et attend. Ici, la demande est publiée
instantanément dans l'interface, avec un résumé lisible ; le thread de l'agent reste bloqué
jusqu'au clic du conseiller (Valider / Refuser). Rien n'est envoyé ni inscrit sans ce clic.
"""
import re
import threading

SK = re.compile(r"\bSK-\d{3}\b", re.IGNORECASE)
JOURS = "lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche"
HEURE = re.compile(r"\b\d{1,2}\s?[h:]\s?\d{2}\b")
CRENEAU = re.compile(rf"\b(?:{JOURS})\b[^\n]{{0,45}}?\b\d{{1,2}}\s*(?:h|:)\s*\d{{0,2}}", re.IGNORECASE)
JOURS_EN = {"Monday": "lundi", "Tuesday": "mardi", "Wednesday": "mercredi", "Thursday": "jeudi",
            "Friday": "vendredi", "Saturday": "samedi", "Sunday": "dimanche"}

TITRES = {
    "reponse": "Réponse au client",
    "visite": "Visite + email de confirmation",
    "deplacement": "Déplacement d'une visite",
    "annulation": "Annulation d'une visite",
    "autre": "Action de l'agent",
}


def _fr(texte):
    texte = str(texte or "")
    for en, fr in JOURS_EN.items():
        texte = texte.replace(en, fr)
    return texte


def classer_action(action: str):
    a = (action or "").lower()
    if "visite" in a and ("créer" in a or "creer" in a):
        return "visite", 2
    if "envoyer" in a:
        return "reponse", 1
    if "déplacer" in a or "deplacer" in a:
        return "deplacement", None
    if "annuler" in a:
        return "annulation", None
    return "autre", None


def resumer(type_: str, d: dict) -> list[dict]:
    lignes = []
    if type_ == "reponse":
        corps = d.get("corps", "")
        biens = sorted({b.upper() for b in SK.findall(corps)})
        creneaux = [" ".join(c.split()) for c in CRENEAU.findall(corps)][:4] \
            or sorted(set(HEURE.findall(corps)))[:4]
        lignes = [("Destinataire", d.get("a")), ("Objet", d.get("sujet")),
                  ("Biens proposés", ", ".join(biens) or "aucun"),
                  ("Créneaux proposés", " · ".join(creneaux) or "aucun"),
                  ("Pose une question au client", "oui" if "?" in corps else "non"),
                  ("Longueur", f"{len(corps.split())} mots")]
    elif type_ == "visite":
        lignes = [("Date", f"{_fr(d.get('debut'))} → {d.get('fin', '')}"), ("Conseiller", d.get("conseiller")),
                  ("Bien", d.get("bien")), ("Adresse", d.get("adresse")), ("Client", d.get("client")),
                  ("Confirmation envoyée à", d.get("email_de_confirmation_a"))]
    else:
        lignes = [(k.replace("_", " ").capitalize(), _fr(v)) for k, v in d.items()
                  if isinstance(v, (str, int, float)) and len(str(v)) < 200]
    return [{"label": a, "valeur": str(b)} for a, b in lignes if b not in (None, "")]


class ApprobateurWeb:
    def __init__(self, etat):
        self.etat = etat
        self._attentes: dict[str, tuple[threading.Event, dict]] = {}
        self._v = threading.Lock()

    def __call__(self, action: str, details: dict):
        tid = self.etat.fil_courant
        type_, numero = classer_action(action)
        with self.etat._v:
            du_fil = [m for m in self.etat.mails.values() if m["thread_id"] == tid] if tid else []
        mail = max(du_fil, key=lambda m: m.get("date") or "", default=None)
        v = self.etat.nouvelle_validation(
            action=action, type=type_, numero=numero, titre=TITRES[type_], thread_id=tid,
            client=(mail or {}).get("nom") or details.get("client") or details.get("a") or "",
            sujet=(mail or {}).get("sujet") or details.get("sujet") or "",
            resume=resumer(type_, details),
            message_client=details.get("message_recu") or "",
            texte_ia=details.get("corps") or details.get("email_de_confirmation") or "",
            details={k: _fr(v) for k, v in details.items()},
        )
        self.etat.etape(tid, f"En attente du conseiller : {TITRES[type_]}", "attente")
        evenement, boite = threading.Event(), {}
        with self._v:
            self._attentes[v["id"]] = (evenement, boite)
        evenement.wait()  # le thread de l'agent attend ici le clic du conseiller
        return boite["accord"], boite["motif"]

    def decider(self, vid: str, accord: bool, motif: str = "") -> bool:
        with self._v:
            attente = self._attentes.pop(vid, None)
        if attente is None:
            return False
        motif = (motif or "").strip() or ("validé par le conseiller" if accord else "refusé par le conseiller")
        self.etat.decision_validation(vid, accord, motif)
        attente[1].update(accord=accord, motif=motif)
        attente[0].set()
        return True
