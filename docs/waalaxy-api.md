# Waalaxy — API interne

Rétro-ingénierie réalisée le 2026-09-23 sur un compte à plan payant, session en **mode cloud**.
Objectif : suivre les stats de campagnes (lecture) et lancer des campagnes (écriture).

## Verdict de triage

**API typée, contrat lisible dans les sourcemaps de prod.** `app.waalaxy.com` est une SPA Vite (« mystique ») ; les `.js.map` sont servis (`index-*.js.map` 6,9 Mo, `app-*.js.map` 8,9 Mo), soit 2 069 fichiers TypeScript du monorepo `waapi`, dont les clients typés de chaque microservice (`libs/clients/<service>-client/src/routes/**/X.client.ts` : verbe + chemin). Les fichiers `.interfaces.ts` (types purs) sont absents : les corps de requête viennent des hooks appelants (`apps/mystique/client/src/entities/**`) et des erreurs de validation zod de l'API, qui listent les champs manquants.

Aucune signature calculée, aucun Turnstile sur les appels. Débit non testé au-delà de ~1 req/s.

## Surfaces

| Hôte | Rôle |
|---|---|
| `https://stargate.prod.aws.waalaxy.com/api/<service>/…` | **Passerelle de l'API interne.** Un préfixe par microservice. C'est la seule surface utile ici. |
| `https://otto.prod.aws.waalaxy.com/` | Socket (temps réel). Non utilisé. |
| `https://waalaxy-omicron-8ce699a.zuplo.app` | **API publique officielle** (clé Bearer créée dans l'app, gérée par le service `janus`). Portail : `https://waalaxy-omicron-8ce699a.zuplo.site/api`. 4 endpoints seulement : `GET /prospectLists/getProspectLists`, `POST /prospects/addProspectFromIntegration` (import + enrôlement via `campaignId`), `GET /campaigns/getAll` (running/paused, id + nom), `/integrations/test`. Ni stats, ni lancement, ni brouillons → insuffisante pour l'objectif. OAS copiée dans `docs/public-api.oas.js`. |
| `chrome-extension://hlkiignknimkfafapmgpbnbnmkajgljh` | Extension Waalaxy (mode extension : c'est elle qui exécute les actions LinkedIn et détient le jeton). En mode cloud elle n'intervient pas. |

Microservices vus derrière stargate : `profesor` (campagnes, prospects, listes, tags, imports, triggers, worlds), `hawking` (statistiques), `pictochat` (conversations/inbox), `janus` (clés API publique), `bouncer` (permissions), `felindra` (facturation), `hermes` (emails), `voltaire` (signatures), `wizz` (notifications), `akatsuki` (exécution cloud des commandes LinkedIn : `runCommand`), `enutrof` (email finder), `zilean`, `shiva`, `crypto`, `coach_nico`, `bridge-palpatine`.

## Authentification

- **Porteur** : `Authorization: Bearer <JWT>` sur chaque appel. Aucun cookie nécessaire côté API.
- **Où le trouver (mode cloud)** : `localStorage.cloudAuthStore` → `{apiToken, userId, apiUrl, socketUrl, environmentName, authSource:"cloud", canBypassExtension:true, timestamp}` ; doublé par le cookie `waalaxy_token` (path `/`). Le store n'est **pas** persisté par zustand, la source de vérité est ce `localStorage`.
- **Forme** : JWT signé, payload `{_id, linkedinId}` — **ni `exp` ni `iat`**. Vérifié le 2026-09-23 : pas d'expiration, le jeton vaut tant que la session cloud n'est pas révoquée (déconnexion, re-login LinkedIn). Aucun appel de refresh à reproduire.
- **Mode extension** : le jeton est dans le store de l'extension (`girbal`) et poussé à la page par messagerie Chrome ; il n'est ni dans `localStorage` ni dans un cookie. Si `cloudAuthStore` est absent, c'est ce mode.
- **Obtention** : sur `app.waalaxy.com` connecté en mode cloud, console DevTools → `copy(JSON.parse(localStorage.cloudAuthStore).apiToken)` ; ou le cookie `waalaxy_token` (domaine `app.waalaxy.com`), qui porte le même JWT. Procédure pas à pas dans le README.
- **Erreur** : 401 = jeton refusé. Les 400 sont des erreurs de validation `{code:"Vxxx400-nnn", message:"champ: Invalid input: expected string, received undefined"}`.

## Endpoints (service `profesor`, préfixe `/api/profesor`)

Tous vérifiés en appel réel le 2026-09-23 depuis la page (fetch avec le Bearer), sauf mention.

| Verbe | Chemin | Corps / params | Réponse |
|---|---|---|---|
| POST | `/campaigns/getAll` | `{state: ["draft","paused","running","stopped"], start: 0, count: 20, search?: {value, fields:["name"]}}` — `state` optionnel (défaut : running+paused, semble-t-il), l'app passe `count: 99999` pour tout lister | `{total, campaigns[]}` — voir modèle. **`total` = total filtré, la page fait `count` éléments** (49 annoncées, 20 renvoyées sans `count`). |
| POST | `/campaigns/countPerStatus` | `{}` | `{count:[{status:"stopped", value:43, hasConnectWithNote}, …, {status:"total", value:52}, {status:"draft", value:3}]}` |
| GET | `/campaigns/:campaignId` | — | **`{campaign: {…}}`** : la campagne complète (avec `world`) est enveloppée, contrairement à `getAll` (vérifié le 2026-09-23 lors de la recette du serveur MCP) |
| GET | `/campaigns/draft/:draftId` | — | `{draftCampaign}` — voir modèle. `GET /campaigns/draft` sans id → 400 « Campaign id should be a mongoid » : **la liste des brouillons se fait via `getAll` avec `state:["draft"]`**. |
| POST | `/campaigns/draft` | `{name, iconColor, sequence, prospectSources, subWorlds?, lastStepIndexInQuickLaunch?}` — `sequence` = le `world` d'une campagne existante sans `_id/createdAt/updatedAt/__v/userId/name`, `worldTemplate` réduit à son id ; `prospectSources: [{discriminator:"unsaved_prospect_batch_origin", prospectList:{…objet de getProspectLists}, count, exc: []}]` (ou `inc:[ids]`, `filters?`) | `{draftCampaign}` — **vérifié le 2026-09-23** (brouillon `6ab3b7a5c5202f09f08ee5eb`) |
| PUT | `/campaigns/draft/:draftId` | idem, champs optionnels | `{draftCampaign}` (non testé) |
| DELETE | `/campaigns/draft/:draftId` | — | (non testé) |
| POST | `/campaigns` | **= lancer une campagne** : `{name, worldToCreate, replySubWorldToCreate?, iconColor, triggers: [triggerId…], prospects: [{listId, prospectIds[]}], imports: [{importId, importState}], onReply?}` — l'app construit `worldToCreate = {...draft.sequence sans _id/createdAt/updatedAt, name}` | `{campaign}` créée **directement en `running`** — **vérifié le 2026-09-23** (`6ab3b8a3ccc336f2c8fe200d`, 3 966 prospects). `prospectIds` obtenus via `POST /prospects/getProspects {prospectList, prospectSelection:{excluded:[]}, excTravelerStatus:["traveling","paused","frozen","postponed"], size:9999999, projection:{_idOnly:true}}` → `{prospects:[{_id}]}` (5 142 → 3 966 éligibles). L'app supprime ensuite le brouillon (`DELETE /campaigns/draft/:id`). |
| PUT | `/campaigns/:campaignId/play` | — | reprend une campagne **en pause**. Ne s'applique pas à un brouillon. |
| PUT | `/campaigns/:campaignId/pause` | — | |
| PUT | `/campaigns/:campaignId/stop` | — | archive (irréversible dans l'UI) |
| PUT | `/campaigns/:campaignId` | mise à jour | |
| POST | `/campaigns/duplicate` | | |
| POST | `/campaigns/:campaignId/travelers` | ajout de prospects (`addProspectBatch`) | |
| POST | `/campaigns/:campaignId/triggers` | | |
| POST | `/travelerssummary` | `{campaignId, status: {}}` (les deux obligatoires) | `{travelersSummaries: {error:[{_id, travelerCount, type:"state", status, travelers:[ids]}], finished:[…], deleted:[…], …}}` |
| POST | `/travelers/getAll`, `/travelers/countAll` | (non testés) | |
| GET/POST | `/worlds`, `/worlds/:worldId`, `/worldTemplates`, `/worldTemplates/quickLaunch` | séquences et modèles (non testés) | |

Codes d'erreur de `POST /campaigns` relevés dans l'app : `R005400-004` prospects déjà en campagne, `R005409-002` / `R002400-011` nom déjà pris, `R002401-001` permission de créer une séquence, `R002401-002` plan Business requis, `R002400-002` compte email introuvable.

### Listes et prospects (vérifiés le 2026-09-23, recette réelle)

| Verbe | Chemin | Corps | Réponse / piège |
|---|---|---|---|
| POST | `/prospectLists/getProspectLists` | `{}` | tableau `[{_id, name, totalProspects, …}]`. **`totalProspects` peut rester à 0** après un import : compter avec `getProspects`. |
| POST | `/prospectLists/createProspectList` | **`{prospectList: {name, iconColor?, iconLabel?}}`** (enveloppé, sinon `V000400-001 prospectList: expected object`) | l'objet liste avec `_id` |
| POST | `/prospects/addProspectFromIntegration` | `{prospects:[{url, customProfile?:{firstName,lastName,email…}, customVariables?}], prospectListId, origin:{name}, canCreateDuplicates?, moveDuplicatesToOtherList?, campaignId?}` — même contrat que l'API publique Zuplo, **accepte le Bearer cloud**. `url` accepte `/in/<memberId ACoAA…>` ; le serveur résout `publicIdentifier`, `salesMemberId`, headline, région depuis son cache de profils. Lots de 10 en ~0,6 s. | `{result:[{importCode:"success"\|"duplicated_prospect"\|…, prospect:{_id, profile}}]}` — **c'est la voie qui persiste** (vérifié par `getProspect` et `getProspects` juste après). `origin.name` libre s'affiche « API-<name> ». |
| POST | `/prospects/addProspectsToList` | flux « import CSV » de l'app : `{prospects:[{status:"unknown", profile:{memberId}, origin:{name:"csv"}, prospectList, sharedGroups:[], sharedProfessionalEvents:[], customProfile}], importId, importType:"csv", canCreateDuplicates, moveDuplicatesToOtherList, shouldOverwriteProfileData, shouldOverwriteCustomProfileData}` | **Piège** : répond `[{code:200, prospect:{_id,…}}]` avec un `_id` neuf à chaque appel **mais rien n'est persisté** en mode cloud (`getProspect {_id}` → `prospect_not_found`, liste vide). Dans l'app, c'est l'extension qui résout le statut avant l'envoi. Ne pas utiliser. |
| POST | `/imports/createImport` | `{origin:"csv", prospectList, moveDuplicatesToOtherList}` → `{_id}` ; puis `/prospects/addImportedProspect {_id, prospectsImportResult:[]}` et `/imports/updateImportStatus {importData:{_id, status:"finished"}}` | inutiles avec `addProspectFromIntegration`. `getImports` exige un `params` non documenté (400 sinon). |
| POST | `/prospects/getProspects` | `{prospectList:"<id>" (string), prospectSelection:{excluded:[]}, size, projection?:{_idOnly:true}, excTravelerStatus?, filters?, search?}` | `{prospects, prospectsCount, prospectListSize}`. Un objet dans `prospectList` → 400. Sans `prospectList`, tout le CRM (18 315). |
| POST | `/prospects/getProspect` | `{_id}` | le prospect (`R000404-001 prospect_not_found` sinon) |
| POST | `/prospects/getProspectsCount` | `{prospectList}` | `{count}` |

Script prêt : `scripts/likers_to_list.py` (réactions aux N derniers posts d'un profil → liste Waalaxy, via Apify sans cookie LinkedIn).

## Endpoints (service `voltaire`, préfixe `/api/voltaire`) — modèles de message

| Verbe | Chemin | Corps | Réponse |
|---|---|---|---|
| GET | `/contents/:contentId` | — | `{content:{_id, name, channel:"linkedin", type:"message", user, params:{messages:[{value, dispatchOrder, percentageMessageGeneratedByAI, valueGeneratedByAI}], gifs, audios, attachments}}}` — c'est ce que référence `waypoint.params.contentReference` |
| POST | `/contents` | même forme sans `_id`/`user` (`name` et `channel` obligatoires) | `{content}` — vérifié le 2026-09-23 (`6ab3b785d584b2cb1c7263c4`) |
| PUT/DELETE | `/contents/:contentId` | (`updateContent`, `deleteContent` dans la methodsMap, non testés) | |
| POST | `/prospectLists/getProspectLists` (profesor) | `{}` | tableau `[{_id, name, totalProspects, user, …}]` |

## Endpoints (service `hawking`, préfixe `/api/hawking`)

| Verbe | Chemin | Params | Réponse |
|---|---|---|---|
| GET | `/stats/allStats` | `users=<userId>` **obligatoire** (string), `startDate` et `endDate` en ISO UTC. Vérifié le 2026-09-23 : sans ces deux-là (ou avec `from/to`, `start/end`, `dateFrom/dateTo`, `since/until`, ignorés en silence) tout est à 0 ; avec `startDate=2024-01-01` → acceptation 44,6 % (3 144 / 7 050), réponse 13,6 % (1 556 / 11 480). | `{responsesMessage:{answerRate,totalReplies,totalSent}, responsesEmail:{…}, acceptance:{acceptanceRate,totalAccepted,totalSent}, emailDelivery, emailBounced, emailFinder, emailOpened}` |
| — | autres routes hawking : `getAcceptanceRate`, `getAnswerRate`, `getActionStats`, `getActionFilters`, `getMainFilters`, `getAnalyticsMembers`, `getEmailDeliveryRate`, `getEmailBounceRate`, `getEmailFinderRate` | chemins non extraits (autre constructeur de route) | |

**Pour les stats par campagne, `hawking` est inutile** : `getAll` renvoie `travelersCount` par campagne (voir modèle). C'est ce que le client utilise (`stats_table`).

## Modèle de données

### Campagne (`getAll`, `GET /campaigns/:id`)

`_id`, `name`, `user` (= userId, invariant sur les 20 échantillons), `state` (`draft|paused|running|stopped`), `priority`, `iconColor`, `triggers[]` (ids), `origins[{_id, origin:"trigger"|…, travelerCount, trigger, createdAt}]`, `history[{date, action:"created"|…}]`, `worldTags[]`, `worldComplexity`, `activeTravelersCount`, `hasTravelingTravelers`, `createdAt`, `updatedAt`, et :

- `world` : la séquence — `{name, userId, startingPoint:{id,type:"entry"}, waypoints:[{id, type, params?}], paths:[{id, from, to, condition}], complexity, tags, worldTemplate, prospectsPreConditions, _id}`. Types de waypoints vus : `entry`, `connectLinkedin`, `messageLinkedin` (`params.contentReference` = id de modèle de message), `goal`, `sleep`, `failed`, `end`. Conditions : arbre `{isAtomic, leftOperand, comparator:"OR", rightOperand, entity:{type:"isNotConnected"|"isConnected"|"isUnknownStatus"}}`.
- `travelersCount` : `{total, traveling, finished, paused, stopped, error, hasReplied, hasNotReplied, hasRepliedInterested, hasRepliedLaterInterested, hasRepliedNotInterested, hasAcceptedInvitation, hasNotAcceptedInvitation, hasBeenEnriched, hasNotBeenEnriched, frozen, postponed, deleted}` — les compteurs « hasReplied*» ne sont pas exclusifs entre eux (11 hasReplied, 16 interested, 24 laterInterested sur 167).

### Brouillon (`GET /campaigns/draft/:id`)

`{_id, name, user, state:"draft", sequence (même forme que `world`, avec `_id`), prospectSources[], iconColor, lastStepIndexInQuickLaunch, createdAt, updatedAt}`. **Pas de `world` ni de `travelersCount` dans `getAll`** pour un brouillon.

### Identifiants

| Champ | Observé | Ce que le nom suggère | Vérification |
|---|---|---|---|
| `campaign._id` | `68ecdc11…` | id de campagne | Identique entre `getAll`, `GET /campaigns/:id`, `travelerssummary.campaignId`, et l'URL de l'UI. Pas de piège. |
| `campaign.user` | id mongo | propriétaire | Invariant sur 100 % des échantillons = compte courant. Jamais une cible. |
| `draftCampaign._id` | `672b958c…` | id de brouillon | Même espace d'ids que les campagnes (un brouillon *est* une campagne en `state:"draft"`). |
| `travelers[]` dans `travelerssummary` | `68f3d77c…` | ids de prospects | À croiser avec `GET /profesor/prospects/:id` avant tout usage en écriture (non fait). |
| `waypoint.params.contentReference` | `672b9858…` | modèle de message | Non vérifié. |

## Unités et effets de bord

- Dates ISO 8601 UTC. Compteurs en entiers. Aucun montant.
- **Lecture sans effet de bord constaté** : `getAll`, `countPerStatus`, `GET /campaigns/:id`, `draft/:id`, `travelerssummary`, `allStats` appelés plusieurs fois ; `updatedAt` des campagnes inchangé. (Vérifié sur la seule campagne active, pas de vérification exhaustive.)
- **Écritures** : `POST /campaigns` crée en `running` immédiatement — les actions LinkedIn partent dès la file d'attente. `stop` archive. Le brouillon n'est pas supprimé par le lancement (l'app appelle `deleteDraft` séparément, à vérifier).

## Pagination

`getAll` : offset `start`, taille `count` (défaut app : 20, `count: 99999` pour tout). `total` est bien le total filtré ; c'est `campaigns.length` qui vaut la page.

## Limites et débit

Non mesuré. L'app fait des rafales de 5-10 appels au chargement. Le client reste à 2 req/s.

**Piège navigateur** : deux `fetch` lancés en parallèle par `javascript_tool` sur le même onglet gèlent le renderer (timeouts CDP à 45 s), et un onglet gelé le reste. Une sonde à la fois, avec `AbortController` à 10-12 s.

## Vérifié le

2026-09-23 — depuis le conteneur avec `client.py` : 52 campagnes (49 + 3 brouillons, `stats`), `allStats` sur 6 variantes de paramètres, `countPerStatus`, `travelerssummary` (1), brouillon (1). Aucun appel d'écriture exécuté.

## Reste à faire

- Pour « lancer une campagne » : `launch-draft <id>` en dry-run montre le payload ; GO explicite de l'utilisateur sur le brouillon désigné avant tout `--go`. Sans `prospects` ni `triggers`, la campagne créée est vide : décider d'abord quelle liste de prospects y mettre (`prospects: [{listId, prospectIds}]`, ids via `POST /profesor/prospects/…`, non cartographié).
