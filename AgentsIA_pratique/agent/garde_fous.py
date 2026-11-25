"""Pré-filtre déterministe AVANT le LLM (défense en profondeur, Exercice 4.3 et 5.4).
Le LLM reste la 2e barrière (prompt système) ; ce filtre attrape les cas évidents
sans dépendre de la bonne volonté du modèle.
- est_automatique : notifications, newsletters, bounces -> ignorés, jamais de réponse ;
- classer : injection / hors périmètre -> transmis à un conseiller."""
import re
import unicodedata


def _n(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


INJECTION = [r"ignore[rz]? (tes|vos|les|toutes|ses) (consignes|instructions|regles)", r"ignore (previous|all|your) instructions",
             r"system prompt", r"prompt systeme", r"liste (des|de tous les|de vos) clients", r"tu es (maintenant|desormais)",
             r"oublie (tes|tout)", r"mode developpeur", r"jailbreak", r"base de donnees (des|de vos) clients",
             r"envoie[- ]moi (la liste|les emails|les adresses|les donnees)",
             r"sans (aucune )?validation", r"note interne", r"coordonnees des (derniers |autres )?clients"]
HORS_PERIMETRE = {
    "reclamation": [r"reclamation", r"\bplainte\b", r"inadmissible", r"scandale", r"remboursement", r"\bfuite\b", r"en panne", r"pas repare"],
    "negociation": [r"baisser? (le )?loyer", r"reduction", r"negoci", r"rabais", r"moins cher possible\?", r"diminuer le prix", r"ristourne"],
    "litige": [r"litige", r"avocat", r"tribunal", r"\bhuissier\b", r"mise en demeure", r"caution (non|pas) (rendue|restituee)", r"expulsion"],
}


# Emails automatiques (notifications, newsletters, retours de livraison) : aucun humain ne lira la réponse.
EXPEDITEUR_AUTO = re.compile(
    r"no[-_.]?reply|do[-_.]?not[-_.]?reply|ne[-_.]?pas[-_.]?repondre|mailer[-_.]?daemon|postmaster"
    r"|^(bounces?|notifications?|notify|newsletters?|news|alerts?|marketing|automated?)([-+_.]|$)")


def adresse_email(de: str) -> str:
    """'Google <noreply@google.com>' -> 'noreply@google.com' (en minuscules)."""
    m = re.search(r"<([^>]+)>", de or "")
    return (m[1] if m else de or "").strip().lower()


def est_automatique(de: str, entetes: dict | None = None) -> str | None:
    """Renvoie le motif si l'email vient d'un robot (expéditeur ou en-têtes standard), sinon None."""
    local = adresse_email(de).split("@")[0]
    if local and EXPEDITEUR_AUTO.search(local):
        return f"expéditeur automatique ({adresse_email(de)})"
    h = {k.lower(): (v or "").strip().lower() for k, v in (entetes or {}).items()}
    if h.get("auto-submitted", "no") != "no":                         # RFC 3834 : réponse/notification auto
        return f"en-tête Auto-Submitted: {h['auto-submitted']}"
    if h.get("precedence") in ("bulk", "list", "junk", "auto_reply"):
        return f"en-tête Precedence: {h['precedence']}"
    if "list-unsubscribe" in h or "list-id" in h:                     # newsletter / liste de diffusion
        return "liste de diffusion (List-Unsubscribe / List-Id)"
    if "x-autoreply" in h or "x-autorespond" in h:
        return "réponse automatique (X-Autoreply)"
    if h.get("return-path") == "<>":                                  # retour de livraison (bounce)
        return "retour de livraison (Return-Path vide)"
    return None


def classer(texte: str) -> tuple[str, str | None]:
    """Renvoie ('injection'|'hors_perimetre'|'ok', motif)."""
    t = _n(texte)
    if any(re.search(p, t) for p in INJECTION):
        return "injection", "tentative de manipulation de l'agent"
    for motif, pats in HORS_PERIMETRE.items():
        if any(re.search(p, t) for p in pats):
            return "hors_perimetre", motif
    return "ok", None


def encapsuler(corps: str) -> str:
    """Le contenu de l'email est balisé comme donnée non fiable."""
    corps = corps.replace("<email_client>", "").replace("</email_client>", "")
    return f"<email_client>\n{corps}\n</email_client>"
