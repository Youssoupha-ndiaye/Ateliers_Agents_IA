# Serveur MCP `sama-ker-catalogue` (Exercice 2)

Serveur MCP du catalogue de l'agence Sama Kër Immobilier : 30 biens fictifs en location à Dakar
(base SQLite `catalogue.db`). Il expose **2 outils** et **1 ressource**. Cette page suffit pour brancher
n'importe quel client MCP (votre agent, l'inspecteur MCP, Claude Desktop, l'agent d'une autre équipe).

## Lancer le serveur

```bash
pip install "mcp[cli]"                         # SDK MCP Python (versions 1.x et 2.x acceptées)
python mcp_server/create_catalogue.py          # une fois : crée mcp_server/catalogue.db
python mcp_server/server.py                    # transport stdio (local)
python mcp_server/server.py --http             # transport Streamable HTTP : http://127.0.0.1:8000/mcp
python mcp_server/server.py --http --host 0.0.0.0 --port 8000   # accessible depuis le réseau local
```

Pour une autre équipe sur le même Wi-Fi : lancer avec `--host 0.0.0.0`, puis lui donner l'URL
`http://<votre-adresse-IP>:8000/mcp` (adresse IP : `ipconfig` sous Windows, `ip a` ou `ifconfig` sous Linux/macOS).

## Outil `rechercher_biens`

Cherche les biens **disponibles** à la location, triés par loyer croissant.

| Argument | Type | Obligatoire | Description |
|---|---|---|---|
| `type_bien` | `"Studio"`, `"F2"`, `"F3"`, `"F4"`, `"F5"` ou `"Villa"` | non | type de logement (F3 = 3 pièces) |
| `quartier` | texte | non | quartier de Dakar ; accents et majuscules facultatifs (`sacre coeur` = `Sacré-Cœur`) |
| `loyer_max` | entier | non | loyer mensuel maximum en F CFA, ex. `400000` |
| `pieces_min` | entier | non | nombre minimum de pièces |
| `meuble` | booléen | non | `true` = meublé uniquement, `false` = non meublé |
| `limite` | entier de 1 à 10 | non (défaut 5) | nombre maximum de résultats |

Quartiers couverts : Almadies, Fann, Liberté 6, Mermoz, Ngor, Ouakam, Plateau, Point E, Sacré-Cœur, Yoff.

**Réponses possibles**

```jsonc
// Appel
{"name": "rechercher_biens", "arguments": {"type_bien": "F3", "quartier": "Mermoz", "loyer_max": 400000}}

// 1. Des biens correspondent
{"ok": true, "nombre": 2, "message": null, "biens": [
  {"id": "SK-001", "type": "F3", "quartier": "Mermoz", "loyer_fcfa": 360000, "pieces": 3,
   "adresse": "Villa n°12, rue MZ-45, Mermoz, Dakar", "disponible": 1, "meuble": 0,
   "description": "F3 lumineux, proche VDN."},
  {"id": "SK-011", "type": "F3", "quartier": "Mermoz", "loyer_fcfa": 395000, "pieces": 3,
   "adresse": "Immeuble Fadia, 3e étage, Mermoz, Dakar", "disponible": 1, "meuble": 1,
   "description": "F3 meublé, gardien 24h/24."}]}

// 2. Aucun bien ne correspond : ne rien inventer
{"ok": true, "nombre": 0, "biens": [], "message": "Aucun bien du catalogue ne correspond à ces critères."}

// 3. Argument invalide : le message dit comment corriger
{"ok": false, "biens": [], "erreur": "quartier 'Rufisque' non couvert. Quartiers couverts : Almadies, Fann, ..."}
```

## Outil `detail_bien`

Renvoie la fiche complète d'un bien : à appeler avant de proposer une visite, pour avoir l'adresse exacte.

| Argument | Type | Obligatoire | Description |
|---|---|---|---|
| `bien_id` | texte | oui | identifiant au format `SK-001` (majuscules facultatives) |

```jsonc
{"name": "detail_bien", "arguments": {"bien_id": "SK-011"}}
// -> {"ok": true, "bien": {"id": "SK-011", "type": "F3", "adresse": "Immeuble Fadia, 3e étage, Mermoz, Dakar", ...}}
// -> {"ok": false, "erreur": "Aucun bien avec l'identifiant 'SK-999'. Format attendu : SK-001."}
```

## Ressource `agence://infos`

Texte Markdown : horaires de visite (du lundi au samedi, 9 h-18 h, heure de Dakar, visites de 45 minutes),
zones couvertes, conditions de location (caution, avance, pièces demandées, frais d'agence) et liste des demandes
traitées par un humain (réclamations, négociation de loyer, litiges).

## Se brancher depuis un client

**Inspecteur MCP** (vérification demandée par l'exercice) :
```bash
npx @modelcontextprotocol/inspector python mcp_server/server.py
# -> Connect, onglet Tools, List Tools, choisir rechercher_biens, remplir les arguments, Run Tool
# HTTP : lancer `python mcp_server/server.py --http`, puis dans l'inspecteur :
#        Transport Type = Streamable HTTP, URL = http://127.0.0.1:8000/mcp, Connect
```

**Agent LangGraph / LangChain** :
```python
from langchain_mcp_adapters.client import MultiServerMCPClient
client = MultiServerMCPClient({"catalogue": {"transport": "streamable_http", "url": "http://<IP>:8000/mcp"}})
tools = await client.get_tools()          # rechercher_biens, detail_bien
```

**Claude Desktop ou tout client stdio** (`claude_desktop_config.json`) :
```json
{"mcpServers": {"sama-ker-catalogue": {"command": "python", "args": ["/chemin/absolu/mcp_server/server.py"]}}}
```

## Conventions

- Les outils ne lèvent jamais d'exception vers le client : toute erreur est un objet `{"ok": false, "erreur": "..."}`
  dont le texte indique comment corriger l'appel.
- Le catalogue est en **lecture seule** : aucun outil ne modifie la base.
- La base est **déterministe** (graine fixe) : tout le monde obtient les mêmes 30 biens.
