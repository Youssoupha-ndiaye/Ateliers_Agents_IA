"""Exercice 1 : agent avec les outils catalogue en fonctions LOCALES (avant le MCP).
Usage : python agent/ex1_outils_locaux.py            (10 demandes de test)
        python agent/ex1_outils_locaux.py "un F3 à Mermoz à moins de 400 000 F"
"""
import asyncio
import sys
from pathlib import Path
from typing import Literal, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp_server"))
import catalogue  # noqa: E402
import tracing  # noqa: E402
from agent import construire_agent, run_config  # noqa: E402

from demandes_ex1 import DEMANDES, SYS  # noqa: E402


@tool
def rechercher_biens(type_bien: Optional[Literal["Studio", "F2", "F3", "F4", "F5", "Villa"]] = None,
                     quartier: Optional[str] = None, loyer_max: Optional[int] = None,
                     pieces_min: Optional[int] = None, meuble: Optional[bool] = None, limite: int = 5) -> dict:
    """Cherche des biens disponibles du catalogue. loyer_max en F CFA (ex. 400000). F3 = 3 pièces.
    Si 'nombre' vaut 0 : aucun bien ne correspond, ne rien inventer. Si ok=False : lire 'erreur'."""
    return catalogue.rechercher(type_bien, quartier, loyer_max, pieces_min, meuble, True, limite)


@tool
def detail_bien(bien_id: str) -> dict:
    """Fiche complète d'un bien à partir de son identifiant (format SK-001)."""
    return catalogue.detail(bien_id)


async def main():
    demandes = sys.argv[1:] or DEMANDES
    agent = construire_agent([rechercher_biens, detail_bien])
    tracing.demarrer("ex1")
    for d in demandes:
        tracing.log("demande", texte=d)
        r = await agent.ainvoke({"messages": [SystemMessage(SYS), HumanMessage(d)]},
                                run_config([tracing.TraceHandler()]))
        print(f"\n>>> {d}\n{r['messages'][-1].content}")


if __name__ == "__main__":
    asyncio.run(main())
