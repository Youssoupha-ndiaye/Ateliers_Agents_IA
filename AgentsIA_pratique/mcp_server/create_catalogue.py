"""Crée la base SQLite du catalogue : 30 biens fictifs (Exercice 1.1).
Usage : python mcp_server/create_catalogue.py
Déterministe (seed fixe) pour que l'évaluation soit reproductible."""
import random
import sqlite3
from pathlib import Path

DB = Path(__file__).parent / "catalogue.db"

QUARTIERS = {  # quartier : (loyer de base F3 en F CFA, rues fictives)
    "Mermoz": 380_000, "Sacré-Cœur": 330_000, "Almadies": 750_000,
    "Point E": 420_000, "Plateau": 500_000, "Ouakam": 300_000,
    "Liberté 6": 250_000, "Ngor": 600_000, "Yoff": 280_000, "Fann": 450_000,
}
TYPES = [("Studio", 1), ("F2", 2), ("F3", 3), ("F4", 4), ("F5", 5), ("Villa", 6)]
COEF = {1: 0.45, 2: 0.7, 3: 1.0, 4: 1.35, 5: 1.7, 6: 2.6}

SCHEMA = """
CREATE TABLE biens (
  id            TEXT PRIMARY KEY,          -- ex. SK-001
  type          TEXT NOT NULL,             -- Studio, F2..F5, Villa
  quartier      TEXT NOT NULL,
  loyer_fcfa    INTEGER NOT NULL CHECK (loyer_fcfa > 0),
  pieces        INTEGER NOT NULL CHECK (pieces BETWEEN 1 AND 10),
  adresse       TEXT NOT NULL,
  disponible    INTEGER NOT NULL DEFAULT 1, -- 1 = libre à la location
  meuble        INTEGER NOT NULL DEFAULT 0,
  description   TEXT
);
CREATE INDEX idx_quartier ON biens(quartier);
CREATE INDEX idx_loyer ON biens(loyer_fcfa);
"""


def generer():
    rnd = random.Random(2026)
    quartiers = list(QUARTIERS)
    biens = []
    for i in range(30):
        q = quartiers[i % len(quartiers)]
        t, p = TYPES[rnd.randrange(len(TYPES))]
        loyer = int(round(QUARTIERS[q] * COEF[p] * rnd.uniform(0.85, 1.15), -4))
        meuble = int(rnd.random() < 0.3)
        biens.append((
            f"SK-{i+1:03d}", t, q, loyer, p,
            f"Villa n°{rnd.randint(1, 250)}, rue {rnd.randint(1, 40)}, {q}, Dakar",
            int(rnd.random() < 0.8), meuble,
            f"{t} {'meublé ' if meuble else ''}de {p} pièce(s) à {q}, "
            f"{rnd.choice(['proche commerces', 'calme', 'vue mer', 'avec parking', 'rénové'])}.",
        ))
    # Garantit quelques biens utiles aux tests (F3 Mermoz < 400 000 F, disponible)
    biens[0] = ("SK-001", "F3", "Mermoz", 360_000, 3,
                "Villa n°12, rue MZ-45, Mermoz, Dakar", 1, 0, "F3 lumineux, proche VDN.")
    biens[10] = ("SK-011", "F3", "Mermoz", 395_000, 3,
                 "Immeuble Fadia, 3e étage, Mermoz, Dakar", 1, 1, "F3 meublé, gardien 24h/24.")
    return biens


def main():
    DB.unlink(missing_ok=True)
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)
    con.executemany("INSERT INTO biens VALUES (?,?,?,?,?,?,?,?,?)", generer())
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM biens").fetchone()[0]
    print(f"Catalogue créé : {DB} ({n} biens)")


if __name__ == "__main__":
    main()
