"""Configuration centralisée (lue depuis .env, jamais committé)."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
MAX_STEPS = int(os.getenv("MAX_STEPS", "12"))
RELEVE_SECONDES = int(os.getenv("RELEVE_SECONDES", "30"))  # intervalle de relève Gmail du pipeline
MCP_TRANSPORT = os.getenv("MCP_TRANSPORT", "stdio")
MCP_HTTP_URL = os.getenv("MCP_HTTP_URL", "http://127.0.0.1:8000/mcp")
GOOGLE_CLIENT_SECRET = ROOT / os.getenv("GOOGLE_CLIENT_SECRET", "secrets/client_secret.json")
GOOGLE_TOKEN = ROOT / os.getenv("GOOGLE_TOKEN", "secrets/token.json")
AGENCY_EMAIL = os.getenv("AGENCY_EMAIL", "")
TZ = "Africa/Dakar"
# Correction de l'exercice 6 (pré-filtre déterministe injection / hors périmètre)
CORRECTION = os.getenv("SAMAKER_CORRECTION", "1") == "1"
STATE_DIR = ROOT / "state"
TRACES_DIR = ROOT / "traces"


def conseillers() -> dict[str, str]:
    """CONSEILLERS=Awa=<calendarId>,Moussa=<calendarId>,... (créé par agent/setup_calendars.py)."""
    raw = os.getenv("CONSEILLERS", "")
    res = dict(p.strip().split("=", 1) for p in raw.split(",") if "=" in p)
    if not res or any("xxx" in v or "yyy" in v or "zzz" in v for v in res.values()):
        raise RuntimeError("CONSEILLERS non configuré dans .env : lancez `python agent/setup_calendars.py` "
                           "et collez la ligne affichée dans .env.")
    return res


# Scopes OAuth de l'AGENT : strict nécessaire demandé par l'atelier
# (lecture des messages, brouillons, envoi, gestion des événements de l'agenda).
SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",    # lire les messages + poser les libellés d'état (pas de suppression définitive)
    "https://www.googleapis.com/auth/gmail.compose",   # créer des brouillons et les envoyer
    "https://www.googleapis.com/auth/calendar.events",  # lire / créer / déplacer / annuler des événements
]
# Scope utilisé UNE SEULE FOIS par setup_calendars.py pour créer les 3 agendas (jamais par l'agent).
SCOPE_CREATION_AGENDAS = ["https://www.googleapis.com/auth/calendar"]
