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
| H8 | C4, E5 | Avec le Système 1 (`regle` puis `nli`), en mode hybride, les appels LLM des déposants sont **au moins divisés par 2** et le retard moyen des décisions LLM aussi, **sans changer l'issue** : part retirée, ICC maximal et instant d'émergence restent dans la dispersion de la référence sans Système 1. Le moteur `nli` décide seul au moins aussi souvent que `regle`. | Séries `reference` (`--systeme1 aucun`), `s1-regle`, `s1-nli`, mêmes graines : `requetes/etiquettes.sql` (appels, retard, `pct_decide_par_s1`, retraits confirmés et infirmés par le LLM) | Économie inférieure à 1,5 fois, ou issue décalée de plus d'un écart-type de la référence. Point de méthode : quand le Système 1 décide, l'agent ne produit pas de nouvelle phrase LLM et ses croyances suivent les règles jusqu'à sa prochaine réflexion |
| H7 | C7 | Le retard entre un événement et sa prise en compte dans les indicateurs augmente avec la population et devient sensible en mode asynchrone sous quota. | `retard_ms` des indicateurs, médiane et 95e centile, à 40 et 140 agents | Retard constant quelle que soit la charge |

## Résultats

### H8 : Système 1 (27/10/2026, mode hybride, 140 déposants, graines 1 à 3)

Moins que les 5 graines du protocole : à confirmer. Séries `reference`, `s1-regle`, `s1-nli`
(`requetes/etiquettes.sql`).

| Série | Appels LLM (moyenne) | Retard moyen d'une décision LLM | Part retirée | Défauts | ICC max | Décidé seul par le S1 |
|---|---|---|---|---|---|---|
| référence | 617 | 429 min | 15 % | 3/3 (vers J6) | 0,52 | — |
| `regle` | 27 (÷ 23) | 121 min | 1,1 % | 0/3 | 0,00 | 98 % |
| `nli` | 419 (÷ 1,47) | 62 min (÷ 7) | 15 % | 3/3 (vers J6) | 0,36 | 53 % |

**H8 est réfutée pour les deux moteurs.** `regle` change l'issue : il juge « non » avec assurance là où le LLM
aurait retiré, et ces cas ne lui sont jamais confiés (le LLM a refusé 32 des 34 retraits proposés par la
règle). `nli` garde l'issue mais n'atteint pas le facteur 1,5 d'économie, et l'ICC maximal baisse ; sa
probabilité ne prédit pas la décision du LLM (2 à 7 % de retraits du LLM dans chaque tranche de
probabilité). La latence d'un appel isolé ne change pas (~1,6 s) : le gain de `nli` sur le retard vient
probablement de rafales d'appels plus petites (non vérifié).

### Système 1 appris (28/10/2026)

Classifieur mDeBERTa entraîné sur 3 589 décisions LLM (197 retraits) de 16 runs, choisi sur 991 (6 runs),
testé sur les 1 577 décisions des 3 runs `reference` (71 retraits), jamais vues
(`tools/systeme1_apprentissage.py`). AUC de test 0,795, contre 0,742 pour le ressenti seul (peur et
estimation des autres, comme R-seuil).

| Seuil (décidé seul si p < seuil) | Décidé seul (test) | Retraits du LLM manqués (test) |
|---|---|---|
| 0,025 | 25 % | 6 % |
| 0,0275 (choisi en validation, au plus 5 % manqués) | 42 % | 14 % |
| 0,03 | 53 % | 17 % |

Le seuil choisi en validation ne se transpose pas au test : les probabilités sont tassées entre 0,02 et 0,05.
Avec si peu de retraits pour apprendre, le classifieur ne prédit pas encore assez bien les décisions rares
du LLM. Chaque run `reference` en ajoute une vingtaine.
