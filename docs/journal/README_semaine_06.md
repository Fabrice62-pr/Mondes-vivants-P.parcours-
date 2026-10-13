# Semaine 6 : la mémoire des agents et le RAG

**Date** : 13 octobre 2026
**Objectif** : donner aux agents LLM une mémoire propre (A1) et une base de connaissances interrogée par
recherche sémantique (F1), sans jamais leur laisser voir une information du futur ou qu'ils n'ont pas
reçue (C6).
**Statut** : code terminé, testé sans réseau et en conditions réelles. **Reste à valider à l'écran.**

---

## 1. Ce qui change pour l'utilisateur

- Dans l'inspection d'un habitant LLM, trois nouvelles rubriques :
  - les **souvenirs qui lui sont revenus** au moment de sa dernière décision ;
  - les **documents consultés**, avec leur score de similarité ;
  - ses **derniers souvenirs**, avec leur nature (observation, conversation, guichet, réflexion).
- Le panneau de gauche compte les **souvenirs écrits**.
- Une **violation temporelle** (C6) s'afficherait en rouge dans le journal. Aucune n'a été observée.
- Installation : `chromadb`, `sentence-transformers` et PyTorch avec CUDA (environ 3 Go, voir
  `requirements.txt`). Le modèle de plongement (environ 470 Mo) se télécharge au premier run.

## 2. Ce que retient un agent, et comment il s'en souvient

| Élément | Contenu | Où |
|---|---|---|
| **Carnet** (mémoire courte, semaine 5) | Les 10 dernières choses apprises, datées | En mémoire, dans le prompt |
| **Souvenirs** (mémoire longue) | Tout ce que l'agent a appris (article lu, communiqué reçu, conversation entendue, file vue, échange au guichet, agence fermée) et ses propres décisions | Chroma, collection du run en mémoire vive ; événement `memory.written` |
| **Corpus de la vallée** | 22 documents publics antérieurs au run : histoire et statuts de la Caisse, garantie, rumeur de 1998, faillite du Val-Morin en 2009, lieux, économie | `rag/corpus.yaml` ; Chroma, sur disque (`data/rag/chroma`) |

**Au moment de décider**, l'agent :
1. part de ce qu'il vient de vivre (ses 3 dernières notes) ;
2. cherche ses souvenirs les plus proches, **uniquement les siens et datés d'avant la décision** (filtre
   dans la requête à Chroma) ;
3. les reclasse par similarité + importance + fraîcheur (demi-vie de 24 h simulées), comme dans
   « Generative Agents » (Park et al., 2023), en gardant **au plus un souvenir de ses propres décisions** ;
4. y ajoute les 2 documents du corpus les plus proches.

Le tout entre dans son prompt, et `decision.made` cite les souvenirs (`souvenirs`) et les documents
(`documents`, avec `t_sim_doc` pour un article ou un communiqué publié pendant le run).

**Importance des souvenirs** : article et agence fermée 0,9 ; communiqué et retrait plafonné 0,8 ;
question au guichet 0,7 ; file vue de 0,4 à 0,9 selon sa longueur ; conversation 0,5 ; sa propre
décision 0,3.

**Garde-fou C6** : après chaque décision, le moteur vérifie qu'aucun souvenir ne vient du futur et
qu'aucun document publié pendant le run n'est cité par un agent qui ne l'a pas reçu. Sinon, il publie
un `temporal.violation`.

## 3. Décisions de conception

| Décision | Choix | Raison |
|---|---|---|
| Plongements | **En local**, `intfloat/multilingual-e5-small` sur le GPU | Des milliers de souvenirs par run : par API, ils puiseraient dans le même quota que les décisions. En local, ils sont aussi reproductibles et gratuits |
| Souvenirs | Collection Chroma **en mémoire vive**, propre au run | Un run interrompu ne laisse rien sur le disque ; les souvenirs restent dans les événements (rejeu, semaine 7) |
| Documents publiés pendant le run | Pas dans le corpus : l'agent ne les connaît que par ses souvenirs | Un agent qui n'a pas lu l'article ne doit pas le retrouver par RAG (C6) |
| Requête de rappel | Les 3 dernières notes de l'agent | Une question fixe (« faut-il retirer ? ») ramenait toujours la même brochure rassurante (voir § 5) |
| Souvenirs de réflexion | Au plus un par rappel, importance 0,3 | Sinon l'agent ressasse sa décision précédente (voir § 5) |
| Agents à règles | Pas de mémoire | Ils ne lisent pas de prompt (sobriété, E5) |

## 4. Fichiers créés et modifiés

| Fichier | Rôle |
|---|---|
| `rag/corpus.yaml` | **Nouveau.** 22 documents de la vallée, équilibrés entre rassurants, inquiétants et neutres |
| `rag/plongement.py` | **Nouveau.** Plongements locaux (e5, GPU) ; version factice pour les tests |
| `rag/memoire.py` | **Nouveau.** Corpus et souvenirs dans Chroma ; rappel filtré par agent et par date, reclassé |
| `engine/moteur.py` | Écriture des souvenirs (`memory.written`), rappel avant chaque décision LLM (déposants, employé, direction), citations dans `decision.made`, garde-fou C6, bilan `rag` dans le résumé |
| `llm/prompts.py` | Souvenirs et documents dans les prompts |
| `llm/passerelle.py` | Attente croissante après un 429 sans délai indiqué (voir § 5) |
| `configs/principal.yaml` | Section `rag` |
| `guichet/scripts/*.gd` | Souvenirs dans l'inspection, compteur, violations temporelles |
| `requirements.txt` | `chromadb`, `sentence-transformers` ; installation de PyTorch |
| `tests/test_rag.py` | **Nouveau** : 5 tests |

