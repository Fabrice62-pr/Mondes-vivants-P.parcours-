# Semaine 1 : cadrage, contrats et infrastructure

**Date** : 13 octobre 2026
**Objectif** : poser tout ce qui doit être décidé **avant** d'écrire le moteur : décisions de conception,
contrat des événements, configuration des runs, infrastructure Kafka, hypothèses.
**Statut** : terminée. Les clés API ont été vérifiées et le catalogue de modèles corrigé (voir § 7).

---

## 1. Point de départ

La maquette Godot existante (`guichet/`, environ 2 200 lignes de GDScript) fait tourner **toute** la
simulation dans Godot :
- la Caisse avec sa comptabilité ;
- 3 agences avec leurs files ;
- 140 déposants avec leurs croyances `fear` et `est_others` ;
- les leviers et un indicateur de basculement ;
- des dialogues LLM optionnels via l'API Claude.

Après lecture du sujet (`Projet Guichet.pdf`), trois écarts majeurs sont apparus :

1. **La contrainte G impose des LLM 100 % gratuits.** L'API Claude, payante, est abandonnée.
2. **Kafka, le rejeu, les runs répétés sans affichage et les modes synchrone et asynchrone** (C1, C4, E2, E3)
   sont très difficiles à obtenir si la simulation vit dans Godot.
3. **Il manque la mémoire, le RAG, les agents de tailles différentes et la traçabilité** (A1, A5, F1 à F6).

## 2. Décisions prises

| Décision | Choix | Raison |
|---|---|---|
| Architecture | **Moteur Python = source de vérité** ; Godot = vue et injection d'événements via un bridge WebSocket ↔ Kafka | Runs sans affichage (E2), rejeu (E3), modes sync et async (C4), Kafka et RAG en Python |
| Fournisseurs LLM | **Groq, Mistral et Google AI Studio**, en offres gratuites, en parallèle | Respect de G, et plus de quota disponible en les combinant |
| Taille des modèles (A5) | Uniquement des modèles de **taille publiée** pour les agents | L'hypothèse « taille = capacité » doit reposer sur un chiffre. Gemini n'est pas publié et ne sert donc qu'à l'analyse |
| Grand modèle | Les **5 personnages principaux** : la boulangère, le retraité, l'entrepreneur, l'employé d'agence, la direction | Justifié par le **rôle et l'expérience**, jamais par l'âge (A5 interdit les catégories sociales) |
| Petit modèle | Environ 75 % des déposants ordinaires, répartis entre Llama 8B, Ministral 8B et Gemma 3 12B | La répartition entre fournisseurs est un choix de charge, équilibré à l'intérieur de chaque profil |
| Agents à règles | **25 %** des déposants dans la configuration principale : les moins exposés à l'information | Exigé par A6, levier de sobriété (E5), et justifié par leur rôle dans le réseau d'information |
| Stabilité des modèles | **Un agent ne change jamais de modèle pendant un run** | Cohérence du profil et validité de A5. En cas d'échec : nouvel essai sur le même modèle, sinon décision par la règle et incident tracé |
| Direction | Agit **seule** par défaut ; l'utilisateur peut **forcer** une action depuis Godot | D3. Une action forcée est marquée `auteur = utilisateur` |
| Employé d'agence | Agent LLM qui reçoit une **consigne** (silence ou transparence) et peut s'en écarter | Remplace le levier « employé tenu au silence ». Sert l'exemple A1 (il « élude ») |
| Calendrier simulé | 7 jours du mercredi au mardi, pas de 15 min, article le vendredi à 7 h 30, agences fermées le dimanche | Plusieurs jours pour la mémoire (A1) ; le dimanche laisse mûrir la rumeur avant le lundi |
| Personnages | Pas de nouveau personnage : « le client depuis 40 ans » **est** le retraité | Décision de l'utilisateur |
| Dépôt git | **Aucun dépôt créé pour l'instant**, pas même `git init`. Seul le `.gitignore` est préparé | Décision de l'utilisateur |

