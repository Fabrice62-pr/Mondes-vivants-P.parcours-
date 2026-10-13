# Semaine 2 : le moteur Python sans LLM

**Date** : 13 octobre 2026
**Objectif** : un moteur de simulation autonome qui tourne sans Godot. Il porte l'horloge simulée, la carte,
la Caisse, les files d'attente et des agents à règles, et publie des événements conformes dans Kafka.
C'est aussi la **référence sans LLM** exigée par E1.
**Statut** : terminée. 20 tests automatiques passent ; le circuit Kafka a été vérifié de bout en bout.

---

## 1. Résultat en bref

```powershell
.venv\Scripts\python -m engine.run --config principal.yaml --graines 1-5
```

- Un run de 7 jours simulés avec 143 déposants dure environ **4 secondes**.
- Chaque run produit 2 000 à 8 000 événements, **tous conformes** aux schémas (0 rejet).
- Les deux scénarios de B3 se **distinguent nettement** : 5 émergences sur 5 contre 0 sur 5 (§ 6).
- La configuration principale se trouve dans la **zone de bascule** : l'issue varie selon la graine (§ 6).

## 2. Décisions de conception

| Décision | Choix | Raison |
|---|---|---|
| Source de la carte | Nouveau fichier `configs/monde.json`, **partagé par le moteur et Godot** | Les deux doivent utiliser exactement les mêmes lieux et coordonnées |
| Identifiants de lieux | Ceux de Godot (`bourg`, `ag_marche`, `zone`, `cafe`…) | Aucune table de correspondance à maintenir. La v1 des schémas a été finalisée en conséquence, **avant tout run archivé** |
| Domiciles | Le moteur tire un point dans le **quartier** de l'agent ; Godot le placera à la maison la plus proche | Les maisons de Godot sont générées par le moteur de rendu, avec un tirage que Python ne peut pas reproduire |
| Déplacements | Un événement par **changement de lieu**, avec l'instant d'arrivée prévu | Granularité justifiée en C1. Godot interpolera le trajet |
| Pas de temps | 15 minutes simulées ; journée de 7 h à 21 h ; agences ouvertes de 9 h à 17 h du lundi au samedi | Plusieurs jours sont nécessaires pour la mémoire (A1) ; le dimanche fermé fait mûrir la rumeur |
| Guichet | 5 minutes par client, soit 3 clients par pas et par agence | Assez lent pour que des files visibles se forment |
| Publication des croyances | `belief.updated` seulement si la peur ou l'estimation varie d'**au moins 0,05** | Environ 600 événements par run au lieu d'environ 90 000 |
| Publication des décisions | `decision.made` seulement quand l'**intention change** (rien, attendre, retirer) | La dernière décision de chaque agent reste consultable (D2) sans saturer le flux |
| État courant | `state.snapshot` à chaque pas dans le topic compacté `guichet.state` | Godot pourra se (re)synchroniser à tout moment (F3) |
| Identifiants d'événements | Tirés d'un générateur à graine | Deux runs de même graine produisent exactement la même trace : c'est la base du rejeu (E3) |
| Paramètres | **Tous** dans la configuration : profils, traits des personnages, règles, routine, guichet, article | C2 : aucun paramètre de run n'est écrit en dur dans le code |
| LLM | `llm.actif: false` : tous les agents décident par règles | Les LLM arrivent en semaine 6. L'attribution des modèles est déjà codée et testée |
| Mode asynchrone | Refusé avec un message explicite | Prévu en semaine 10 |

## 3. Le modèle porté depuis Godot

La logique de `guichet/scripts/` (`caisse.gd`, `agency.gd`, `depositor.gd`, `main.gd`) a été réécrite en
Python et adaptée au temps simulé en jours.

### Déroulement d'un pas de 15 minutes

1. **Commandes** : celles de Godot (Kafka) et celles programmées dans le scénario.
2. **Article** : publié à l'heure prévue (vendredi 7 h 30).
3. **Tic d'horloge**.
4. **Arrivées** des agents en déplacement. Ceux qui arrivent à une agence rejoignent la file, ou rentrent
   chez eux si elle a fermé entre-temps.
5. **Routine** : les sorties dépendent de la sociabilité ; la boulangère va travailler à la boulangerie.
   **Employé** : déjeune au café et peut y parler. **Conversations** : les agents présents au même endroit,
   y compris dans une file, échangent deux par deux.
6. **Perception** : files visibles à moins de 50 m, humeur des personnes présentes, passage à la Maison
   de la presse, oubli.
