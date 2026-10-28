# Architecture

## Composants

```
                ┌──────────────────────── Kafka ─────────────────────────┐
┌──────────┐    │ guichet.events / commands / llm.* / indicators / ...    │    ┌──────────────┐
│ MOTEUR   │◄──►│                                                        │◄──►│ Bridge       │◄──► Godot
│ Python   │    └──────┬──────────────────┬───────────────────┬───────────┘    │ FastAPI      │   (vue + leviers)
│          │           │                  │                   │                └──────────────┘
└────┬─────┘    ┌──────▼──────┐   ┌───────▼────────┐  ┌───────▼────────┐
     │          │ Passerelle  │   │ Indicateurs    │  │ Archiveur      │
     │          │ LLM         │   │                │  │ DuckDB/Parquet │
     │          └──────┬──────┘   └────────────────┘  └────────────────┘
     └─────────────────┴──► PostgreSQL + pgvector (corpus + souvenirs) ; embeddings locaux (GPU)
```

- **Moteur** : la source de vérité. Il porte l'horloge, le monde (lieux, agences, files), la Caisse et les
  agents. Il tourne aussi **sans Godot** pour les runs répétés.
- **Passerelle LLM** (`llm/`) : attribution figée des modèles, limites de débit par modèle, nouveaux
  essais, validation des sorties, incidents, mesure du coût. Elle est appelée **directement par le
  moteur** (bibliothèque, appels en parallèle sur des fils d'exécution), et ses `llm.called` partent dans
  `guichet.llm.responses`.
- **Traitement hybride (C4)** : un seul mécanisme pour tous les appels LLM. Les requêtes d'un pas partent
  en parallèle et le pas les attend **au plus `hybride.echeance_s` secondes réelles**. Une réponse arrivée
  à temps s'applique dans le pas (`decision.made.voie = dans_le_pas`) ; une réponse en retard s'applique au
  pas où elle arrive, datée de l'instant où l'agent a commencé à réfléchir (`voie = en_retard`,
  `t_sim_contexte`). Les deux voies coexistent dans un même run. Le mode synchrone (échéance infinie) et le
  mode asynchrone (échéance nulle) sont les deux cas limites du même code.
- **Système 1 / Système 2** (`engine/systeme1.py`, `systeme1` dans la configuration) : quand un déposant
  LLM a du nouveau, un jugement rapide et local (Système 1) répond à une seule question, « va-t-il retirer
  son argent ? », avec une probabilité. Un « non » assez sûr (confiance ≥ `confiance_min`) est décidé sans
  appel. Un « oui » est **toujours** vérifié par le LLM (Système 2), et une situation ambiguë lui est
  confiée aussi. Pourquoi une seule question : dans le moteur, « attendre » et « rien » ont le même effet,
  seul le retrait change la dynamique (95 % des réponses LLM mesurées étaient « attendre »). Deux moteurs :
  `regle` (R-seuil, la confiance croît avec l'écart entre la pression et le seuil) et `nli` (mDeBERTa-v3
  multilingue NLI, ~280 M de paramètres, en local sur le GPU, ~5 ms par agent, qui lit le carnet en
  français et compare deux hypothèses opposées). En cas de panne du LLM, le Système 1 décide. Le Système
  1 réduit les **rafales** d'appels qui saturaient les 6 appels simultanés : en hybride, 88 % des décisions
  LLM arrivaient en retard, de 3 h 30 simulées en moyenne (mesure du 27/10/2026).
- **Godot** : affichage et interaction uniquement. Il ne calcule rien de ce qui compte pour les résultats.
- **Bridge** (`services/bridge.py`) : application **FastAPI** servie par uvicorn sur le port 8765. Godot s'y
  connecte par WebSocket (`/ws`) : il reçoit le flux Kafka et envoie ses actions (`lancer`, `commande`…),
  vérifiées par des modèles Pydantic. Les mêmes actions existent en routes REST, documentées par Swagger
  (`/docs`). Le bridge lance le moteur ou le rejeu, publie les commandes dans `guichet.commands` (DLQ si
  non conformes) ; il ne calcule rien et ne détient aucune clé API.
- **Service d'indicateurs** (`services/indicateurs.py`) : consommateur Kafka indépendant du moteur. Il
  recalcule l'ICC à partir des instantanés (`icc_flux`), le moyenne sur 2 h (`icc_2h`) et mesure son
  retard (C7). Pendant un rejeu, son ICC recalculé est celui du moteur : la preuve que les indicateurs se
  reproduisent à partir des seules données enregistrées (E3).
- **Archiveur** (`services/archiveur.py`) : à la fin de chaque run, transforme `events.jsonl` en tables
  Parquet ; DuckDB les interroge toutes ensemble (F5). La table `runs` porte l'**étiquette** de la série
  (`--etiquette`), le moteur du Système 1, la latence des appels et le retard des décisions LLM :
  `requetes/etiquettes.sql` compare les séries.
- **Runs interrompus** (`services/reconstitution.py`) : un run arrêté depuis Godot, fermé ou planté n'écrit
  pas son `resume.json`. Le bridge le recalcule depuis `events.jsonl` dès que le moteur s'arrête, puis
  l'archive (`runs.interrompu = true`) ; l'archiveur fait de même pour les anciens runs. La configuration
  est écrite au démarrage du run, et `run.started.code` donne la version du code (commit, empreinte).
- **Rejeu** (`services/rejeu.py`) : republie un run enregistré dans Kafka, pas après pas, sans calcul
  ni appel LLM (E3). Lancé depuis Godot par le bridge.

## Où vivent les données (F3)

| Donnée | Lieu | Volume | Fréquence | Latence | Conservation | Justification |
|---|---|---|---|---|---|---|
| Flux d'événements | **Kafka** | ~50 000 par run | continue | < 100 ms | 7 jours | Diffusion à plusieurs consommateurs découplés, relecture possible pendant le run |
| État courant du monde | **Mémoire du moteur** + topic compacté `guichet.state` | ~150 agents | à chaque pas | immédiate | le run | Le moteur lit et écrit l'état à chaque pas ; le topic compacté donne un instantané à Godot quand il (re)connecte |
| Historique des runs | `events.jsonl` pendant le run, puis **Parquet** (`data/runs/<run_id>/archive/`) lu par **DuckDB** (`data/archive.duckdb`, des vues seulement) | ~2 000 à 10 000 événements par run, des centaines de milliers au total | écriture pendant le run, archivage à la fin, lecture en analyse | secondes | permanente | Requêtes SQL reproductibles entre runs (F5, `requetes/*.sql`) ; colonnes compressées ; aucun serveur à faire tourner |
| Instantanés pour le rejeu | `etats.jsonl` (`data/runs/<run_id>/`) | un par pas, ~15 Ko chacun | écriture pendant le run, lecture au rejeu | — | permanente | Le rejeu (E3) republie les mêmes messages que pendant le run, instantanés compris |
| Base de connaissances et mémoires | **PostgreSQL + pgvector** (conteneur `guichet-postgres`, port 5433, volume `guichet-pg` ; schéma `rag/schema.sql`) | ~25 documents + quelques milliers de souvenirs par run | écriture par lots à chaque pas, lecture à chaque décision LLM | < 50 ms | permanente (corpus et souvenirs de tous les runs) | Recherche sémantique exacte filtrée par run, par agent et par `t_sim` en SQL (A1, F1, C6) ; souvenirs interrogeables après le run (`requetes/souvenirs_postgres.sql`). Le carnet (10 dernières notes) reste en mémoire du moteur |

## Répartition des agents (A5, A6)

| Catégorie | Qui | Modèles | Justification (rôle et expérience) |
|---|---|---|---|
| **Grand modèle** | La boulangère, le retraité, l'entrepreneur, l'employé d'agence : gpt-oss-120b (Groq, 117B). La direction : Gemma 4 31B (Google) | gpt-oss-120b, Gemma 4 31B | Leurs rôles demandent d'arbitrer entre des informations contradictoires : gérer une trésorerie, mobiliser 40 ans d'expérience de la Caisse, informer ou se taire au guichet, décider d'une communication de crise |
| **Petit modèle** | ~75 % des déposants ordinaires | Ministral 8B (Mistral, 80 %), gpt-oss-20b (Groq, 20 %) | Déposants qui reçoivent et relaient l'information sans responsabilité particulière |

**Comparaison de taille à famille égale (A5)** : gpt-oss-120b et gpt-oss-20b sont de la même famille
(même architecture, même entraînement), avec un rapport de taille de 117 à 21. C'est la paire la plus
propre pour isoler l'effet de la taille. Ce sont des modèles « mixture of experts » : on rapportera à la
fois les paramètres totaux et les paramètres actifs (5,1 et 3,6 milliards).
| **Règles** | ~25 % des déposants : les moins exposés à l'information (sociabilité la plus faible) | aucun | Ils sortent peu et conversent peu ; un modèle de seuil suffit à décrire leur réaction aux files observées. Levier de sobriété (E5) |

**Règles d'attribution** :
- L'attribution est **figée au lancement** (graine) et publiée dans `run.started`.
- **Un agent ne change jamais de modèle pendant un run.** En cas d'échec, la passerelle refait un essai sur
  le même modèle. Si l'échec persiste, l'agent décide avec sa règle (`decision.made.type = repli_regles`)
  et un incident est publié.
- La répartition des petits modèles entre fournisseurs est un **choix de charge**. Elle est équilibrée à
  l'intérieur de chaque profil, pour que le fournisseur ne soit pas confondu avec le profil. Une mesure de
  contrôle comparera les décisions d'un même profil selon le fournisseur.
- Vérification après un run (doit renvoyer 0 ligne) :
  ```sql
  SELECT agent_id FROM llm_calls WHERE run_id = ? GROUP BY agent_id HAVING COUNT(DISTINCT modele) > 1;
  ```
- Le modèle auxiliaire (Gemini) n'est attribué à aucun agent. Il ne sert qu'à l'analyse, par exemple pour
  classer les positions des messages.
