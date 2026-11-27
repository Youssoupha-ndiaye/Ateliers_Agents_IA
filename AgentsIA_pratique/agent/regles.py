"""Règles de l'agence et calcul des créneaux — logique pure, sans réseau (testable)."""
import re
import unicodedata
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Africa/Dakar")
DUREE = timedelta(minutes=45)
OUVERTURE, FERMETURE = time(9, 0), time(18, 0)
PAS = timedelta(minutes=15)  # granularité de recherche
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOMENTS = {  # bornes horaires d'un moment de la journée
    "matin": (time(9), time(12)),
    "midi": (time(12), time(14)),
    "apres-midi": (time(14), time(18)),
    "debut d'apres-midi": (time(14), time(16)),
    "fin d'apres-midi": (time(16), time(18)),
    "journee": (time(9), time(18)),
}
ALIAS_MOMENTS = {  # formulations fréquentes (y compris wolof courant) -> moment
    "soir": "fin d'apres-midi", "fin de journee": "fin d'apres-midi", "en fin d'apres-midi": "fin d'apres-midi",
    "suba": "matin", "ci suba": "matin", "ngoon": "apres-midi", "ci ngoon": "apres-midi",
    "toute la journee": "journee", "n'importe quand": "journee", "": "journee",
}


def _n(s):
    s = (s or "").replace("\u2019", "'").replace("`", "'")
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower().strip()


def resoudre_date(expr: str, aujourd_hui: date | None = None) -> date:
    """'demain', 'jeudi prochain', 'jeudi', '21/11', '2026-11-21' -> date.
    'jeudi prochain' = le jeudi de la semaine suivante si on est déjà en début de semaine
    convention de l'agence : le PROCHAIN jeudi strictement après aujourd'hui."""
    d0 = aujourd_hui or datetime.now(TZ).date()
    e = _n(expr)
    if e in ("aujourd'hui", "aujourdhui", "ce jour"):
        return d0
    if e in ("demain", "elleg"):
        return d0 + timedelta(days=1)
    if e in ("apres-demain", "apres demain"):
        return d0 + timedelta(days=2)
    if m := re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", e):
        return date(*map(int, m.groups()))
    if m := re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{4}))?", e):
        j, mo, a = int(m[1]), int(m[2]), int(m[3] or d0.year)
        d = date(a, mo, j)
        return d if d >= d0 or m[3] else date(a + 1, mo, j)
    for i, nom in enumerate(JOURS):
        if e.startswith(nom):
            delta = (i - d0.weekday()) % 7 or 7
            return d0 + timedelta(days=delta)
    raise ValueError(f"Date non comprise : '{expr}'. Utiliser 'jeudi prochain', 'demain', 'JJ/MM' ou 'AAAA-MM-JJ'.")


def plage_moment(moment: str) -> tuple[time, time]:
    m = " ".join(_n(moment).replace("_", " ").split())
    m = m.replace("apres midi", "apres-midi").replace("fin apres-midi", "fin d'apres-midi").replace("d' apres", "d'apres")
    m = ALIAS_MOMENTS.get(m, m)
    if m not in MOMENTS:
        raise ValueError(f"moment '{moment}' inconnu. Valeurs : {', '.join(MOMENTS)}.")
    return MOMENTS[m]


def est_dans_horaires(debut: datetime) -> bool:
    debut = debut.astimezone(TZ)
    fin = debut + DUREE
    return debut.weekday() < 6 and debut.time() >= OUVERTURE and fin.time() <= FERMETURE and fin.date() == debut.date()


def chevauche(a0, a1, b0, b1) -> bool:
    return a0 < b1 and b0 < a1


def creneaux_libres(jour: date, moment: str, occupations: dict[str, list[tuple[datetime, datetime]]],
                    nombre: int = 3, maintenant: datetime | None = None) -> list[dict]:
    """Renvoie les créneaux libres les plus tôt, en équilibrant la charge du jour.

    occupations = {conseiller: [(debut, fin), ...]}.
    """
    if jour.weekday() == 6:
        return []
    h0, h1 = plage_moment(moment)
    now = maintenant or datetime.now(TZ)
    t = datetime.combine(jour, max(h0, OUVERTURE), TZ)
    fin_plage = datetime.combine(jour, min(h1, FERMETURE), TZ)
    res: list[dict] = []
    # La liste d'occupations couvre la journée : elle sert de mesure simple de la charge.
    # Les créneaux proposés dans cet appel sont ajoutés aussi, pour ne pas proposer les 3
    # options au même conseiller quand plusieurs conseillers sont également disponibles.
    charge = {c: len(occ) for c, occ in occupations.items()}
    derniere_visite = {
        c: max((debut for debut, _ in occ), default=None)
        for c, occ in occupations.items()
    }
    ordre = {c: i for i, c in enumerate(occupations)}
    while t + DUREE <= fin_plage and len(res) < nombre:
        espace = all(abs((t - datetime.fromisoformat(r["debut"])).total_seconds()) >= 1800 for r in res)
        if t > now and espace:
            disponibles = []
            for c, occ in occupations.items():
                deja = [(datetime.fromisoformat(r["debut"]), datetime.fromisoformat(r["fin"]))
                        for r in res if r["conseiller"] == c]
                if not any(chevauche(t, t + DUREE, o0, o1) for o0, o1 in occ + deja):
                    disponibles.append(c)
            if disponibles:
                # L'heure la plus tôt reste prioritaire. À heure égale, choisir le moins
                # chargé; en cas d'égalité, privilégier la visite la plus ancienne, puis
                # l'ordre configuré pour que le résultat reste déterministe.
                c = min(disponibles, key=lambda nom: (
                    charge[nom],
                    derniere_visite[nom] or datetime.min.replace(tzinfo=TZ),
                    ordre[nom],
                ))
                res.append({"conseiller": c, "debut": t.isoformat(), "fin": (t + DUREE).isoformat()})
                charge[c] += 1
                derniere_visite[c] = t
        t += PAS
    return res
