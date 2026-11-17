"""Configuration centralisée (lue depuis .env, jamais committé)."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
MAX_STEPS = int(os.getenv("MAX_STEPS", "12"))
MCP_TRANSPORT = os.getenv("MCP_TRANSPORT", "stdio")
MCP_HTTP_URL = os.getenv("MCP_HTTP_URL", "http://127.0.0.1:8000/mcp")


TRACES_DIR = ROOT / "traces"


