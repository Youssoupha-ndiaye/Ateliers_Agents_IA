"""Logique métier du catalogue, indépendante du transport (testable seule)."""
import sqlite3
import unicodedata
from pathlib import Path

DB = Path(__file__).parent / "catalogue.db"
TYPES_VALIDES = {"studio", "f2", "f3", "f4", "f5", "villa"}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return s.lower().replace("-", " ").strip()


def _con():
    if not DB.exists():
        raise FileNotFoundError("catalogue.db absent : lancez mcp_server/create_catalogue.py")
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def quartiers() -> list[str]:
    with _con() as c:
        return [r[0] for r in c.execute("SELECT DISTINCT quartier FROM biens ORDER BY 1")]


def rechercher(type_bien=None, quartier=None, loyer_max=None, pieces_min=None,
               meuble=None, seulement_disponibles=True, limite=5) -> dict:
    erreurs = []
    if type_bien and _norm(type_bien) not in TYPES_VALIDES:
        erreurs.append(f"type_bien '{type_bien}' inconnu. Valeurs : Studio, F2, F3, F4, F5, Villa.")
    q_connus = {_norm(q): q for q in quartiers()}
    if quartier and _norm(quartier) not in q_connus:
        erreurs.append(f"quartier '{quartier}' non couvert. Quartiers couverts : {', '.join(q_connus.values())}.")
    if loyer_max is not None and loyer_max <= 0:
        erreurs.append("loyer_max doit être un entier positif en F CFA.")
    if erreurs:
        return {"ok": False, "erreur": " ".join(erreurs), "biens": []}

    sql, args = "SELECT * FROM biens WHERE 1=1", []
    if type_bien:
        sql += " AND lower(type)=?"; args.append(_norm(type_bien))
    if quartier:
        sql += " AND quartier=?"; args.append(q_connus[_norm(quartier)])
    if loyer_max is not None:
        sql += " AND loyer_fcfa<=?"; args.append(int(loyer_max))
    if pieces_min is not None:
        sql += " AND pieces>=?"; args.append(int(pieces_min))
    if meuble is not None:
        sql += " AND meuble=?"; args.append(int(bool(meuble)))
    if seulement_disponibles:
        sql += " AND disponible=1"
    sql += " ORDER BY loyer_fcfa LIMIT ?"; args.append(max(1, min(int(limite), 10)))
    with _con() as c:
        biens = [dict(r) for r in c.execute(sql, args)]
    return {"ok": True, "nombre": len(biens), "biens": biens,
            "message": None if biens else "Aucun bien du catalogue ne correspond à ces critères."}


def detail(bien_id: str) -> dict:
    with _con() as c:
        r = c.execute("SELECT * FROM biens WHERE upper(id)=upper(?)", (bien_id.strip(),)).fetchone()
    if not r:
        return {"ok": False, "erreur": f"Aucun bien avec l'identifiant '{bien_id}'. Format attendu : SK-001."}
    return {"ok": True, "bien": dict(r)}
