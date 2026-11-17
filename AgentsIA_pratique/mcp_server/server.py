"""Serveur MCP du catalogue Sama Kër (Exercice 2). Documentation complète : mcp_server/README.md

Local (stdio) : python mcp_server/server.py
HTTP          : python mcp_server/server.py --http                    (http://127.0.0.1:8000/mcp)
Réseau local  : python mcp_server/server.py --http --host 0.0.0.0     (pour l'agent d'une autre équipe)
Inspecteur    : npx @modelcontextprotocol/inspector python mcp_server/server.py

Compatible SDK MCP Python 1.x (FastMCP) et 2.x (FastMCP renommé MCPServer).
"""
import sys
from pathlib import Path
from typing import Annotated, Literal, Optional

from pydantic import Field

sys.path.insert(0, str(Path(__file__).resolve().parent))
import catalogue  # noqa: E402

HOST = sys.argv[sys.argv.index("--host") + 1] if "--host" in sys.argv else "127.0.0.1"
PORT = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8000

try:  # SDK MCP 1.x
    from mcp.server.fastmcp import FastMCP
    mcp = FastMCP("sama-ker-catalogue", host=HOST, port=PORT)

    def lancer(http: bool):
        mcp.run(transport="streamable-http" if http else "stdio")
except ModuleNotFoundError:  # SDK MCP 2.x
    from mcp.server.mcpserver import MCPServer
    mcp = MCPServer("sama-ker-catalogue")

    def lancer(http: bool):
        if http:
            mcp.run(transport="streamable-http", host=HOST, port=PORT)
        else:
            mcp.run(transport="stdio")

AGENCE = """# Sama Kër Immobilier — fiche agence
- Adresse : Rue fictive 10, Mermoz, Dakar
- Horaires de visite : du lundi au samedi, 9 h – 18 h (heure de Dakar), visites de 45 minutes
- Zones couvertes : Mermoz, Sacré-Cœur, Almadies, Point E, Plateau, Ouakam, Liberté 6, Ngor, Yoff, Fann
- Conditions de location : 2 mois de caution + 1 mois d'avance, bail d'un an renouvelable,
  pièce d'identité et justificatif de revenus demandés. Frais d'agence : 1 mois de loyer.
- Hors périmètre de l'agent (transmis à un conseiller) : réclamations, négociation de loyer, litiges.
"""


@mcp.resource("agence://infos", name="infos_agence", mime_type="text/markdown",
              description="Horaires de visite, zones couvertes et conditions de location de l'agence.")
def infos_agence() -> str:
    return AGENCE


@mcp.tool()
def rechercher_biens(
    type_bien: Annotated[Optional[Literal["Studio", "F2", "F3", "F4", "F5", "Villa"]],
                         Field(description="Type de logement. F3 = 3 pièces.")] = None,
    quartier: Annotated[Optional[str], Field(description="Quartier de Dakar, ex. 'Mermoz'.")] = None,
    loyer_max: Annotated[Optional[int], Field(description="Loyer mensuel maximum en F CFA, ex. 400000.")] = None,
    pieces_min: Annotated[Optional[int], Field(description="Nombre minimum de pièces.")] = None,
    meuble: Annotated[Optional[bool], Field(description="True = meublé uniquement, False = non meublé.")] = None,
    limite: Annotated[int, Field(description="Nombre max de résultats (1-10).")] = 5,
) -> dict:
    """Cherche des biens DISPONIBLES dans le catalogue de l'agence selon les critères du client.
    Renvoie {'ok', 'nombre', 'biens', 'message'}. Si 'nombre' vaut 0, dire au client qu'aucun bien
    ne correspond : ne jamais inventer de bien. Si 'ok' est False, lire 'erreur' et corriger les arguments."""
    try:
        return catalogue.rechercher(type_bien, quartier, loyer_max, pieces_min, meuble, True, limite)
    except Exception as e:  # erreur exploitable par le modèle
        return {"ok": False, "erreur": f"Erreur interne du catalogue : {e}", "biens": []}


@mcp.tool()
def detail_bien(
    bien_id: Annotated[str, Field(description="Identifiant du bien, format SK-001.")],
) -> dict:
    """Renvoie la fiche complète d'un bien (adresse, loyer, pièces, disponibilité, description).
    À utiliser avant de proposer une visite, pour obtenir l'adresse exacte."""
    try:
        return catalogue.detail(bien_id)
    except Exception as e:
        return {"ok": False, "erreur": f"Erreur interne du catalogue : {e}"}


if __name__ == "__main__":
    lancer("--http" in sys.argv)