7. **Décisions** des déposants.
8. **Guichets** : service des clients, échanges au guichet, défaut éventuel.
9. **Direction** : examine la situation à 8 h et à 14 h.
10. **Publication** : variations de files et de croyances, instantané de l'état, calcul de l'ICC.

### Règles identifiées (D2)

Chaque décision indique la règle appliquée (`regle_id`) et une justification chiffrée, par exemple :
*« pression 0.71 > seuil 0.62 (peur 0.55, estime que les autres retirent 0.48, couverture par la garantie 0 %) »*.

| Règle | Agent | Ce qu'elle fait |
|---|---|---|
| `R-seuil` | Déposant | La pression à retirer dépasse le seuil personnel : il part à l'agence |
| `R-vigilance` | Déposant | La pression dépasse 60 % du seuil : il reste inquiet et attend |
| `R-calme` | Déposant | La pression est sous 60 % du seuil |
| `R-file` | Déposant | Une file visible fait monter vite l'estimation des retraits des autres, et l'apaise lentement |
| `R-ambiance` | Déposant | L'inquiétude des personnes présentes au même endroit se transmet |
| `R-conversation` | Déposant | L'interlocuteur s'aligne sur la peur du locuteur, plus vite quand la nouvelle est mauvaise |
| `R-oubli` | Déposant | L'inquiétude retombe lentement si rien ne l'entretient |
| `R-consigne` | Employé | Au guichet, il élude ou répond selon la consigne de la direction |
| `R-fuite-cafe` | Employé | Au café, au-delà de 8 % de dépôts retirés, il peut confier que les retraits s'accumulent |
| `R-direction` | Direction | File ou retraits au-delà d'un seuil : elle publie un communiqué factuel, au plus un toutes les 24 h |

### Bug corrigé : la perte comptée deux fois au défaut

Dans la maquette Godot, quand le défaut survenait **pendant** le service d'un client,
`finalize_default()` fixait sa perte, puis `on_served()` l'**ajoutait une seconde fois**. Le moteur Python
solde ce client une seule fois. Un test vérifie maintenant la **conservation de l'argent** pour chaque
déposant : dépôt initial = retiré + remboursé par la garantie + perdu + restant.

## 4. Fichiers créés ou modifiés

### Moteur (`engine/`)
| Fichier | Rôle |
|---|---|
| `config.py` | Chargement des configurations avec héritage (`herite:`), empreinte de configuration, chargement du catalogue et de la carte |
| `horloge.py` | Horloge simulée : jour, jour de la semaine, heure, ouverture des agences, veille et sommeil |
| `caisse.py` | Comptabilité : réserves, prêts, fonds propres, défaut, solvabilité, pertes réelles |
| `monde.py` | Lieux, quartiers, distances, tirage des domiciles |
| `agents.py` | Déposant (croyances, situation, pression à retirer) et agents institutionnels (employé, direction) |
| `population.py` | Construction de la population et **attribution figée des modèles** (A5, A6) |
| `evenements.py` | Enveloppe, **validation (F4)**, routage par topic, clé de partition, DLQ ; trois sorties : Kafka, fichier JSONL, mémoire |
| `moteur.py` | La simulation synchrone : routine, conversations, perception, décisions, guichets, direction, commandes |
| `indicateurs.py` | Calcul de l'ICC (fonction pure, réutilisable par le futur service d'indicateurs) |
| `textes.py` | Phrases des agents à règles, communiqués, questions et réponses au guichet |
| `run.py` | Lanceur en ligne de commande : une ou plusieurs graines, population, durée, sorties, rythme |

### Configuration
| Fichier | Modification |
|---|---|
| `configs/monde.json` | **Nouveau** : lieux et quartiers partagés avec Godot |
| `configs/principal.yaml` | Ajout des profils, des traits des personnages, des sections `guichet`, `routine`, `regles`, `scenario`, `article`, et de `llm.actif` ; **étalonnage** de l'oubli et de la force de la rumeur |
| `configs/scenarios/emergence.yaml` | **Nouveau** : rumeur forte (0,8), 30 % de connectés, sans garantie |
| `configs/scenarios/absence.yaml` | **Nouveau** : rumeur faible (0,2), garantie à 100 000, communiqué factuel le vendredi à 8 h |

### Schémas
| Modification | Raison |
|---|---|
| Identifiants de lieux alignés sur Godot | Une seule nomenclature |
| `run.started.attribution` : ajout de `nom`, `depot`, `connecte`, `quartier`, `domicile` | Godot pourra créer et placer les agents à partir de ce seul événement |
| **Nouveau** `article.published` | La parution de l'article n'avait pas de type ; il deviendra aussi un document pour le RAG |
| **Nouveau** `state.snapshot` | L'état courant publié dans `guichet.state` |