## 3. Fichiers créés ou modifiés

Tout se trouve dans `guichet-godot/`.

### Infrastructure
| Fichier | Contenu |
|---|---|
| `docker-compose.yml` | Kafka 3.9 en mode KRaft (un seul nœud, sans Zookeeper), Kafka UI v0.7.2, et un conteneur `kafka-init` qui crée les topics puis s'arrête |
| `.env.example` | Modèle pour les clés `GEMINI_API_KEY`, `GROQ_API_KEY`, `MISTRAL_API_KEY` et `KAFKA_BOOTSTRAP` |
| `.gitignore` | Exclut `.env`, `.venv/`, `data/` et les fichiers générés par Godot |
| `.venv/` | Environnement Python 3.14 avec `pyyaml` et `jsonschema` |

**Topics Kafka créés** :

| Topic | Partitions | Conservation |
|---|---|---|
| `guichet.events`, `guichet.commands`, `guichet.llm.requests`, `guichet.llm.responses`, `guichet.indicators`, `guichet.incidents` | 3 | 7 jours |
| `guichet.dlq` (événements non conformes) | 1 | 30 jours |
| `guichet.state` (état courant) | 1 | compacté |

### Configuration
| Fichier | Contenu |
|---|---|
| `configs/models.yaml` | Les 3 fournisseurs (une URL compatible OpenAI chacun) et 5 modèles d'agents classés en `grand` et `petit` avec leur taille publiée, plus un modèle auxiliaire Gemini réservé à l'analyse |
| `configs/principal.yaml` | **Tous** les paramètres d'un run (C2) : graine, mode, horloge, population, personnages principaux avec leur modèle et leur justification, Caisse, rumeur, leviers, budget LLM |
| `configs/baseline_regles.yaml` | Référence sans LLM (E1) qui hérite de `principal.yaml` avec `part_regles: 1.0` |

### Contrat des événements (F4)
| Fichier | Contenu |
|---|---|
| `schemas/envelope/v1.json` | L'enveloppe commune : `event_id`, `type`, `version`, `run_id`, `seq`, **`t_sim`**, **`t_emit`**, **`t_proc`**, `producer`, **`causes`** (traçabilité) et `payload` |
| `schemas/<type>/v1.json` | **19 types** : `run.started`, `run.ended`, `clock.tick`, `agent.moved`, `conversation.held`, `counter.exchanged`, `belief.updated`, `decision.made`, `queue.changed`, `withdrawal.served`, `caisse.updated`, `caisse.defaulted`, `communique.published`, `command.issued`, `memory.written`, `llm.called`, `incident.raised`, `temporal.violation`, `indicator.computed` |

Points de conception importants :
- **`decision.made.t_sim_contexte`** donne l'instant de la photographie du monde utilisée pour décider,
  distinct du `t_sim` d'application. C'est la base de C3 et C6.
- **`decision.made.documents` et `souvenirs`** enregistrent ce qui a été retrouvé par le RAG (F2) et
  les souvenirs utilisés (A1, D2).
- **`run.started.attribution`** fige le modèle de chaque agent pour tout le run.
- **`llm.called`** porte les tokens, la latence et le statut de chaque appel (E4).
- **`indicator.computed.sources`** remonte aux événements bruts (F6), et **`retard_ms`** mesure le
  décalage (C7).

