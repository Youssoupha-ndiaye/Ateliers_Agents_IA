"""Construction de l'agent : modèle Ollama + boucle ReAct LangGraph + outils.
- Catalogue : fonctions locales (Exercice 1) puis serveur MCP (Exercice 2, outils_catalogue_mcp).
- Agenda / Gmail : outils Python avec validation humaine intégrée.
"""
import sys
from pathlib import Path

from langchain_ollama import ChatOllama
from langgraph.prebuilt import create_react_agent

import config

SERVER = Path(__file__).resolve().parent.parent / "mcp_server" / "server.py"


def modele():
    # temperature 0 : réponses stables et reproductibles ; num_ctx 8192 : fil + résultats d'outils
    return ChatOllama(model=config.OLLAMA_MODEL, base_url=config.OLLAMA_BASE_URL,
                      temperature=0, num_ctx=8192)


async def outils_catalogue_mcp():
    """Client MCP : récupère rechercher_biens / detail_bien exposés par le serveur."""
    from langchain_mcp_adapters.client import MultiServerMCPClient
    if config.MCP_TRANSPORT == "http":
        conf = {"catalogue": {"transport": "streamable_http", "url": config.MCP_HTTP_URL}}
    else:
        conf = {"catalogue": {"transport": "stdio", "command": sys.executable,
                              "args": [str(SERVER)], "cwd": str(SERVER.parent)}}
    client = MultiServerMCPClient(conf)
    return await client.get_tools()


def construire_agent(outils):
    """Boucle ReAct : le modèle choisit un outil, LangGraph l'exécute, renvoie le résultat, etc.
    handle_tool_errors=True : une exception d'outil devient un message lisible par le modèle."""
    from langgraph.prebuilt import ToolNode
    return create_react_agent(modele(), ToolNode(outils, handle_tool_errors=True))


def run_config(callbacks):
    # MAX_STEPS : chaque étape = 1 appel LLM ou 1 exécution d'outil -> recursion_limit = 2*MAX_STEPS+1
    return {"recursion_limit": 2 * config.MAX_STEPS + 1, "callbacks": callbacks}