### Tests (`tests/`, 20 tests)
| Fichier | Ce qui est vérifié |
|---|---|
| `test_caisse_horloge.py` | Retraits, défaut, solvabilité, pertes réelles ; calendrier, ouverture des agences, dimanche fermé |
| `test_population.py` | Population fixée sans toucher au code (C2), 25 % d'agents à règles choisis parmi les moins sociables (A6), catégories de modèles et répartition 80/20 (A5), **attribution identique à graine égale**, aucun LLM quand `llm.actif` est faux |
| `test_simulation.py` | Tous les événements conformes (F4), **déterminisme** (E3), `t_sim` croissant et `seq` unique (C3), **conservation de l'argent**, défaut sans double perte, **émergence contre absence** (B3), commande invalide isolée sans arrêt (F4), effet de la garantie (D3), mode asynchrone refusé |

### Autres
| Fichier | Modification |
|---|---|
| `requirements.txt` | **Nouveau** : PyYAML 6.0.3, jsonschema 4.26.0, confluent-kafka 2.16.0, pytest 9.1.1 |
| `tools/validate_contracts.py` | Utilise le chargeur du moteur et vérifie aussi les deux scénarios |
| `docs/evenements.md` | Nouveaux types, seuil de publication des croyances, volumes mesurés |
| `README.md` | Commandes pour lancer une simulation et les tests |

## 5. Étalonnage

Les paramètres de la maquette Godot étaient prévus pour un temps réel en secondes. Transposés tels quels
dans des pas de 15 minutes, **il ne se passait rien** : la peur d'un déposant informé disparaissait en
environ 14 heures, et le vendredi soir tout le monde avait déjà oublié l'article.

| Paramètre | Avant | Après | Effet |
|---|---|---|---|
| `oubli_peur_par_pas` | 0,010 | **0,002** | Une peur de 0,55 dure environ 3 jours sans entretien |
| `oubli_estime_par_pas` | 0,012 | **0,003** | Idem pour l'estimation des retraits des autres |
| `rumeur.force` (principal) | 0,55 | **0,65** | Place la configuration principale dans la zone de bascule |

Balayage de la force de la rumeur, sur 8 graines par valeur :

| Force | Défauts | Part retirée selon la graine |
|---|---|---|
| 0,55 | 0 sur 8 | 0 à 2 % |
| 0,60 | 0 sur 8 | 1 à 7 % |
| **0,65** | **1 sur 8** | **3 à 15 %** |
| 0,70 | 0 sur 8 | 6 à 14 % |
| 0,75 | 2 sur 8 | 9 à 15 % |

> Cet étalonnage porte sur la **référence à règles**, avant toute mesure de l'apport des LLM. Il ne touche
> ni aux hypothèses H1 à H7 ni aux seuils d'émergence, qui restent ceux fixés en semaine 1.

## 6. Mesures

### Configuration principale (5 graines, 143 déposants, 7 jours)

| Graine | Issue | Part retirée | File max | ICC max | Émergence |
|---|---|---|---|---|---|
| 1 | pas de défaut | 10,5 % | 5 | 0,04 | non |
| 2 | pas de défaut | 5,9 % | 3 | 0,04 | non |
| 3 | pas de défaut | 5,3 % | 1 | 0,01 | non |
| 4 | pas de défaut | 6,0 % | 2 | 0,02 | non |
| 5 | **défaut** vendredi 15 h 45 (Caisse solvable) | 15,0 % | 5 | 0,64 | **oui** |

Part retirée : 8,5 % ± 4,2 %. Une seule configuration donne des issues opposées selon la graine. C'est
l'équilibre fragile décrit par le sujet, et cela montre déjà pourquoi E2 impose plusieurs runs par
configuration.

### Scénarios B3 (5 graines chacun)

| Scénario | Défaut | Émergence | ICC max | Heure du défaut |
|---|---|---|---|---|
| **Émergence** | 5 sur 5 | 5 sur 5 | 0,65 à 0,81 | vendredi entre 10 h et 13 h 30 |
| **Absence** | 0 sur 5 | 0 sur 5 | 0,00 | — |

Tous les défauts concernent une Caisse **solvable** : ce sont des paniques autoréalisatrices.

**Observation à creuser** : pendant le balayage, à une force de 0,75, deux défauts sont survenus **sans**
émergence du contexte commun. Une banque peut donc tomber par une érosion progressive des retraits, sans
croyance collective « tout le monde retire ». L'ICC distingue ces deux mécanismes, ce qui sera un bon point
de discussion pour B3.

