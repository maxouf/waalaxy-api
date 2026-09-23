# waalaxy-api

Client Python de l'API interne de [Waalaxy](https://www.waalaxy.com) et serveur [MCP](https://modelcontextprotocol.io) pour la piloter depuis Claude Code (ou tout client MCP) : stats de campagnes, brouillons, listes de prospects, pause / reprise / archivage, lancement d'une campagne depuis un brouillon.

L'API publique officielle de Waalaxy (4 endpoints) ne permet ni de lire les stats ni de lancer une campagne. Ce projet s'appuie sur l'API interne de l'application web, cartographiée dans [`docs/waalaxy-api.md`](docs/waalaxy-api.md). Elle n'est pas documentée par Waalaxy et peut changer sans préavis.

## Installation

Prérequis : Python 3.11+, [uv](https://docs.astral.sh/uv/), un compte Waalaxy utilisé en **mode cloud** (l'exécution des actions LinkedIn est déléguée aux serveurs Waalaxy, ce qui est le cas par défaut sur les plans payants).

```bash
git clone https://github.com/maxouf/waalaxy-api.git
cd waalaxy-api
uv sync
```

### 1. Récupérer votre jeton

Le jeton est un JWT sans expiration : il reste valable jusqu'à ce que vous vous déconnectiez de Waalaxy.

1. Ouvrez https://app.waalaxy.com connecté à votre compte.
2. Ouvrez la console du navigateur (F12 → onglet Console).
3. Collez et validez :
   ```js
   copy(JSON.parse(localStorage.cloudAuthStore).apiToken)
   ```
   Le jeton est maintenant dans votre presse-papiers. Si `cloudAuthStore` n'existe pas, votre compte est en mode extension : le jeton n'est pas accessible de cette façon.

### 2. Configurer

```bash
mkdir -p ~/.config/waalaxy
cp api.env.example ~/.config/waalaxy/api.env
chmod 600 ~/.config/waalaxy/api.env
# puis coller le jeton après WAALAXY_TOKEN=
```

Les mêmes clés passent aussi en variables d'environnement (`WAALAXY_TOKEN=…`), ce qui prime sur le fichier. `WAALAXY_CONFIG` change le chemin du fichier.

### 3. Vérifier

```bash
.venv/bin/waalaxy-mcp status
# Waalaxy : jeton valide. Campagnes : {'running': 3, 'paused': 4, ...}
```

### 4. Brancher dans Claude Code

```bash
claude mcp add waalaxy --scope user -- "$PWD/.venv/bin/waalaxy-mcp"
```

Pour un autre client MCP (Claude Desktop, Cursor, OpenCode…) : serveur stdio, commande `<chemin>/.venv/bin/waalaxy-mcp`, aucun argument.

## Les outils MCP

Lecture (vues résumées ; le brut de l'API derrière `full=True`) :

| Outil | Ce qu'il rend |
|---|---|
| `waalaxy_stats` | une ligne par campagne : état, total, en cours, terminés, erreurs, invitations acceptées, réponses |
| `waalaxy_campaign` | fiche d'une campagne : origines, compteurs, séquence en étapes lisibles |
| `waalaxy_drafts` / `waalaxy_draft` | brouillons et détail (sources prévues, séquence) |
| `waalaxy_travelers_summary` | prospects d'une campagne groupés par état |
| `waalaxy_global_stats` | invitations et réponses du compte sur une période |
| `waalaxy_prospect_lists` | listes de prospects, triées par taille |

Écriture, derrière `confirm=True`. Sans lui, l'outil rend un aperçu nommé de ce qui partirait et n'émet rien :

| Outil | Effet |
|---|---|
| `waalaxy_pause` / `waalaxy_play` | pause / reprise d'une campagne |
| `waalaxy_stop` | archivage, **irréversible** dans Waalaxy |
| `waalaxy_launch_draft` | crée une campagne en `running` depuis un brouillon, avec les prospects éligibles d'une liste (ceux qui ne sont pas déjà en campagne) |

`WAALAXY_DRY_RUN=1` dans l'environnement bloque toute écriture au niveau du client, même avec `confirm=True`. Utile pour les tests et les évaluations d'agents.

## Ligne de commande

```bash
.venv/bin/waalaxy stats                      # tableau : une ligne par campagne
.venv/bin/waalaxy drafts
.venv/bin/waalaxy lists
.venv/bin/waalaxy eligible <listId>          # prospects hors campagne en cours
.venv/bin/waalaxy launch-draft <draftId> --list <listId>        # dry-run : montre le payload
.venv/bin/waalaxy launch-draft <draftId> --list <listId> --go   # lance pour de vrai
```

`python client.py …` à la racine fait la même chose sans venv (httpx requis).

## Tests

```bash
uv sync --extra dev
.venv/bin/pytest -q
```

Les fixtures de `tests/fixtures/` sont de vrais retours de l'API, anonymisés (ids remplacés, noms génériques).

## Structure

```
src/waalaxy_api/client.py   client HTTP + CLI
src/waalaxy_mcp/server.py   serveur MCP (outils, gates)
src/waalaxy_mcp/views.py    vues résumées pour le modèle
docs/waalaxy-api.md         cartographie de l'API interne
docs/public-api.oas.js      OpenAPI de l'API publique officielle (pour comparaison)
```

## Limites connues

- Le lancement d'une campagne ne supprime pas le brouillon (l'application le fait à part).
- Débit non mesuré ; le client se limite à 2 requêtes/s.
- Mode extension non pris en charge : le jeton n'est alors ni dans `localStorage` ni dans un cookie.
