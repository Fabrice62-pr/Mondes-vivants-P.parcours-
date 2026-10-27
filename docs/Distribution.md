# Distribution de GUICHET sur deux PC

> **Statut : plan de travail, rien n'est encore implémenté.** Ce document décrit l'état cible et la
> marche à suivre. Il intègre les quatre modifications prévues : le bridge en **FastAPI**, la mémoire des
> agents dans **PostgreSQL**, le traitement **hybride** synchrone/asynchrone, et un **modèle LLM local**
> qui s'ajoute aux LLM en ligne (Groq, Mistral, Google).

---

## 1. L'idée en une phrase

Un PC **calcule** la simulation, l'autre la **pilote, l'affiche et fait tourner le modèle local**. Les
deux ne se parlent **que par Kafka**, sur le réseau local. Aucun programme d'un PC n'appelle directement
un programme de l'autre.

```
 PC 1 « SERVEUR »                           PC 2 « PILOTAGE + LLM LOCAL »
 ────────────────                           ─────────────────────────────
 Docker                                     Godot (visualisation 3D)
   ├─ Kafka      (port 9094 réseau)           │ ws://127.0.0.1:8765/ws
   ├─ Kafka UI   (port 8080)                  ▼
   └─ PostgreSQL + pgvector                 Bridge FastAPI (+ Swagger)
 Lanceur (nouveau service)                  Service d'indicateurs (ICC)
 Moteur de simulation
   ├─ RAG : e5 (GPU du PC 1)                ┌─ LE MODÈLE LOCAL EST ICI ─┐
   ├─ mémoire des agents → PostgreSQL       │ Travailleur LLM local     │
   └─ passerelle LLM                        │ Ollama (GPU du PC 2)      │
        ├─ en ligne : HTTPS vers            └─────────────▲─────────────┘
        │  Groq, Mistral, Google                          │
        └─ local : n'exécute aucun modèle,                │
           envoie les requêtes par Kafka ─────────────────┘
           guichet.llm.requests  →
           guichet.llm.responses ←
 Historique des runs (DuckDB)

 Tous les échanges entre PC 1 et PC 2 passent par Kafka (réseau local).
```

> Sur le PC 1, « local » désigne seulement la branche de la passerelle qui **envoie** les requêtes au
> modèle local. Le modèle (Ollama et ses poids) n'est installé et ne calcule **que sur le PC 2**.

### 1.1 Trois rôles logiques, deux PC physiques

Le système se découpe en **trois rôles**. Chacun peut être placé sur n'importe quel PC, puisqu'ils ne
communiquent que par Kafka :

| Rôle | Ce qu'il porte | Placement recommandé |
|---|---|---|
| **Serveur** | Kafka, PostgreSQL, lanceur, moteur, passerelle LLM, plongements e5 | PC 1 (meilleure carte NVIDIA) |
| **Pilotage** | Bridge FastAPI, service d'indicateurs, Godot | PC 2 |
| **Calcul LLM local** | Ollama et le travailleur LLM local | **PC 2** si sa carte NVIDIA a au moins 6 Go de mémoire vidéo, sinon PC 1 |

**Pourquoi ce découpage ?**

- Le PC 1 porte ce qui est lourd côté données : Kafka, la base, le moteur et les plongements sur le GPU.
  Choisir le PC qui a la meilleure carte NVIDIA. Ce PC-ci a une RTX 4060 Laptop (8 Go) : à comparer
  avec celle du binôme.
- Le modèle local occupe 5 à 6 Go de mémoire vidéo. Sur le PC 1, il entrerait en concurrence avec les
  plongements e5 sur les mêmes 8 Go. Sur le PC 2, il utilise un GPU qui, sinon, ne sert qu'à Godot. La
  charge est ainsi **mieux répartie**, et la démonstration montre un vrai calcul sur chaque machine.
- Le PC 2 ne détient **aucune clé API** et n'a **pas besoin de Docker**. Le modèle local n'a pas besoin
  de clé non plus.
- Tout passe par Kafka, y compris les requêtes au modèle local : c'est l'architecture par événements
  que le projet veut démontrer. Les topics `guichet.llm.requests` et `guichet.llm.responses` existent
  déjà ; le premier était prévu pour ce genre d'usage et n'a encore jamais servi.

> **Si le PC 2 n'a pas de carte NVIDIA suffisante**, on lance Ollama et le travailleur sur le PC 1. Le
> code ne change pas : seul le `.env` diffère (§ 7.6). C'est l'intérêt de passer par Kafka plutôt que par
> un appel HTTP direct.

---

## 2. Ce qui change par rapport à aujourd'hui