### Kafka de bout en bout

Run de 60 déposants sur 3 jours, publié dans Kafka. Pendant le run, deux messages ont été envoyés sur
`guichet.commands` comme le fera Godot : une commande « garantie » valide et un message mal formé.

| Topic | Messages | Vérification |
|---|---|---|
| `guichet.events` | 1 355 | Identique au fichier `events.jsonl` |
| `guichet.state` | 256 | Un instantané par pas |
| `guichet.commands` | 2 | — |
| `guichet.dlq` | 1 | Le message mal formé, avec ses 9 erreurs de validation |

La commande valide a été **appliquée en cours de run**. Son événement `command.issued` porte dans `causes`
l'identifiant du message reçu. Le message invalide a été **isolé sans arrêter la simulation**.

## 7. Exigences avancées

| Exigence | Avancement |
|---|---|
| C1 | ✅ Les événements du monde transitent par Kafka, avec une granularité justifiée et mesurée |
| C2 | ✅ `--deposants N` ou la configuration, sans toucher au code |
| C3 | ✅ Horloge explicite ; chaque événement porte `t_sim` et `t_emit` (`t_proc` sera posé par les consommateurs) |
| C4 | 🟡 Mode synchrone fait ; mode asynchrone en semaine 10 |
| F4 | ✅ Validation à l'émission et à la réception, DLQ, simulation jamais interrompue |
| A6 | 🟡 Agents à règles faits ; ils cohabiteront avec les LLM en semaine 6 |
| E1 | 🟡 La référence à règles existe et tourne |
| E2 | 🟡 Runs en série avec moyenne et écart-type (`--graines 1-5`) |
| E3 | 🟡 Déterminisme vérifié à graine égale ; le rejeu viendra en semaine 10 |
| B2, B3 | 🟡 ICC calculé dans le moteur ; contraste émergence/absence démontré. L'affichage dans Godot viendra en semaines 4 et 5 |
| D2 | 🟡 Règle et justification portées par chaque décision |
| D3 | 🟡 Commandes reçues et appliquées ; les boutons Godot viendront en semaines 4 et 5 |

## 8. Limites connues

- **Un seul personnage « employé »**, au guichet du Bourg. Aux deux autres agences, les échanges sont
  publiés sans être attribués à un personnage, mais la consigne s'applique à tout le personnel.
- La **direction à règles** ne connaît qu'un ton (factuel). Les tons rassurant et vague restent
  accessibles par commande. La direction LLM choisira son ton elle-même.
- L'**ICC** est calculé à chaque pas sur l'état instantané. La fenêtre glissante de 2 h prévue dans
  `docs/contexte_commun.md` sera appliquée par le service d'indicateurs (semaine 10).
- **Pas encore d'événement `indicator.computed`** : il viendra avec ce service.
- Les **déplacements** durent au moins un pas : un agent parti à 10 h 00 arrive au plus tôt au pas de 10 h 15.

## 9. Pour la semaine 3

Le plan prévoyait la suite du moteur en semaine 3. Comme le moteur sans LLM est déjà complet, deux options
s'offrent :

1. **Avancer Godot** (prévu en semaines 4 et 5) : le bridge WebSocket ↔ Kafka, puis Godot en vue
   (création des agents depuis `run.started`, animation des déplacements, HUD alimenté par les événements,
   leviers envoyés comme commandes).
2. **Consolider le moteur** : affiner les règles, ajouter l'archiveur DuckDB, préparer le rejeu.

Je recommande l'option 1 : le socle D1 et D3 dépend de Godot, et c'est la partie la plus visible en
soutenance.

## 10. Commandes utiles

```powershell
cd "C:\Users\LEGION\Desktop\EIGSI 5A\projet de parcours\guichet-godot"
.venv\Scripts\python -m engine.run --config principal.yaml                        # un run
.venv\Scripts\python -m engine.run --config scenarios\emergence.yaml --graines 1-5  # série de 5
.venv\Scripts\python -m engine.run --deposants 40 --puits kafka,jsonl --rythme 0.5  # dans Kafka, au ralenti
.venv\Scripts\python -m pytest -q tests                                           # tests
.venv\Scripts\python tools\validate_contracts.py                                  # contrats
```

Les historiques de runs sont dans `data\runs\<run_id>\` : `events.jsonl`, `dlq.jsonl`, `resume.json`,
`config.json`. Le dossier `data\` est exclu de git.
