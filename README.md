# GUICHET : la banque solide qui tombe parce qu'on la croit perdue

Simulation multi-agents d'un bank run dans la vallée fictive de Brévanne. Une population hybride
(agents LLM de deux tailles et agents à règles) circule dans Kafka, et Godot l'affiche.

## Organisation

| Dossier | Contenu |
|---|---|
| `guichet/` | Projet Godot 4.7, la **vue** : scène 3D, HUD, inspection d'agent, leviers (voir `guichet/README.md`) |
| `services/` | `bridge.py` : bridge FastAPI entre Godot (WebSocket `/ws`) et Kafka, routes REST et Swagger sur `/docs`, lance le moteur ou un rejeu à la demande de Godot. `indicateurs.py` : ICC recalculé depuis Kafka et retard (C7). `rejeu.py` : rejeu d'un run enregistré (E3). `archiveur.py` : historique en Parquet et DuckDB (F5). `comparaison.py` : résumés des runs pour Godot |
| `requetes/` | Requêtes SQL reproductibles sur l'historique des runs (scénarios, coût, décisions, mémoire, sources de l'ICC, modèle unique par agent) |
| `lancer.bat`, `arreter.bat` | Démarrage en un double-clic (Kafka, bridge, Godot) et arrêt |
| `archives/` | Maquette Godot d'origine, où la simulation tournait dans Godot |
| `configs/` | Configurations de run (`principal.yaml`, `baseline_regles.yaml`) et catalogue des modèles (`models.yaml`) |
| `schemas/` | Contrat des événements : `envelope/v1.json` et `<type>/v1.json` |
| `docs/` | Contexte commun (B1), hypothèses, événements, architecture |
| `docs/plan.md` | Plan des 8 semaines et avancement |
| `docs/journal/` | Compte rendu détaillé de chaque semaine de travail |
| `docs/README_api_key.md` | Clés API, fournisseurs, modèles, quotas, problèmes rencontrés et dépannage |
| `tools/` | Vérification des contrats et des clés API |
| `engine/` | Moteur de simulation : horloge, monde, Caisse, files, agents à règles, publication Kafka |
| `tests/` | Tests automatiques (`pytest`) |
| `llm/` | Passerelle LLM : fournisseurs gratuits, limites de débit, prompts et validation des réponses |
| `rag/` | Mémoire des agents et RAG : corpus de la vallée, plongements locaux, PostgreSQL + pgvector (`schema.sql`) |

## Démarrage rapide

Double-cliquez sur **`lancer.bat`** : Docker Desktop doit être démarré. Le script lance Kafka et le bridge,
puis ouvre Godot. Dans Godot, choisissez une configuration et cliquez sur **Lancer**.
Pour tout arrêter, double-cliquez sur **`arreter.bat`** (Kafka reste en marche ; `arreter.bat -Kafka`
l'arrête aussi).

## Mise en route détaillée

```powershell
# 1. Kafka + Kafka UI (http://localhost:8080)
docker compose up -d

# 2. Environnement Python (déjà créé dans .venv)
.venv\Scripts\python -m pip install torch==2.11.0 --index-url https://download.pytorch.org/whl/cu128   # GPU NVIDIA (~3 Go)
.venv\Scripts\python -m pip install -r requirements.txt

# 3. Clés API : copier .env.example en .env, puis coller les clés
copy .env.example .env

# 4. Vérifications
.venv\Scripts\python tools\validate_contracts.py   # schémas et configurations
.venv\Scripts\python tools\check_llm.py --list     # modèles disponibles chez chaque fournisseur
.venv\Scripts\python tools\check_llm.py            # un petit appel par modèle déclaré
.venv\Scripts\python -m pytest -q tests            # tests automatiques
```

## Lancer une simulation (sans Godot)

```powershell
# Un run, historique écrit dans data\runs\<run_id>\ (events.jsonl, resume.json, config.json)
.venv\Scripts\python -m engine.run --config principal.yaml

# 5 runs d'un scénario, avec moyenne et dispersion (E2)
.venv\Scripts\python -m engine.run --config scenarios\emergence.yaml --graines 1-5

# Publier dans Kafka, à un rythme lisible (0,5 s réelle par pas de 15 min), avec 40 déposants (C2)
.venv\Scripts\python -m engine.run --config principal.yaml --deposants 40 --puits kafka,jsonl --rythme 0.5

# Sans aucun appel LLM (tous les agents à règles) : pour la mise au point, sans consommer de quota
.venv\Scripts\python -m engine.run --config principal.yaml --sans-llm

# Modes (C4) : synchrone (le pas attend toutes les réponses LLM), asynchrone (il n'en attend aucune),
# hybride (il les attend au plus --echeance secondes ; les réponses en retard s'appliquent à leur arrivée)
.venv\Scripts\python -m engine.run --config scenarios\emergence.yaml --mode asynchrone
.venv\Scripts\python -m engine.run --config scenarios\emergence.yaml --mode hybride --echeance 3
```

## Rejouer et interroger l'historique

```powershell
# Rejouer un run enregistré (E3) : depuis Godot (« Comparer les runs passés », bouton Rejouer), ou :
.venv\Scripts\python -m services.rejeu --run <run_id> --rythme 0.5
.venv\Scripts\python -m services.rejeu --run <run_id> --verifier        # rejeu identique ?
.venv\Scripts\python -m services.indicateurs --verifier <run_id>        # ICC recalculé = ICC du moteur ?

# Historique de tous les runs en SQL (F5) : chaque run est archivé en Parquet à sa fin
.venv\Scripts\python -m services.archiveur --sql requetes\scenarios.sql
.venv\Scripts\python -m services.archiveur --requete "SELECT config, count(*) FROM runs GROUP BY config"
```

Les agents LLM sont actifs par défaut (`llm.actif` dans `configs/principal.yaml`). Un run complet de
140 déposants consomme plusieurs centaines d'appels gratuits : voir `docs/README_api_key.md`.

## Contraintes du sujet

Streaming Apache Kafka · visualisation Godot · LLM 100 % gratuits (Groq, Mistral, Google AI Studio) ·
nombre d'agents paramétrable · RAG obligatoire · modes synchrone et asynchrone · monde fictif.