| Aujourd'hui (un seul PC) | Cible (deux PC) | Pourquoi |
|---|---|---|
| Le bridge (`websockets`) **lance lui-même le moteur** en sous-processus | Le bridge **publie une demande** dans Kafka ; le **lanceur** du PC 1 démarre le moteur | Le moteur doit tourner sur le PC 1, le bridge sur le PC 2 |
| Le bridge écoute sur `127.0.0.1:8765` | Bridge FastAPI (uvicorn) sur le port 8765, route WebSocket `/ws` et routes REST | Modification n° 2 |
| Kafka annonce `localhost:9092` | Un listener supplémentaire `RESEAU` annonce `IP_DU_PC1:9094` | Sinon le PC 2 se fait renvoyer vers son propre localhost |
| Mémoire des agents dans Chroma, en mémoire vive | PostgreSQL + pgvector sur le PC 1 | Modification n° 3 |
| `mode: synchrone` **ou** `asynchrone` | `mode: hybride` (les deux en même temps) | Modification n° 1 |
| Uniquement des LLM en ligne (Groq, Mistral, Google), appelés en HTTPS par la passerelle | LLM en ligne **et** un modèle local servi par Ollama, joint par Kafka | Modification n° 4 |
| `guichet.llm.requests` est créé mais inutilisé | Il transporte les requêtes vers le modèle local | Modification n° 4 |
| « Comparer » et « Rejouer » lisent `data/runs` sur la machine du bridge | Le **lanceur** du PC 1 répond, car c'est là que sont les données | Les runs sont enregistrés sur le PC 1 |
| `lancer.ps1` démarre tout | `lancer.ps1 -Role serveur`, `-Role pilotage` ou `-Role local` (défaut), avec ou sans modèle local | Chaque PC ne démarre que sa part |

> **Règle importante :** le mode **local** (tout sur un seul PC) doit continuer de fonctionner. C'est
> celui qu'on utilise pour développer et pour les tests ; le mode à deux PC sert à l'intégration et à la
> démonstration. Toute adresse vient du fichier `.env`, jamais du code. Le moteur doit aussi pouvoir
> tourner **sans** modèle local : les agents qui lui sont attribués décident alors par règles, avec un
> incident (§ 6.6).

---

## 3. Modification n° 2 : le bridge FastAPI et le lanceur

### 3.1 Le bridge FastAPI (PC 2) : `services/bridge.py`

Il garde son rôle de relais : il ne calcule rien et ne détient aucune clé.

| Route | Rôle |
|---|---|
| `WS /ws` | Flux Kafka → Godot (comme aujourd'hui), plus les messages JSON actuels (`lister`, `lancer`, `arreter`, `rejouer`, `comparer`, `commande`) pour que Godot change le moins possible |
| `GET /sante` | État du bridge, de la connexion à Kafka et du modèle local (battement `llm.status`, § 6.4) |
| `GET /configurations` | Liste des configurations disponibles |
| `POST /runs` | Demande de lancement d'un run, publiée dans Kafka |
| `DELETE /runs/actif` | Demande d'arrêt |
| `POST /runs/{run_id}/rejeu` | Demande de rejeu |
| `GET /runs/comparaison` | Résumés des derniers runs, demandés au lanceur par Kafka |
| `POST /commandes` | Commande vers le moteur (garantie, plafond…), publiée dans `guichet.commands` |
| `GET /docs` | Documentation Swagger générée automatiquement (utile en soutenance) |

Pour le démarrer :

```powershell
.venv\Scripts\python -m uvicorn services.bridge:app --host 127.0.0.1 --port 8765
```

- L'écoute de Kafka reste dans un **thread** séparé, car `confluent-kafka` est bloquant. Les messages sont
  repassés à la boucle asyncio de FastAPI, comme aujourd'hui.
- Les messages entrants sont validés par des modèles **Pydantic**.
- Dépendances à ajouter dans `requirements.txt` : `fastapi` et `uvicorn`, avec des versions figées.
- Godot : dans [guichet/scripts/bridge_client.gd](../guichet/scripts/bridge_client.gd), l'URL devient
  `ws://127.0.0.1:8765/ws`. Godot et le bridge restent sur le même PC, donc pas d'adresse réseau ici.

### 3.2 Le lanceur (PC 1) : nouveau `services/lanceur.py`

Il reprend le code de lancement qui est aujourd'hui dans le bridge (`_lancer`, `_rejouer`, `_arreter`,
`_surveiller`), ainsi que la lecture de `data/runs` pour la comparaison.

- Il consomme le topic **`guichet.runs`** avec un `group.id` fixe (`lanceur`) : un seul lanceur traite
  chaque demande.
- Il n'exécute **qu'un run à la fois**. Une nouvelle demande arrête le run en cours, comme aujourd'hui.
- Il répond dans le même topic par un événement `run.status`.

### 3.3 Nouveaux contrats d'événements

| Élément | Contenu |
|---|---|
| Topic `guichet.runs` | 1 partition, conservation 1 jour. À ajouter dans le service `kafka-init` de `docker-compose.yml` |
| `schemas/run.requested/v1.json` | `demande_id`, `action` (`lancer` \| `arreter` \| `rejouer` \| `comparer`), `parametres` (config, graine, déposants, rythme, llm, mode, run_id…) |
| `schemas/run.status/v1.json` | `demande_id`, `etat` (`accepte` \| `refuse` \| `termine` \| `erreur` \| `resultat`), `detail`, `runs` (pour `comparer`) |
| [docs/evenements.md](evenements.md) | Documenter les deux nouveaux types |

Les événements du modèle local (`llm.requested`, `llm.completed`) sont décrits au § 6.4.

Le parcours d'un lancement :

```
Godot ──ws──► bridge (PC 2) ──run.requested──► guichet.runs ──► lanceur (PC 1)
                                                                  │ démarre engine.run
Godot ◄──ws── bridge ◄── guichet.events (run.started…) ◄── moteur ┘
              bridge ◄── guichet.runs (run.status : accepte, termine) ◄── lanceur
```

---

## 4. Modification n° 3 : la mémoire des agents dans PostgreSQL (PC 1)

### 4.1 Ce qui change et ce qui ne change pas

- **Change :** le **stockage** et la **recherche** des souvenirs et du corpus. Chroma est retiré et
  remplacé par PostgreSQL avec l'extension **pgvector**.