### Documentation
| Fichier | Contenu | Exigences |
|---|---|---|
| `README.md` (racine) | Organisation du projet et mise en route | — |
| `docs/contexte_commun.md` | Définition observable du contexte commun, indice **ICC = A × C**, accord des positions, critère d'émergence (ICC ≥ 0,6 pendant 4 h), scénarios « émergence » et « absence » | B1, B2, B3 |
| `docs/hypotheses.md` | **H1 à H7**, écrites avant toute mesure, chacune avec sa mesure et ce qui la réfuterait | C5, E1, E5, A3, C8, C7 |
| `docs/evenements.md` | Rôle des topics, clé de partition, **justification de la granularité**, les deux temps, politique de versions, traitement des messages non conformes | C1, C3, F4 |
| `docs/architecture.md` | Composants, **lieu de chaque donnée** avec volume, fréquence, latence et conservation, répartition et attribution des agents | F3, A5, A6 |

### Outils
| Fichier | Rôle |
|---|---|
| `tools/validate_contracts.py` | Vérifie que chaque schéma est valide, que les exemples sont conformes, **qu'un événement faux est rejeté**, et que les configurations sont cohérentes (modèles de la bonne catégorie, justifications présentes, proportions qui somment à 1) |
| `tools/check_llm.py` | Lit `.env` sans jamais afficher les clés. `--list` liste les modèles de chaque fournisseur ; sans option, il fait un petit appel par modèle déclaré et affiche la latence, les tokens et la validité du JSON |

### Modifié
| Fichier | Modification |
|---|---|
| `guichet/README.md` | Version corrigée (Godot 4.7 au lieu de 4.3), avec une note indiquant que la maquette évolue vers l'architecture moteur Python + Kafka |

Rien n'a été modifié dans les scripts GDScript.

## 4. Vérifications effectuées

| Vérification | Résultat |
|---|---|
| `tools/validate_contracts.py` | ✅ 19 types, 4 exemples conformes, événement faux rejeté, 2 configurations cohérentes |
| `docker compose up -d` | ✅ `guichet-kafka` en bonne santé, `guichet-kafka-ui` en marche, `guichet-kafka-init` terminé normalement |
| Topics (`kafka-topics.sh --describe`) | ✅ 8 topics avec les bons partitionnements et les bonnes conservations |
| Kafka UI, http://localhost:8080 | ✅ HTTP 200 |
| `tools/check_llm.py` sans `.env` | ✅ Message clair et sortie en erreur, comme prévu |
| `tools/check_llm.py` avec les clés | ✅ Les 5 modèles répondent avec un JSON valide, après les corrections du § 7 |

Le poste de travail a aussi été vérifié : Python 3.14.3, Docker 29.3.1 avec Compose 5.1, Git 2.56,
Godot 4.7.2 et un GPU RTX 4060 de 8 Go, qui servira aux embeddings du RAG.

## 5. Exigences avancées cette semaine

| Exigence | Avancement |
|---|---|
| B1 | ✅ Définition observable écrite |
| C1 | 🟡 Topics créés et granularité justifiée ; reste à publier depuis le moteur |
| C2 | 🟡 Population dans la configuration ; reste à la lire depuis le moteur |
| C3 | 🟡 Les deux temps sont définis dans l'enveloppe |
| F3 | 🟡 Stockage choisi et justifié |
| F4 | 🟡 Schémas versionnés et validation écrite ; reste la DLQ dans les consommateurs |
| A5, A6 | 🟡 Répartition et attribution définies et justifiées |
| C5, E1, E5 | 🟡 Hypothèses formulées avant la mesure |

## 6. Ce qui reste à faire

**À faire de ton côté** :
1. `copy .env.example .env`, puis coller les 3 clés dans `.env`.
2. Lancer `.venv\Scripts\python tools\check_llm.py --list`, puis `.venv\Scripts\python tools\check_llm.py`.
3. Transmettre la sortie, qui ne contient aucune clé, pour confirmer les identifiants de modèles. Ceux
   de `mistral-large-2411` et `gemma-3-12b-it` sont à vérifier, car les catalogues gratuits changent.
4. Relire et valider les seuils d'émergence (`docs/contexte_commun.md`) et les hypothèses
   (`docs/hypotheses.md`) **avant** les premières mesures.

