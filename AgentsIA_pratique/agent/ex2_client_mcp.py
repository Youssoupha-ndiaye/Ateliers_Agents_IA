"""Exercice 2 : mêmes 10 demandes, mais les outils viennent du serveur MCP
(fonctions locales supprimées). Usage : python agent/ex2_client_mcp.py  [MCP_TRANSPORT=http]"""
import asyncio

from langchain_core.messages import HumanMessage, SystemMessage

import tracing
from agent import construire_agent, outils_catalogue_mcp, run_config
from demandes_ex1 import DEMANDES, SYS


async def main():
    outils = await outils_catalogue_mcp()
    print("Outils MCP :", [t.name for t in outils])
    agent = construire_agent(outils)
    tracing.demarrer("ex2")
    for d in DEMANDES:
        tracing.log("demande", texte=d)
        r = await agent.ainvoke({"messages": [SystemMessage(SYS), HumanMessage(d)]}, run_config([tracing.TraceHandler()]))
        print(f"\n>>> {d}\n{r['messages'][-1].content}")


if __name__ == "__main__":
    asyncio.run(main())