- **Ne change pas :** le **calcul** des plongements (embeddings). Le modèle `intfloat/multilingual-e5-small`
  et PyTorch restent nécessaires : PostgreSQL range et cherche les vecteurs, il ne les calcule pas.
- **Ne change pas :** le contrat de la classe `Memoire`, utilisée par le moteur. Il faut garder
  `ecrire(...)`, `rappeler(...)`, `fermer()`, `n_souvenirs` et `_en_attente`, que le moteur lit
  directement dans [engine/moteur.py](../engine/moteur.py).

### 4.2 Le service Docker

À ajouter dans `docker-compose.yml` :

```yaml
  postgres:
    image: pgvector/pgvector:pg17
    container_name: guichet-postgres
    ports:
      - "127.0.0.1:5432:5432"        # accessible depuis le PC 1 seulement : le moteur y est
    environment:
      POSTGRES_USER: guichet
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: guichet
    volumes:
      - guichet-pg:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U guichet"]
      interval: 5s
      retries: 20

volumes:
  guichet-pg:
```

### 4.3 Les tables

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE corpus (
    doc_id     TEXT PRIMARY KEY,
    titre      TEXT NOT NULL,
    texte      TEXT NOT NULL,
    empreinte  TEXT NOT NULL,          -- corpus et modèle de plongement : on reconstruit si elle change
    embedding  vector(384) NOT NULL
);

CREATE TABLE souvenirs (
    run_id      TEXT NOT NULL,
    memoire_id  TEXT NOT NULL,
    agent_id    TEXT NOT NULL,
    t_sim       INTEGER NOT NULL,
    importance  REAL NOT NULL,
    nature      TEXT NOT NULL,
    doc_id      TEXT,
    texte       TEXT NOT NULL,
    embedding   vector(384) NOT NULL,
    PRIMARY KEY (run_id, memoire_id)
);
CREATE INDEX ON souvenirs (run_id, agent_id, t_sim);
CREATE INDEX ON souvenirs USING hnsw (embedding vector_cosine_ops);
```

Le rappel s'écrit directement en SQL. Le filtre temporel C6, « jamais un souvenir du futur », est dans
la requête :

```sql
SELECT memoire_id, texte, t_sim, importance, nature, doc_id, 1 - (embedding <=> %(v)s) AS similarite
FROM souvenirs
WHERE run_id = %(run)s AND agent_id = %(agent)s AND t_sim <= %(t)s
ORDER BY embedding <=> %(v)s
LIMIT 12;
```

Le reclassement (similarité + importance + fraîcheur) et la limite d'un seul souvenir de réflexion
restent en Python, comme aujourd'hui.

### 4.4 Choix à valider

- **Les souvenirs sont conservés après le run** : c'est le principal gain, car on peut les interroger en
  SQL pour l'analyse. Mais un agent **ne rappelle que les souvenirs de son run** (`WHERE run_id = ...`).
  Sinon, la reproductibilité et la règle C6 seraient cassées.
- **Le carnet** (les 10 dernières notes) : nous recommandons de le **laisser en mémoire Python**. C'est une
  mémoire de travail relue à chaque pas, et la placer dans PostgreSQL ralentirait le moteur sans rien
  apporter, puisque son contenu est déjà dans les souvenirs et dans les événements.
- **L'historique des runs** (DuckDB et Parquet) : nous recommandons de ne pas y toucher, car ce n'est pas
  la mémoire des agents.
- **Dépendances :** `psycopg[binary]` et `pgvector` dans `requirements.txt`. `chromadb` peut être retiré.
- **Configuration :** `POSTGRES_URL=postgresql://guichet:<mot_de_passe>@localhost:5432/guichet` dans le
  `.env` du PC 1.
- **Tests :** adapter [tests/test_rag.py](../tests/test_rag.py). Le `PlongementFaux` reste utilisable, et
  les tests ont besoin d'une base PostgreSQL de test.

---

## 5. Modification n° 1 : le traitement hybride (moteur, PC 1)

### 5.1 Le principe recommandé : un pas avec échéance

À chaque pas de 15 minutes simulées :

1. Les agents à règles décident tout de suite.
2. Les requêtes des agents LLM partent **en parallèle**, vers les API en ligne comme vers le modèle local.
3. Le pas **attend au plus `echeance_s` secondes réelles**.
   - Les réponses arrivées **à temps** s'appliquent **dans ce pas** : c'est la part synchrone.
   - Les **retardataires** s'appliquent au pas où elles arrivent, datées de l'instant où l'agent a
     commencé à réfléchir (`t_sim_contexte`) : c'est la part asynchrone.
4. Le pas se termine. Un modèle lent agit donc naturellement plus tard qu'un modèle rapide (H2).

Les deux mécanismes fonctionnent **en même temps, dans le même run**. Le moteur fait déjà quelque chose
de ce genre pour la direction : on généralise ce mécanisme.

Le modèle local se prête bien à ce mode : sa latence dépend de la file d'attente sur un seul GPU, et non
d'un quota. Quand beaucoup d'agents locaux réfléchissent au même pas, une partie de leurs réponses
arrive en retard, ce qui alimente la part asynchrone.

### 5.2 La configuration proposée

Dans `configs/principal.yaml` :

```yaml
mode: hybride                 # synchrone | asynchrone | hybride
hybride:
  echeance_s: 3               # attente maximale des réponses LLM dans un pas
  rythme_s: 0.5               # durée réelle minimale d'un pas
  # Variante possible : imposer une voie par catégorie d'agent
  # voies: { principal: synchrone, petit: echeance, direction: asynchrone, employe: synchrone }
```

