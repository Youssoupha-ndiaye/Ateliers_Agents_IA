# Interface — Console conseiller Sama Kër

Interface web complète et **temps réel** pour piloter l'agent : boîte de réception, tableau de bord,
agenda des conseillers et page de validation (double validation). Elle s'exécute **à part** :
l'ancienne interface Gradio ([agent/ui_gradio.py](../agent/ui_gradio.py)) et le pipeline en ligne de
commande restent inchangés et fonctionnent toujours.

---

## 1. Lancer l'interface

Depuis le dossier `AgentsIA_pratique`, dans l'environnement Python où sont installées les dépendances
du projet (`pip install -r requirement.txt`) :

```bash
# 1. Ollama doit tourner (le modèle est préchargé automatiquement par l'interface)
ollama serve

# 2a. Avec le vrai Gmail et les vrais agendas Google
python Interface/app.py

# 2b. OU en mode démo : Gmail et agendas simulés, idéal pour la présentation
python Interface/app.py --demo
```

Le navigateur s'ouvre tout seul sur **http://127.0.0.1:8080**. `Ctrl+C` pour arrêter.

Aucune dépendance nouvelle : FastAPI et Uvicorn sont déjà installés avec Gradio.

| Option | Effet |
|---|---|
| `--demo` | Gmail et agendas simulés. Le **modèle Ollama et le serveur MCP restent réels**. Un bouton « Simuler un email » apparaît. |
| `--port 8080` | Port de l'interface (8080 par défaut ; 7860 est pris par Gradio, 8000 par le MCP en HTTP). |
| `--detection 2` | Intervalle de détection des nouveaux emails Gmail, en secondes (2 par défaut). |
| `--keep-alive 1h` | Durée pendant laquelle Ollama garde le modèle en mémoire. |
| `--sans-mcp-persistant` | Revient au comportement d'origine (un processus MCP par appel d'outil). |
| `--sans-navigateur` | N'ouvre pas le navigateur automatiquement. |
| `--host 0.0.0.0` | Rend l'interface accessible aux autres appareils du réseau local. |

> Une seule instance à la fois : ne lancez pas en même temps `app.py`, `pipeline.py` et `ui_gradio.py`
> sur le même compte Gmail, sinon deux agents traiteraient les mêmes emails.

---

## 2. Les pages

| Page | Ce qu'on y voit |
|---|---|
| **Tableau de bord** | 6 indicateurs (emails du jour, en traitement, validations en attente, fils répondus, visites à venir, transmis) · **l'agent en direct** (chaque outil appelé, chaque réflexion du modèle, au moment où elle se produit) · validations en attente · **suivi de chaque conversation en 4 étapes** : Reçu → Réponse validée (V1) → Choix du client → Visite confirmée (V2) · prochaines visites · activité en temps réel. |
| **Boîte de réception** | Tous les emails reçus, regroupés par fil, avec recherche et filtres par état (À traiter, En attente, Répondus, Transmis). À droite : la conversation complète (client, réponses envoyées, **brouillon en attente** en pointillés), le suivi pas à pas de l'agent, la progression et le dossier du client (biens et créneaux réellement proposés). Bouton « Relancer l'agent ». En mode démo : répondre à la place du client, avec un bouton qui pré-remplit le choix du 1er bien et du 1er créneau. |
| **Validations** | Les actions de l'agent qui attendent votre accord. Pour chaque demande : **Validation 1 sur 2** (réponse au client) ou **2 sur 2** (visite + confirmation), un **résumé** (destinataire, biens cités, créneaux, question posée…), le message du client à côté de la réponse rédigée par l'IA (identifiants SK-xxx et heures surlignés). Pour la validation 2, un « billet » montre la visite exacte qui sera inscrite. Boutons **Valider** / **Refuser** avec commentaire. Historique des décisions de la session. |
| **Agenda** | Semaine des conseillers (lundi → samedi, 8 h–19 h, heure de Dakar), une couleur par conseiller, filtres, navigation de semaine en semaine, ligne rouge « maintenant ». Les visites créées par l'agent (pleines) se distinguent des rendez-vous existants (hachurés) et **apparaissent dès la validation 2**. Détail d'un rendez-vous et charge de la semaine par conseiller. |

Autres détails : notifications (toast + son + notification du navigateur, bouton cloche), compteur de
validations dans l'onglet du navigateur, thème clair / sombre, affichage adapté au téléphone, état du
système (Gmail, modèle, MCP, agenda, connexion temps réel) dans la barre latérale.

---

## 3. Pourquoi les emails arrivent maintenant tout de suite

Avant, il fallait 2 à 3 minutes entre l'envoi d'un email et son apparition dans Gradio. Quatre causes,
quatre corrections :

