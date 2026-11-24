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
python mcp_server/server.py reseau             # partage réseau : affiche l'URL à transmettre
```

`reseau` (ou son alias `--reseau`) démarre le serveur en HTTP sur `0.0.0.0`. Pour une autre équipe
sur le même Wi-Fi, donne-lui l'URL affichée par le serveur :
`http://<votre-adresse-IP>:8000/mcp` (adresse IP : `ipconfig` sous Windows, `ip a` ou `ifconfig` sous Linux/macOS).

## Connexion de l'agent de l'atelier à un autre MCP

Dans ce scénario, l'autre machine héberge le serveur MCP et l'agent de cet atelier est le client.
Demande à l'équipe qui héberge le serveur son URL **Streamable HTTP** complète, par exemple
`http://192.168.1.23:8000/mcp`. L'adresse doit être celle du serveur sur le réseau, pas `0.0.0.0`.

1. Vérifie que le serveur distant est démarré, que son transport est Streamable HTTP et que son URL
   est joignable depuis ton PC. Les deux machines doivent être sur le même réseau local, sauf si un
   accès distant sécurisé a été mis en place.
2. Depuis le dossier `AgentsIA_pratique`, modifie le bloc MCP de `.env` :

   ```dotenv
   MCP_TRANSPORT=http
   MCP_HTTP_URL=http://192.168.1.23:8000/mcp
   ```

   Remplace l'exemple par l'URL fournie par l'équipe distante. Dans ce projet, `config.py` lit ce
   fichier et le client MCP utilise l'URL quand `MCP_TRANSPORT=http`. Avec `stdio`, il lance à la
   place le serveur de catalogue local : ce mode ne sert pas à joindre un serveur sur un autre PC.
3. Vérifie la découverte des outils en lançant, depuis `AgentsIA_pratique` :

   ```powershell
   python .\agent\ex2_client_mcp.py
   ```

   La ligne `Outils MCP : [...]` indique les outils que le serveur a bien exposés au client.
   L'exercice lance ensuite le modèle Ollama et les demandes de démonstration; il faut donc aussi
   qu'Ollama soit disponible pour exécuter tout l'exercice.
4. L'agent de l'exercice 2 a des consignes orientées vers le catalogue immobilier. Il peut découvrir
   les outils d'un autre MCP, mais pour les utiliser correctement, les demandes et les consignes
   doivent correspondre à ces outils.

Le client actuel attend un MCP Streamable HTTP sans authentification configurée. Si le serveur
distant exige une clé, OAuth ou un autre mécanisme d'authentification, cette configuration seule
ne suffira pas.

## Partager ce serveur avec un autre agent

Dans ce scénario, cet ordinateur héberge le serveur Sama Kër et l'autre agent est le client.

1. Ouvre un terminal dans `AgentsIA_pratique`. Si le catalogue n'existe pas encore, crée-le une fois :

   ```powershell
   python .\mcp_server\create_catalogue.py
   ```

2. Démarre le serveur en mode réseau et laisse ce terminal ouvert :

   ```powershell
   python .\mcp_server\server.py reseau
   ```

   Pour choisir un autre port, par exemple `9000` :

   ```powershell
   python .\mcp_server\server.py reseau --port 9000
   ```

   `reseau` active HTTP et l'écoute sur toutes les interfaces réseau (`0.0.0.0`). Le serveur affiche
   l'URL à transmettre, par exemple `http://192.168.1.23:8000/mcp`. Ne partage pas `0.0.0.0` :
   c'est une adresse d'écoute, pas une adresse de destination.
3. Autorise Python sur les **réseaux privés** dans le pare-feu Windows si celui-ci le demande. Les
   deux ordinateurs doivent pouvoir communiquer sur le même réseau local.
4. Transmets à l'autre personne l'URL affichée. Dans son client MCP, elle doit choisir
   **Streamable HTTP** et utiliser cette URL. Avec LangChain, la configuration ressemble à ceci :

   ```python
   client = MultiServerMCPClient({
       "catalogue": {
           "transport": "streamable_http",
           "url": "http://192.168.1.23:8000/mcp",
       }
   })
   outils = await client.get_tools()
   ```

   L'adresse ci-dessus est un exemple : l'autre agent doit utiliser l'adresse IP et le port affichés
   par ton serveur. Il devrait découvrir `rechercher_biens` et `detail_bien`.

Ce serveur n'a pas d'authentification. Toute personne pouvant joindre son adresse peut appeler ses
outils de lecture; partage-le uniquement sur un réseau de confiance et ne l'expose pas directement
sur Internet.

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