### 5.3 Fichiers touchés

- [engine/moteur.py](../engine/moteur.py) : `_decider`, `_decider_llm`, `_recevoir_reflexions`, `_direction`.
- [engine/run.py](../engine/run.py) : accepter `--mode hybride` et régler le rythme.
- `schemas/run.started/v1.json` : ajouter `hybride` aux modes possibles. Si le changement n'est pas
  compatible, passer en v2.
- Éventuellement `decision.made` : un champ `voie` (`dans_le_pas` ou `en_retard`) pour mesurer la part
  synchrone et la part asynchrone.
- Le lanceur et le bridge : transmettre `mode: hybride`.
- Les tests de [tests/test_simulation.py](../tests/test_simulation.py).

### 5.4 Point d'attention

La part asynchrone dépend de la **latence réelle** des API et du GPU local. Deux runs avec la même graine
peuvent donc différer. Il faut l'écrire dans [docs/hypotheses.md](hypotheses.md). Le **rejeu** reste
exact, car il republie les événements enregistrés sans appeler les LLM.

---

## 6. Modification n° 4 : le modèle LLM local (PC 2 par défaut)

### 6.1 Le principe

Le modèle local est un **fournisseur de plus** dans le catalogue, au même titre que Groq, Mistral et
Google. Les règles de la passerelle ne changent pas :

- un agent reçoit son modèle au début du run (publié dans `run.started`) et le garde jusqu'au bout ;
- en cas d'échec : un nouvel essai sur le **même** modèle, puis repli sur la règle de l'agent, avec un
  incident. **Jamais** de bascule d'un modèle local vers un modèle en ligne, ni l'inverse ;
- chaque essai produit un `llm.called` (tokens, latence, statut), avec `fournisseur: local`.

Seul le **transport** diffère : au lieu d'un appel HTTPS, la passerelle publie la requête dans Kafka et
attend la réponse.

```
moteur + passerelle (PC 1)                                        travailleur LLM local (PC 2)
   │ llm.requested (requete_id, prompt, delai_ms)                          │
   ├──────────────► guichet.llm.requests ─────────────────────────────────►│
   │                                                                       │ POST 127.0.0.1:11434/api/chat
   │                                                                       │ (Ollama, GPU du PC 2)
   │◄────────────── guichet.llm.responses ◄────────────────────────────────┤
   │ llm.completed (call_id, essai, texte, tokens, latence_modele_ms)      │
   ▼
 llm.called publié par le moteur, comme pour les autres fournisseurs
```

### 6.2 Le serveur de modèle : Ollama

- **Ollama** (gratuit, installeur Windows) sert le modèle sur le GPU NVIDIA. Le travailleur utilise son
  **API native** (`POST /api/chat`), qui accepte une graine (`seed`), le format JSON et la désactivation
  du mode « réflexion » (`think: false`). L'appel est dans `llm/local.py`.
- Ollama écoute sur **`127.0.0.1:11434` seulement**. Il n'est **pas** ouvert sur le réseau : seul le
  travailleur, sur la même machine, l'appelle.
