"""Pré-filtre déterministe AVANT le LLM (défense en profondeur, Exercice 4.3 et 5.4).
Le LLM reste la 2e barrière (prompt système) ; ce filtre attrape les cas évidents
sans dépendre de la bonne volonté du modèle."""
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
