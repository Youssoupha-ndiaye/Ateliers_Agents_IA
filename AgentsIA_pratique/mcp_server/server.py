"""Serveur MCP du catalogue Sama Kër (Exercice 2). Documentation complète : mcp_server/README.md

Local (stdio) : python mcp_server/server.py
HTTP          : python mcp_server/server.py --http                    (http://127.0.0.1:8000/mcp)
Réseau local  : python mcp_server/server.py --reseau                  (partage : affiche l'URL à donner)
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

# ═══════════════════════════════════════════════════════════════════════════════════════════════
# MODE RÉSEAU : partager ce serveur MCP avec d'autres machines (même Wi-Fi / même réseau local)
# ═══════════════════════════════════════════════════════════════════════════════════════════════
# Commande :  python mcp_server/server.py --reseau              (port 8000)
#             python mcp_server/server.py --reseau --port 9000  (autre port)
#
# Comment ça marche, en 3 points :
#
# 1. TRANSPORT HTTP au lieu de stdio.
#    En stdio, le serveur est un sous-processus lancé PAR l'agent, sur la MÊME machine : il parle
#    par l'entrée et la sortie standard, donc personne d'autre ne peut le joindre. En HTTP
#    ("streamable-http"), le serveur tourne tout seul et répond aux requêtes sur l'URL /mcp.
#    N'importe quel client MCP qui connaît l'adresse peut s'y connecter.
#
# 2. ÉCOUTE SUR 0.0.0.0 au lieu de 127.0.0.1.
#    127.0.0.1 (localhost) n'accepte que les connexions venant de CE PC. 0.0.0.0 signifie
#    « toutes les cartes réseau ». Le serveur accepte alors aussi les connexions qui arrivent par
#    le Wi-Fi ou l'Ethernet, c'est-à-dire celles des autres PC.
#
# 3. AFFICHAGE DE L'ADRESSE À PARTAGER.
#    0.0.0.0 n'est pas une adresse qu'on peut donner à quelqu'un. La fonction ip_locale() trouve
#    l'IP de ce PC sur le réseau local (ex. 192.168.1.23) et le serveur affiche l'URL exacte à
#    transmettre : http://192.168.1.23:8000/mcp
#
# Côté de l'autre équipe : elle met cette URL dans son client MCP, par exemple
#     MultiServerMCPClient({"catalogue": {"transport": "streamable_http",
#                                         "url": "http://192.168.1.23:8000/mcp"}})
#
# À savoir :
#  - Pare-feu Windows : au premier lancement, Windows demande d'autoriser Python. Cochez
#    « Réseaux privés ». Sinon les autres PC ne pourront pas joindre le serveur.
#  - Les deux machines doivent être sur le même réseau. Sur Internet, il faudrait un tunnel
#    (ngrok, cloudflared…) devant le port 8000.
#  - Il n'y a pas d'authentification : toute personne sur le réseau peut interroger le catalogue.
#    Les outils sont en lecture seule (rechercher / détailler des biens), donc le risque est faible.
#    Évitez quand même ce mode sur un Wi-Fi public.
# ═══════════════════════════════════════════════════════════════════════════════════════════════
RESEAU = "--reseau" in sys.argv
if RESEAU:
    HOST = "0.0.0.0"  # point 2 : écouter sur toutes les cartes réseau, pas seulement localhost


def ip_locale() -> str:
    """Point 3 : retrouve l'IP de ce PC sur le réseau local (celle à donner aux autres).

    Astuce : on « connecte » une socket UDP vers une adresse externe. Avec UDP, aucun paquet
    n'est envoyé. Le système choisit seulement la carte réseau qu'il utiliserait pour y aller,
    et getsockname() renvoie l'IP de cette carte.
    """
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:  # pas de réseau : on retombe sur le nom de la machine
        return socket.gethostbyname(socket.gethostname())
    finally:
        s.close()

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
    if RESEAU:
        # Point 1 : le mode réseau impose HTTP (le stdio ne sort pas de la machine).
        # Les messages vont sur stderr pour ne jamais se mélanger au protocole MCP.
        print("\n  Serveur MCP Sama Kër : MODE RÉSEAU", file=sys.stderr)
        print(f"  Sur ce PC          : http://127.0.0.1:{PORT}/mcp", file=sys.stderr)
        print(f"  URL à partager     : http://{ip_locale()}:{PORT}/mcp", file=sys.stderr)
        print("  (autorisez Python dans le pare-feu Windows si on vous le demande)\n", file=sys.stderr)
    lancer("--http" in sys.argv or RESEAU)