**Semaine 2** : le moteur Python sans LLM.
- Horloge simulée et mode synchrone.
- Lieux, agences, files et Caisse, portés depuis `caisse.gd` et `agency.gd`. On corrigera au passage la
  perte comptée deux fois au moment du défaut.
- Agents à règles, portés depuis `depositor.gd`.
- Publication dans Kafka avec l'enveloppe, validation et DLQ.
- Lancement en ligne de commande sans affichage.

## 7. Ajout : vérification des clés et correction du catalogue de modèles

Après que les clés ont été collées dans `.env`, `check_llm.py` a révélé trois problèmes.

| Problème | Cause | Correction |
|---|---|---|
| Groq : `HTTP 403, error code: 1010` | Cloudflare, placé devant Groq, bloque l'identifiant par défaut `Python-urllib` | Envoi d'un en-tête `User-Agent: guichet-sim/0.1`. **À reprendre dans la passerelle LLM** |
| Modèles prévus absents | Groq ne sert plus Llama, Mistral ne sert plus Mistral Large, Google ne sert plus Gemma 3 | Nouveau catalogue, ci-dessous |
| Google : HTTP 500 sur Gemma 4 | L'interface compatible OpenAI de Google échoue pour Gemma 4, alors que l'API native fonctionne | Google est appelé par son **API native** (`generateContent`). `check_llm.py` gère maintenant les deux types d'API |

**Nouveau catalogue** (`configs/models.yaml`), uniquement des modèles de taille publiée :

| Catégorie | Modèle | Fournisseur | Taille | Latence mesurée |
|---|---|---|---|---|
| Grand | gpt-oss-120b | Groq | 117 milliards (MoE, 5,1 actifs) | 0,4 à 1,0 s |
| Grand | Gemma 4 31B | Google | 31 milliards | 28 à 33 s |
| Petit | gpt-oss-20b | Groq | 21 milliards (MoE, 3,6 actifs) | 0,3 à 0,7 s |
| Petit | Ministral 8B (2512) | Mistral | 8 milliards | 0,7 à 0,9 s |
| Auxiliaire, aucun agent | gemini-flash-lite-latest | Google | non publiée | 39 à 71 s |

**Quotas gratuits relevés** dans les en-têtes de réponse :
- **Groq** : 1 000 requêtes par jour et 8 000 tokens par minute, **par modèle** ;
- **Mistral** : 188 requêtes et 625 000 tokens par minute ;
- **Google** : quotas non communiqués, et des erreurs 503 (« forte demande ») ce jour-là.

**Nouvelle attribution** (`configs/principal.yaml`) :
- La boulangère, le retraité, l'entrepreneur et l'employé : **gpt-oss-120b**.
- La direction : **Gemma 4 31B**. Elle fait peu d'appels et tolère donc la lenteur de Google.
- Les petits modèles : **80 % Ministral 8B**, qui absorbe le volume, et **20 % gpt-oss-20b**.

**Ce qu'on y gagne pour A5** : gpt-oss-120b et gpt-oss-20b forment une paire de **même famille** avec
un rapport de taille de 117 à 21. C'est la comparaison la plus propre pour isoler l'effet de la taille.

**Risques à suivre** :
- Avec 1 000 requêtes par jour sur Groq, on fera environ 2 à 3 runs complets par jour.
- La configuration E5 « tous sur le grand modèle » devra tourner à **population réduite**.
- Google est lent et instable : en cas d'échec, la direction décidera par sa règle, et chaque repli sera
  compté comme incident (A4).

## 8. Commandes utiles

```powershell
cd "C:\Users\LEGION\Desktop\EIGSI 5A\projet de parcours\guichet-godot"
docker compose up -d                                  # démarrer Kafka
docker compose down                                   # l'arrêter
.venv\Scripts\python tools\validate_contracts.py      # vérifier les contrats
.venv\Scripts\python tools\check_llm.py               # vérifier les clés et les modèles
```
