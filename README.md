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

### 5. Avec ChatGPT ou Codex

ChatGPT ne lance pas de serveur sur votre machine : il ne se connecte qu'à une adresse HTTP. Deux façons de faire.

**Codex (CLI d'OpenAI)** lance les serveurs stdio comme Claude Code. Après les étapes 1 à 3, ajoutez dans `~/.codex/config.toml` :

```toml
[mcp_servers.waalaxy]
command = "/chemin/vers/waalaxy-api/.venv/bin/waalaxy-mcp"
```

**ChatGPT (web ou desktop)** : plan Plus ou supérieur, mode développeur activé (Réglages → Connecteurs → Avancé). Lancez le serveur en HTTP local :

```bash
.venv/bin/waalaxy-mcp --http 8000
# Waalaxy MCP : http://127.0.0.1:8000/mcp
```

puis reliez-le à ChatGPT par le **Secure MCP Tunnel** d'OpenAI (un petit client qui tourne chez vous et ouvre une connexion sortante vers ChatGPT, sans adresse publique). Dans ChatGPT, ajoutez le connecteur en mode **Tunnel** et choisissez votre tunnel. Le serveur et le tunnel doivent rester allumés pendant que vous utilisez ChatGPT.

Le serveur n'écoute que sur `127.0.0.1` et n'a **aucune authentification** : il porte votre jeton Waalaxy. Ne l'exposez jamais directement sur internet (ngrok, redirection de port…) ; quiconque aurait l'adresse piloterait votre compte.

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
| `waalaxy_prospect_list` | fiche d'une liste : taille, dates, prospects en campagne |
| `waalaxy_search_prospects` | recherche de prospects (nom, poste, entreprise), dans une liste ou partout |
| `waalaxy_prospect` | fiche d'un prospect depuis son URL LinkedIn : relation, liste, tags, notes, historique |
| `waalaxy_tags` | tags du compte |

Écriture, derrière `confirm=True`. Sans lui, l'outil rend un aperçu nommé de ce qui partirait et n'émet rien :

| Outil | Effet |
|---|---|
| `waalaxy_pause` / `waalaxy_play` | pause / reprise d'une campagne |
| `waalaxy_stop` | archivage, **irréversible** dans Waalaxy |
| `waalaxy_launch_draft` | crée une campagne en `running` depuis un brouillon, avec les prospects éligibles d'une liste (ceux qui ne sont pas déjà en campagne) |
| `waalaxy_add_to_campaign` | ajoute des prospects précis (ids) d'une liste à une campagne ; écarte ceux déjà en campagne, refuse une liste vide |
| `waalaxy_create_list` / `waalaxy_rename_list` | crée ou renomme une liste de prospects (doublon de nom refusé) |
| `waalaxy_create_tag` | crée un tag (doublon refusé) |
| `waalaxy_prospect_note` | ajoute ou remplace la note d'un prospect |
| `waalaxy_move_prospects` | déplace des prospects précis (ids) d'une liste vers une autre |
| `waalaxy_tag_prospects` | pose ou retire (`remove=True`) un tag sur des prospects précis d'une liste |
| `waalaxy_set_prospect_state` | change l'état de prospection (`interested`, `later_interested`) de prospects précis |

Les trois derniers envoient une sélection explicite `{included: [ids]}` et refusent une liste vide : chez Waalaxy, une sélection vide signifie « tous les prospects ». Corps observés dans l'application le 24/09/2026 et testés en réel sur un prospect de test.

**Volontairement absents** : suppression de prospects, suppression de listes, sortie de prospects d'une campagne (corps non observé).

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

## Sécurité des écritures

- Le client refuse toute requête autre que GET en dehors d'une liste explicite de routes de lecture (`READ_POSTS`). Une écriture passe obligatoirement par une méthode dédiée, avec cible nommée et dry-run par défaut.
- **Ne jamais envoyer de requête exploratoire à une route d'écriture**, même avec un corps vide « pour voir l'erreur de validation ». Sur cette API, un corps sans sélection peut vouloir dire « tous les prospects » : `POST /prospects/archiveProspects {}` supprime l'intégralité des prospects du compte, sans confirmation ni retour possible.

## Limites connues

- Le lancement d'une campagne ne supprime pas le brouillon (l'application le fait à part).
- Débit non mesuré ; le client se limite à 2 requêtes/s.
- Mode extension non pris en charge : le jeton n'est alors ni dans `localStorage` ni dans un cookie.

## Licence

[MIT](LICENSE).