| Cause du délai | Avant | Maintenant |
|---|---|---|
| Relève de Gmail | une boucle toutes les 60 s (puis 30 s) | **veille toutes les 2 s** avec une requête très légère (identifiants seulement) ; l'email s'affiche **dès sa détection** |
| Affichage | l'email n'apparaissait dans Gradio qu'au moment de la validation, donc **après** tout le travail du LLM | l'email, puis chaque étape de l'agent, sont **poussés au navigateur à l'instant** (Server-Sent Events, mesuré à ~20 ms) |
| Modèle Ollama | déchargé après 5 min d'inactivité, rechargé à froid au mail suivant | **préchargé au démarrage** et gardé en mémoire 1 h (`keep_alive`) |
| Serveur MCP | un nouveau processus Python lancé **à chaque appel d'outil** | **une seule session MCP** ouverte pour toute la durée de l'interface |

Ce qui reste incompressible : le temps de réflexion du modèle lui-même (qwen2.5:7b sur CPU, quelques
dizaines de secondes par email). Mais il n'est plus « invisible » : on voit l'email arriver, puis l'agent
chercher les biens, lister les créneaux et rédiger, en direct. Pour aller plus vite encore : un GPU,
ou un `num_ctx` plus petit.

> Avec 2 s de détection, la consommation de quota Gmail reste très faible (environ 150 unités par minute
> pour une limite de 15 000).

---

## 4. Comment c'est construit

```
Interface/
├── app.py                  serveur FastAPI : pages, API, flux temps réel (/api/flux)
├── noyau/
│   ├── evenements.py       état partagé + diffusion instantanée aux navigateurs (SSE)
│   ├── sources.py          veille Gmail 2 s, lectures (fil complet, agenda), instrumentation des backends
│   ├── moteur.py           moteur de l'agent : réveil immédiat, modèle préchargé, session MCP unique, suivi en direct
│   └── validation_web.py   approbateur web : bloque l'agent jusqu'au clic Valider / Refuser, résumé de l'IA
├── static/
│   ├── index.html
│   ├── css/style.css
│   └── js/app.js           application monopage sans dépendance
└── README.md
```

**Le code de l'agent n'est pas modifié.** L'interface réutilise tel quel `pipeline.traiter_message`
(garde-fous, agent LLM, outils MCP, double validation) :

- la validation passe par `validation.set_approbateur(...)`, exactement comme Gradio le faisait ;
- les backends Gmail et Agenda sont des **sous-classes** qui signalent chaque brouillon, envoi, libellé et visite ;
- le suivi en direct se branche sur `tracing.log` (les traces JSONL continuent d'être écrites dans `traces/`) ;
- l'état des conversations reste dans `state/conversations.json`.

Les règles de sécurité restent donc identiques : **rien n'est envoyé ni inscrit dans l'agenda sans le clic
du conseiller**, l'email piégé est bloqué par le garde-fou avant le LLM, et une décision déjà prise ne peut
pas être rejouée.

### API (pour information)

| Méthode | Route | Rôle |
|---|---|---|
| GET | `/api/flux` | flux temps réel (SSE) |
| GET | `/api/etat` | instantané complet (au chargement et à la reconnexion) |
| GET | `/api/fils/{id}` | conversation complète d'un fil + dossier |
| POST | `/api/validations/{id}` | `{"accord": true/false, "motif": "..."}` |
| POST | `/api/fils/{id}/relancer` | remet le fil « À traiter » et relance l'agent |
| POST | `/api/releve` | fait passer l'agent tout de suite |
| GET | `/api/agenda?debut=AAAA-MM-JJ&jours=6` | événements des conseillers |
| POST | `/api/demo/email` | (mode démo) simule un email client |

---

## 5. Scénario de présentation (mode démo)

1. `python Interface/app.py --demo`.
2. **Simuler un email** → « Demande de visite » → Envoyer. L'email apparaît aussitôt et l'agent travaille en direct.
3. Une notification « Validation 1/2 à faire » arrive. Page **Validations** : lire le résumé, puis **Valider et envoyer**.
4. Boîte de réception → le fil → **Choisir le 1er bien et le 1er créneau** → Envoyer (c'est la réponse du client).
5. « Validation 2/2 » : le billet de la visite → **Valider : inscrire et confirmer**.
6. Page **Agenda** : la visite est dans l'agenda du conseiller ; le tableau de bord montre les 4 étapes cochées.
7. Bonus : **Simuler un email** → « Email piégé » : il est transmis à un conseiller sans passer par l'IA.

---

## 6. Limites connues

- L'agent traite **un email à la fois** (comme le pipeline d'origine) : pendant qu'une validation attend,
  les nouveaux emails s'affichent immédiatement mais restent « En file d'attente ».
- Si l'interface est arrêtée pendant qu'une validation attend, le brouillon reste dans Gmail avec le
  libellé « En attente de validation » : il faut le traiter à la main ou utiliser « Relancer l'agent ».
- Le texte de l'IA ne peut pas être modifié dans l'interface avant l'envoi : on valide ou on refuse avec un motif.
- La détection repose sur une interrogation de Gmail toutes les 2 s. Un vrai « push » Gmail demanderait
  Google Cloud Pub/Sub et une configuration supplémentaire.