- Réglages à mettre dans les variables d'environnement Windows du PC qui l'héberge :
  - `OLLAMA_NUM_PARALLEL=2` : deux requêtes traitées en même temps ;
  - `OLLAMA_KEEP_ALIVE=-1` : le modèle reste chargé en mémoire vidéo pendant toute la séance (sinon, le
    premier appel après 5 minutes d'inactivité prend plusieurs secondes de chargement).

**Le choix du modèle** doit respecter les règles du catalogue :

- une **taille publiée** (hypothèse A5) ; sinon il ne peut être attribué à aucun agent ;
- il doit tenir dans la mémoire vidéo disponible, en version quantifiée Q4 (environ 5 Go pour 8 milliards
  de paramètres) ;
- il doit produire un **JSON valide** de façon fiable, en français.

| Candidat (à vérifier avec `tools/check_llm.py`) | Taille publiée | Mémoire vidéo (Q4) | Remarque |
|---|---|---|---|
| Qwen3 8B | 8,2 Md | ~5,5 Go | Bon en JSON ; désactiver le mode « réflexion » (`think: false`) |
| Llama 3.1 8B Instruct | 8 Md | ~5 Go | Llama n'est plus servi par Groq : le local le réintroduit dans l'étude |
| Gemma 3 4B / Qwen3 4B | ~4 Md | ~3 Go | Si le GPU du PC hôte a moins de 6 Go |

> **Intérêt pour l'étude :** un modèle d'environ 8 milliards de paramètres, local, se compare
> directement à `ministral-8b`, qui a la même taille mais tourne en ligne. Les deux ne diffèrent que par
> le fournisseur et la latence : c'est une bonne paire pour isoler l'effet de la latence (H2) de celui de
> la taille (A5, H4).

### 6.3 Le catalogue et la configuration

Dans [configs/models.yaml](../configs/models.yaml) :

```yaml
providers:
  local:
    base_url: http://127.0.0.1:11434      # Ollama (OLLAMA_URL dans .env a priorité)
    env_key: null                         # aucune clé
    api: ollama
    transport: kafka                      # kafka | http (voir ci-dessous)

models:
  qwen3-8b-local:
    provider: local
    id: qwen3:8b                          # nom du modèle dans Ollama (ollama pull qwen3:8b)
    params_b: 8.2
    tier: petit
    max_tokens: 300
    json: true
    timeout_s: 20                         # inclut l'aller-retour Kafka et la file d'attente du GPU
    limites: { rpm: 40 }                  # pas de quota, mais un seul GPU : on évite d'empiler les requêtes
    options: { seed_par_run: true }       # graine fixée à partir de celle du run (§ 6.7)
```

Dans [configs/principal.yaml](../configs/principal.yaml), une part des agents « petit modèle » lui est
attribuée :

```yaml
population:
  repartition_petits: { ministral-8b: 0.60, gpt-oss-20b: 0.15, qwen3-8b-local: 0.25 }
```

**Les deux transports.** La variable `LLM_LOCAL_TRANSPORT` du `.env` du PC 1 choisit le chemin :

| Valeur | Chemin | Quand l'utiliser |
|---|---|---|
| `kafka` | Passerelle → `guichet.llm.requests` → travailleur → Ollama | Mode à deux PC (démonstration) ; aussi possible en mode local |
| `http` | Passerelle → Ollama directement, sur `127.0.0.1:11434` | Mode local, pour développer sans démarrer le travailleur |

Dans les deux cas, la passerelle reçoit une `Reponse` identique : le reste du moteur ne voit pas la
différence.

### 6.4 Le travailleur LLM local : nouveau `services/llm_local.py`

- Il consomme **`guichet.llm.requests`** avec un `group.id` fixe (`llm-local`). Les requêtes sont
  publiées avec la clé `agent_id`.
- Pour chaque requête, il appelle Ollama, puis publie un `llm.completed` dans `guichet.llm.responses`.
- Il **ignore** une requête dont le délai `delai_ms`, compté à partir de sa réception, est dépassé :
  après l'arrêt d'un run ou une panne, il ne doit pas traiter un arriéré de requêtes devenues inutiles.
- Toutes les 5 secondes, il publie un battement **`llm.status`** dans `guichet.llm.responses` : modèle
  prêt, requêtes en cours, traitées, expirées. Le bridge l'affiche dans `/sante`, et la passerelle
  l'attend au démarrage du run (§ 6.6).
- Il ne lit et n'écrit **aucune clé API** et ne connaît ni le moteur ni PostgreSQL.

**Nouveaux contrats d'événements**

| Élément | Contenu |
|---|---|
| `schemas/llm.requested/v1.json` | `requete_id` (un par essai), `modele`, `systeme`, `utilisateur`, `temperature`, `max_tokens`, `json`, `graine`, `think`, `delai_ms` |
| `schemas/llm.status/v1.json` | `modele`, `pret`, `en_cours`, `traitees`, `expirees` (battement du travailleur) |
| `schemas/llm.completed/v1.json` | `requete_id`, `statut` (`ok` \| `erreur` \| `expire`), `texte`, `tokens_entree`, `tokens_sortie`, `latence_modele_ms`, `erreur` |
| `schemas/llm.called/v1.json` | Ajouter `local` aux valeurs de `fournisseur` ; champ facultatif `latence_modele_ms` |
| [docs/evenements.md](evenements.md) | Documenter les deux nouveaux types et le nouvel usage de `guichet.llm.requests` |
| [services/archiveur.py](../services/archiveur.py) | `guichet.llm.responses` contiendra aussi des `llm.completed` : les archiver ou les ignorer explicitement |

`latence_modele_ms` (mesurée par le travailleur) et `latence_ms` (mesurée par la passerelle) permettent de
séparer le temps de calcul du modèle et le temps de transport par Kafka. Les deux mesures sont prises
chacune sur une seule horloge : l'écart d'horloge entre les PC (§ 7.5) ne les fausse pas.

### 6.5 Côté passerelle (PC 1)

- [llm/fournisseurs.py](../llm/fournisseurs.py) : une nouvelle classe `FournisseurKafka` avec la même
  méthode `appeler(modele, systeme, utilisateur, temperature, delai_s) -> Reponse`. Elle publie le
  `llm.requested`, puis attend le `llm.completed` correspondant pendant `delai_s` au plus. Sans réponse,
  elle renvoie `Reponse(0, erreur="timeout")`, qui suit le chemin d'échec habituel.
- Un **consommateur unique** par run écoute `guichet.llm.responses` dans un thread séparé (groupe
  `moteur-<run_id>`, lecture à partir de la fin) et range les réponses par `requete_id`. Il doit être
  abonné **avant** la première requête.
- `disponible()` renvoie vrai si le dernier battement `llm.status` date de moins de 20 secondes.
- [llm/passerelle.py](../llm/passerelle.py) : aucune règle ne change. Il faut seulement construire
  `FournisseurKafka` (ou le fournisseur HTTP) pour le fournisseur `local`, selon `LLM_LOCAL_TRANSPORT`.
- [tools/check_llm.py](../tools/check_llm.py) : savoir tester le modèle local (latence, JSON valide).

### 6.6 Si le modèle local n'est pas disponible

Le comportement suit celui d'une clé API absente aujourd'hui :

- les agents attribués au modèle local décident **par règles** ;
- un seul incident `erreur_api` est publié (« modèle local indisponible : travailleur absent »), puis les
  suivants sont comptés sans être republiés ;
- le run continue. On ne réattribue **jamais** ces agents à un modèle en ligne, ce qui casserait A5 et la
  comparaison entre runs.

### 6.7 Points d'attention

- **Reproductibilité :** contrairement aux API en ligne, Ollama accepte une **graine** (`seed`). En la
  dérivant de la graine du run et de l'`agent_id`, les réponses du modèle local deviennent beaucoup plus
  stables d'un run à l'autre. C'est à signaler dans [docs/hypotheses.md](hypotheses.md).
- **Débit :** un seul GPU traite environ 30 à 50 tokens par seconde pour un modèle de 8 milliards. Avec
  `OLLAMA_NUM_PARALLEL=2`, compter 3 à 6 secondes par réponse. Il faut limiter la part des agents locaux
  (25 % des petits modèles pour commencer) et la mesurer au premier test de charge.
- **Coût (E4, E5) :** 0 € et aucun quota, mais les tokens et la latence sont comptés comme pour les
  autres fournisseurs. On peut ajouter le temps GPU au bilan du run.
- **Contrainte G :** le sujet autorise « des LLM 100 % gratuits, par API ou en local ». Le modèle local
  la satisfait. Les poids sont téléchargés une fois, avant la séance, avec `ollama pull`.
- **Modèle auxiliaire (option) :** le classifieur de l'indicateur de contexte commun utilise aujourd'hui
  Google, lent et instable. Il pourrait passer sur le modèle local, puisqu'il n'est attribué à aucun agent
  et ne touche donc pas à A5.

---

## 7. Configuration réseau

### 7.1 Le réseau

- Les deux PC doivent être sur le **même réseau local**.
- **Le Wi-Fi de l'école** (eduroam…) isole souvent les appareils entre eux, et cela ne marchera
  probablement pas. Solutions :
  - le **partage de connexion d'un téléphone** (le plus simple) ;
  - un **câble Ethernet** entre les deux PC, ou un petit switch ou routeur.
- Déclarer ce réseau en **« Privé »** dans Windows, sur les deux PC (Paramètres > Réseau > Propriétés).
- Le modèle local n'a pas besoin d'Internet pendant la séance. Les LLM en ligne, si : le réseau choisi
  doit donner accès à Internet au **PC 1**.

### 7.2 L'adresse IP du PC 1

```powershell
ipconfig        # repérer « Adresse IPv4 » de la carte connectée, par ex. 192.168.43.20
```

Elle peut changer à chaque connexion. Il faut la vérifier avant chaque séance et mettre à jour le `.env`
**des deux PC**.

### 7.3 Kafka (`docker-compose.yml`, PC 1)

On ajoute un listener `RESEAU` sur le port 9094. Le listener `HOTE` (`localhost:9092`) reste en place pour
les programmes du PC 1 et pour le mode local.

```yaml
  kafka:
    ports:
      - "9092:9092"
      - "9094:9094"
    environment:
      KAFKA_LISTENERS: INTERNE://:19092,CONTROLLER://:9093,HOTE://:9092,RESEAU://:9094
      KAFKA_ADVERTISED_LISTENERS: INTERNE://kafka:19092,HOTE://localhost:9092,RESEAU://${IP_SERVEUR:-127.0.0.1}:9094
      KAFKA_LISTENER_SECURITY_PROTOCOL_MAP: CONTROLLER:PLAINTEXT,INTERNE:PLAINTEXT,HOTE:PLAINTEXT,RESEAU:PLAINTEXT
```

`docker compose` lit automatiquement `IP_SERVEUR` dans le `.env` du dossier du projet. Après chaque
changement d'IP : `docker compose up -d` pour recréer le conteneur.

### 7.4 Le pare-feu (PC 1, PowerShell en administrateur)

```powershell
New-NetFirewallRule -DisplayName "GUICHET Kafka et Kafka UI" -Direction Inbound -Protocol TCP `
    -LocalPort 9094,8080 -Action Allow -Profile Private
```

PostgreSQL (5432) **n'est pas ouvert** : seul le moteur, sur le PC 1, l'utilise. Ollama (11434) **n'est
pas ouvert non plus**, sur aucun des deux PC : seul le travailleur, sur la même machine, l'appelle. Le
PC 2 n'a besoin d'aucune règle entrante, puisqu'il se connecte lui-même à Kafka.

### 7.5 L'heure des deux PC

Le service d'indicateurs (PC 2) mesure un retard (`retard_ms`) entre l'émission d'un événement par le
moteur (horloge du PC 1) et sa propre publication (horloge du PC 2). Si les horloges diffèrent d'une
seconde, la mesure est fausse d'une seconde.

- Synchroniser l'heure sur les deux PC (PowerShell en administrateur) : `w32tm /resync`.
- Dans le rapport, préciser que le retard mesuré entre deux machines inclut l'écart d'horloge. On peut
  aussi faire calculer le retard sur une seule machine, par exemple en comparant à l'heure de réception
  par Kafka.
- Les requêtes au modèle local n'en dépendent pas : leur délai `delai_ms` est compté par le travailleur à
  partir de leur réception.

### 7.6 Les fichiers `.env`

| Variable | PC 1 (serveur) | PC 2 (pilotage + LLM local) |
|---|---|---|
| `GROQ_API_KEY`, `MISTRAL_API_KEY`, `GEMINI_API_KEY` | **oui** | non (aucune clé sur le PC 2) |
| `KAFKA_BOOTSTRAP` | `localhost:9092` | `192.168.x.x:9094` (IP du PC 1) |
| `IP_SERVEUR` | `192.168.x.x` (sa propre IP) | — |
| `POSTGRES_PASSWORD`, `POSTGRES_URL` | **oui** | — |
| `LLM_LOCAL_TRANSPORT` | `kafka` (ou `http` en mode local) | — |
| `LLM_LOCAL` | `0` | `1` : ce PC démarre le travailleur LLM local |
| `OLLAMA_URL`, `LLM_LOCAL_MODELE` | — | `http://127.0.0.1:11434`, `qwen3:8b` |
| `GUICHET_ROLE` | `serveur` | `pilotage` |
| `GODOT_EXE` | — | chemin de Godot sur ce PC |

Si le modèle local tourne finalement sur le PC 1, on inverse simplement `LLM_LOCAL` et on déplace
`OLLAMA_URL`.

Pensez à mettre à jour [.env.example](../.env.example) avec les nouvelles variables, sans valeurs.

### 7.7 La sécurité

Kafka est en `PLAINTEXT`, sans authentification : n'importe quel appareil du réseau peut lire et écrire.
Les prompts envoyés au modèle local y circulent en clair ; ils ne contiennent aucune clé ni donnée
personnelle réelle. C'est acceptable sur un réseau fermé (partage de connexion, câble), **pas** sur un
Wi-Fi public. Une phrase dans le rapport suffit, et l'ajout de SASL/TLS peut figurer comme amélioration
possible.

---

## 8. Scripts de lancement

[lancer.ps1](../lancer.ps1) reçoit un paramètre `-Role` :

| Rôle | PC | Ce qui démarre |
|---|---|---|
| `local` (défaut) | un seul PC | Tout, comme aujourd'hui, plus Ollama et le travailleur si `LLM_LOCAL=1` |
| `serveur` | PC 1 | Docker (Kafka, Kafka UI, PostgreSQL), puis le lanceur ; Ollama et le travailleur si `LLM_LOCAL=1` |
| `pilotage` | PC 2 | Vérification que Kafka sur le PC 1 répond ; si `LLM_LOCAL=1`, vérification qu'Ollama répond et que le modèle est téléchargé, puis le travailleur ; enfin le bridge FastAPI, le service d'indicateurs et Godot |

Le démarrage du modèle local est donc piloté par `LLM_LOCAL`, et non par le rôle : il peut suivre l'un
ou l'autre PC sans modifier les scripts.

Il faut prévoir un `.bat` par rôle (`lancer-serveur.bat`, `lancer-pilotage.bat`) pour garder le
double-clic. [arreter.ps1](../arreter.ps1) suit la même logique (il arrête le travailleur ; Ollama peut
rester ouvert).

---

## 9. Mise en route, pas à pas

### Une seule fois

1. **Sur les deux PC :** `git pull`, recréer le `.venv`, puis
   `.venv\Scripts\python -m pip install -r requirements.txt`.
2. **PC 1 :** Docker Desktop, PyTorch CUDA (voir `requirements.txt`), un `.env` avec les clés et
   `IP_SERVEUR`, et la règle de pare-feu (§ 7.4).
3. **PC 2 :** Godot 4.7 et un `.env` avec `KAFKA_BOOTSTRAP`, `GODOT_EXE` et `LLM_LOCAL=1`. Docker n'est
   pas nécessaire.
4. **PC qui héberge le modèle local (PC 2 par défaut) :** installer Ollama, régler
   `OLLAMA_NUM_PARALLEL` et `OLLAMA_KEEP_ALIVE` (§ 6.2), puis télécharger le modèle **avant** la séance :
   ```powershell
   ollama pull qwen3:8b
   ollama run qwen3:8b "Réponds en JSON : {\"ok\": true}"     # premier essai sur le GPU
   ```

### À chaque séance

1. Connecter les deux PC au même réseau et le déclarer « Privé ».
2. **PC 1 :** `ipconfig`, puis reporter l'IP dans `IP_SERVEUR` (PC 1) et dans `KAFKA_BOOTSTRAP` (PC 2).
3. Synchroniser l'heure sur les deux PC : `w32tm /resync`.
4. **PC 1 :** `lancer-serveur.bat`. Attendre « Kafka prêt » et « Lanceur prêt ».
5. **PC 2 :** vérifier la connexion :
   ```powershell
   Test-NetConnection 192.168.x.x -Port 9094     # TcpTestSucceeded : True
   ```
   et ouvrir `http://192.168.x.x:8080` dans un navigateur : Kafka UI doit s'afficher.
6. **PC 2 :** `lancer-pilotage.bat`. Attendre « Modèle local prêt », puis Godot s'ouvre ; choisir une
   configuration, puis « Lancer ».
7. **Vérifier que tout est bien distribué :**
   - dans Kafka UI, des messages apparaissent dans `guichet.runs`, puis dans `guichet.events` ;
   - dans Kafka UI, des `llm.requested` apparaissent dans `guichet.llm.requests`, suivis de
     `llm.completed` dans `guichet.llm.responses` ;
   - sur le PC 1, `data/logs` contient un journal `moteur-...log`, et l'activité GPU monte dans le
     Gestionnaire des tâches (plongements e5) ;
   - sur le PC 2, l'activité GPU monte aussi (modèle local), les agents bougent dans Godot, et
     `http://127.0.0.1:8765/docs` affiche Swagger ; `/sante` indique le modèle local comme disponible.

---

## 10. Problèmes fréquents

| Symptôme | Cause probable | Que faire |
|---|---|---|
| `Test-NetConnection` échoue | Pare-feu, réseau « Public » ou Wi-Fi qui isole les appareils | § 7.1 et § 7.4 ; essayer le partage de connexion |
| Kafka UI s'affiche, mais le bridge ne reçoit rien | `IP_SERVEUR` faux ou ancien : Kafka annonce une mauvaise adresse | Corriger `.env` sur le PC 1, puis `docker compose up -d` |
| Le run ne démarre pas | Le lanceur n'est pas démarré sur le PC 1 | Lire `data/logs` sur le PC 1 ; le bridge doit recevoir un `run.status` |
| `retard_ms` négatif ou énorme | Horloges des PC désynchronisées | `w32tm /resync` sur les deux PC |
| Le moteur s'arrête aussitôt | PostgreSQL pas prêt, mauvais `POSTGRES_URL` ou clés manquantes | Journal du moteur sur le PC 1 ; `docker ps` |
| « Comparer » ne renvoie rien | Les runs sont sur le PC 1, et le lanceur ne répond pas | Vérifier le lanceur et le topic `guichet.runs` |
| Incident « modèle local indisponible » | Travailleur non démarré, ou Ollama fermé | `LLM_LOCAL=1` dans le `.env` du bon PC ; `ollama list` ; battements `llm.status` dans `guichet.llm.responses` |
| Des `llm.requested` sans `llm.completed` | Le travailleur n'est pas abonné, ou rejette des requêtes expirées | Journal du travailleur ; vérifier l'heure des deux PC (§ 7.5) |
| Beaucoup de `timeout` sur le modèle local | GPU saturé : trop d'agents locaux ou modèle déchargé | Réduire sa part dans `repartition_petits` ; `OLLAMA_KEEP_ALIVE=-1` ; vérifier qu'Ollama utilise bien le GPU (`ollama ps`) |
| `json_invalide` fréquents sur le modèle local | Modèle trop petit ou mode « réflexion » actif | `json: true` dans le catalogue ; `think: false` pour Qwen3 ; essayer un autre candidat (§ 6.2) |

---

## 11. Répartition du travail entre binômes

Chacun développe et teste **en mode local** sur son PC. On ne passe à deux PC qu'à l'intégration.

| Phase | Toi | Ton binôme |
|---|---|---|
| **0. Préparation** | Ajouter le binôme comme collaborateur GitHub ; créer un `CLAUDE.md` commun | Cloner le dépôt, recréer le `.venv` et le `.env`, lancer `pytest` ; installer Ollama et tester les modèles candidats sur son GPU |
| **1. En parallèle** | `feature/fastapi` : bridge FastAPI, lanceur, topic `guichet.runs`, schémas, `bridge_client.gd` | `feature/postgres` : service PostgreSQL, `rag/memoire.py` réécrit, tests RAG |
| **2. Modèle local** | Choix du modèle avec `check_llm.py` ; entrée du catalogue ; transport `http` dans la passerelle | `feature/llm-local` : travailleur `services/llm_local.py`, `FournisseurKafka`, schémas `llm.requested` et `llm.completed`, archiveur |
| **3. Réseau** | `lancer.ps1` et `arreter.ps1` avec les rôles et `LLM_LOCAL`, fichiers `.bat` | `feature/reseau` : listener Kafka `RESEAU`, `.env.example`, procédure pare-feu |
| **4. Premier test à deux PC** | PC 2 (pilotage + modèle local) | PC 1 (serveur), ou l'inverse selon les GPU |
| **5. Hybride** | `feature/hybride` (moteur, run, schémas), à partir de la branche FastAPI fusionnée | Tests de charge (part des agents locaux, latence du GPU) et mesure des retards sur deux PC ; mise à jour de la documentation |
| **6. Démonstration** | Répétition complète à deux PC, sur le réseau prévu pour la soutenance | Idem |

**Règles communes**

- On ne travaille jamais directement sur `main` : on passe par des branches, puis des Pull Requests
  relues par l'autre.
- Ordre de fusion : `feature/postgres`, puis `feature/fastapi`, puis `feature/llm-local`, puis
  `feature/reseau`, puis `feature/hybride`.
- Dans `requirements.txt`, chacun ajoute ses dépendances dans **sa propre section commentée**, pour
  limiter les conflits.
- Le contrat de `Memoire` (§ 4.1), le format de `run.requested` et `run.status` (§ 3.3) et celui de
  `llm.requested` et `llm.completed` (§ 6.4) sont fixés **avant** de commencer.
- Avant chaque Pull Request : `git pull origin main`, puis `pytest`, puis un test avec `lancer.bat` en
  mode local.

---

## 12. Décisions encore ouvertes

- [ ] Quel PC est le serveur, c'est-à-dire lequel a le meilleur GPU NVIDIA ?
- [ ] Sur quel PC tourne le modèle local : PC 2 (recommandé, § 1.1) ou PC 1 ? Cela dépend de la mémoire
      vidéo de la carte du binôme.
- [ ] Quel modèle local (§ 6.2) : Qwen3 8B, Llama 3.1 8B, ou un modèle de 4 milliards si le GPU est
      limité ?
- [ ] Quelle part des agents « petit modèle » lui attribuer (25 % pour commencer) ?
- [ ] Le classifieur auxiliaire passe-t-il de Google au modèle local (§ 6.7) ?
- [ ] Sur quel réseau se fera la soutenance : partage de connexion, câble ou Wi-Fi de l'école ?
- [ ] Hybride : une échéance seule (§ 5.1), ou aussi des voies imposées par catégorie d'agent ?
- [ ] Le carnet reste-t-il en mémoire Python, comme recommandé (§ 4.4) ?
- [ ] Les souvenirs doivent-ils être conservés indéfiniment dans PostgreSQL, ou purgés au-delà de N runs ?
