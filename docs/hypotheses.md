# Hypothèses, formulées avant toute mesure

Chaque hypothèse est écrite **avant** les expériences (C5, E1). Les résultats seront comparés à ces
prédictions, y compris quand ils les contredisent. Protocole commun : au moins 5 runs par configuration,
graines 1 à 5, dispersion présentée (moyenne, écart-type, min, max) (E2).

| # | Exigence | Hypothèse | Mesure | Ce qui la réfuterait |
|---|---|---|---|---|
| H1 | E1 | Les agents LLM produisent des réactions **plus variées** qu'une règle de seuil face au même communiqué, et réagissent au **ton** (rassurant, factuel, vague), là où la règle ne voit qu'une intensité. | Dispersion des variations de `peur` après un communiqué ; écart d'effet entre les tons. Version LLM contre `baseline_regles` | Même dispersion et même effet du ton dans les deux versions |
| H2 | C5 | En mode asynchrone, les agents servis par un modèle rapide agissent plus tôt. Les premiers arrivés au guichet sont surtout des agents rapides, et le basculement survient **plus tôt** qu'en mode synchrone. | Instant du point de non-retour ; rang moyen d'arrivée au guichet selon la latence du modèle | Pas de différence d'instant ni d'ordre entre les modes |
| H3 | E5 | La population hybride (grands et petits modèles, 25 % de règles) produit une courbe de retraits **comparable** à « tous sur le grand modèle », pour **au moins 3 fois moins** de tokens. | Tokens et appels par run ; écart entre les courbes de retraits cumulés | Courbes très différentes, ou économie inférieure à 2 fois |
| H4 | A3 | Face à une même situation figée, la boulangère (trésorerie de commerce) retire plus souvent que le retraité (40 ans de relation), et leurs justifications invoquent des raisons différentes. | 10 tirages chacun ; taux de retrait ; analyse des justifications | Taux identiques ou justifications interchangeables |
| H5 | C8 | Avec 140 déposants, le basculement est plus brutal (montée plus rapide de l'ICC) qu'avec 40, parce que les files sont plus visibles. | Pente maximale de l'ICC ; durée entre « Proche » et « Irréversible » | Pentes équivalentes |
| H6 | Levier | Une garantie des dépôts annoncée avant l'article empêche l'émergence du contexte commun « tout le monde retire ». | ICC maximal ; occurrence d'un défaut | Émergence aussi fréquente avec la garantie |
| H2b | C4 | En mode hybride, la part des décisions LLM appliquées en retard augmente avec la population et avec la taille du modèle (latence) ; à échéance égale, le basculement survient entre celui du mode synchrone et celui du mode asynchrone. | `decision.made.voie` par modèle et par run ; `resume.llm.voies` ; instant du point de non-retour selon le mode | Part en retard indépendante du modèle et de la charge, ou basculement hors de l'intervalle |
| H7 | C7 | Le retard entre un événement et sa prise en compte dans les indicateurs augmente avec la population et devient sensible en mode asynchrone sous quota. | `retard_ms` des indicateurs, médiane et 95e centile, à 40 et 140 agents | Retard constant quelle que soit la charge |