## 5. Ce que les essais réels ont appris

Scénario `emergence`, 20 déposants, 4 jours, graine 1.

| Essai | Souvenirs | Décisions avec souvenirs | ICC max | Défaut |
|---|---|---|---|---|
| Semaine 5, sans mémoire | — | — | 0,49 | J3 vendredi 13:15 |
| Mémoire, première version | 160 | 99 / 102 | **0,03** | aucun |
| Mémoire, après corrections | 79 | 34 / 36 | 0,51 | J3 vendredi 16:45 |

**Deux biais trouvés au premier essai, et corrigés** :
1. **Un document dominait** : la brochure « Conseil aux épargnants » (« retirer dans la précipitation
   fait perdre les intérêts ») était rappelée dans 92 décisions sur 102. La requête de rappel commençait
   par « faut-il retirer mon argent ? », qui l'attirait toujours. La requête part maintenant de ce que
   l'agent vient de vivre : les documents rappelés se diversifient (aucun au-dessus de 23 rappels sur 36).
2. **Les agents ressassaient** : 102 souvenirs sur 160 étaient leurs propres décisions (« j'ai décidé
   d'attendre »), et ils occupaient toutes les places de rappel. L'agent restait enfermé dans sa
   décision précédente. Un seul souvenir de réflexion par rappel désormais.

**Leçon pour le rapport** : la mémoire et le corpus ne sont pas neutres. Ce qu'un agent se rappelle, et
comment on le lui fait rappeler, change l'issue du run. Ces choix doivent être déclarés, et la
campagne de la semaine 8 mesurera leur effet.

**Mistral saturé** : au deuxième essai, Mistral a renvoyé 19 fois `429 backend_out_of_capacity`, une
saturation de ses serveurs (pas notre quota), sans délai d'attente indiqué. La passerelle refaisait
aussitôt son second essai. Elle attend maintenant 2, 4, 8 puis 16 s entre les essais sur un 429, sans
entamer les essais prévus pour les vraies erreurs.

### Run complet

Scénario `emergence`, 140 déposants, 7 jours, graine 1, mémoire active (run `emergence-g1-20261013-155951`,
chiffres obtenus avec l'archiveur de la semaine 7).

| Mesure | Semaine 5 (sans mémoire, sans connaissance commune) | Semaine 6 (mémoire) |
|---|---|---|
| Appels LLM | 1 004 | **460** |
| Tokens | 667 000 | 417 000 |
| Replis sur la règle | 17 | 2 |
| Échecs de quota gpt-oss-20b | 12 | **0** (le pas attend le quota) |
| Décisions « retirer » / « attendre » | 18 / 961 | 37 / 410 |
| ICC max | 0,40 | 0,57 |
| Défaut | J6 lundi 09:15 | J4 samedi 15:45 |
| Souvenirs écrits | — | 848 |
| Violations temporelles | — | **0** |
| Durée réelle | 500 s | 584 s |

Il y a moins d'appels parce que le défaut arrive plus tôt et que le run s'arrête 4 h après.

**Documents rappelés** : « La direction de la Caisse » (305 fois sur 459 décisions), l'article (246),
« Les grands emprunteurs de la vallée » (179), « Les groupes de discussion en ligne » (115). Le premier
domine parce que presque tous les agents ont reçu le communiqué de la direction, que leur requête de
rappel contient donc le mot « direction ». Le rapprochement a du sens, mais la **qualité de la
récupération** reste à évaluer sur un échantillon de décisions (F2, semaine 8).

**Gemma** : 1 réussite sur 5 appels (erreurs 500 de Google). La direction s'est surtout repliée sur sa
règle.

## 6. Vérifications

| Vérification | Résultat |
|---|---|
| Tests automatiques | ✅ **43 tests** passent, dont 5 pour la mémoire (corpus, filtre par agent et par date, importance et fraîcheur, run complet qui cite ses sources sans violation, garde-fou C6) et 1 pour l'attente après un 429. Un échec isolé, non reproduit en 9 lancements, laisse soupçonner un test instable (probablement lié au fil d'exécution de la direction) |
| Runs réels | ✅ 0 rejet en DLQ, 0 violation temporelle |
| Vue Godot | ✅ rejeu sans fenêtre : souvenirs rappelés, documents consultés et derniers souvenirs dans l'inspection |

## 7. Limites connues

- Le corpus est écrit à la main : son équilibre entre documents rassurants et inquiétants est un choix
  de modélisation, à déclarer.
- Pas de souvenirs « résumés » (réflexions de haut niveau à la Generative Agents) : seulement les
  souvenirs bruts et les décisions.
- Les personnages de la direction et de l'employé n'ont pas de carnet : seulement leurs souvenirs et le
  corpus.

## 8. Suite

**Semaine 7** : mode asynchrone (C4), rejeu d'un run (E3), archiveur DuckDB/Parquet et service
d'indicateurs.
