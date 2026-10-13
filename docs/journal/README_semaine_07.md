# Semaine 7 : mode asynchrone, rejeu, archive et service d'indicateurs

**Date** : 13 octobre 2026
**Objectif** : les exigences du flux et du temps qui manquaient encore. Mode asynchrone (C4), rejeu d'un
run à l'identique (E3), historique interrogeable en SQL (F5), indicateur calculé hors du moteur avec son
retard mesuré (C7).
**Statut** : code terminé, testé sans réseau et de bout en bout avec Kafka. **Reste à valider à l'écran.**

---

## 1. Ce qui change pour l'utilisateur

**Avant de tester : `arreter.bat` puis `lancer.bat`.** Le lancement démarre maintenant **4 éléments** :
Kafka, le bridge, le service d'indicateurs, puis Godot.

Dans Godot :
- au lancement, un menu **Mode** : synchrone ou asynchrone ;
- dans **Comparer les runs passés**, un bouton **Rejouer** par run. Le run repart à l'identique, sans aucun
  appel LLM, et la ligne du run affiche « REJEU ». Pause et vitesse fonctionnent ;
- une ligne **Service d'indicateurs** : ICC moyenné sur 2 h et retard de prise en compte (C7).

En ligne de commande : `--mode asynchrone`, `services.rejeu`, `services.archiveur` et les requêtes de
`requetes/` (voir le README).

## 2. Mode asynchrone (C4)

| | Synchrone | Asynchrone |
|---|---|---|
| Horloge simulée | avance quand tout le monde a décidé | avance au **rythme réel** choisi (0,5 s par pas de 15 min par défaut) |
| Un agent LLM réfléchit | le pas attend sa réponse (barrière) | la vallée continue ; la décision s'applique **quand la réponse arrive** |
| `t_sim_contexte` d'une décision | égal à `t_sim` | l'instant où l'agent a commencé à réfléchir, **antérieur** à `t_sim` |
| Effet attendu (H2) | aucun : tous agissent au même pas | un modèle rapide agit plus tôt qu'un modèle lent |

À 0,5 s par pas, une réponse de Mistral en 1,5 s arrive 3 pas plus tard, soit 45 minutes simulées. Une
réponse de Gemma en 50 s arrive environ 100 pas plus tard, soit plus d'une journée simulée.

**Détails** :
- Un agent qui réfléchit ne relance pas de réflexion avant d'avoir reçu sa réponse.
- Si sa situation a changé entre-temps (il fait déjà la queue, ou la Caisse a fait défaut), la réponse
  est comptée dans le coût mais ne s'applique pas.
- Le garde-fou C6 compare chaque souvenir à l'instant **où l'agent a commencé à réfléchir**.
- L'employé au guichet reste synchrone : un client attend sa réponse.
- La direction délibérait déjà sans bloquer depuis la semaine 5.
- Au plus 6 requêtes en vol à la fois (`max_simultanes`), pour ne pas saturer les fournisseurs.

## 3. Rejeu (E3)

« Un run passé se rejoue à l'identique à partir des seules données enregistrées, sans nouvel appel LLM.
Le rejeu reproduit la même visualisation et les mêmes indicateurs. »

- Le moteur garde maintenant les **instantanés d'état** dans `etats.jsonl`, à côté de `events.jsonl`.
  Ce sont eux qui placent et colorent les agents dans Godot.
- `services/rejeu.py` republie les deux fichiers **tels quels**, dans l'ordre d'origine (`seq`), pas après
  pas. Il ne fait aucun calcul et aucun appel LLM. Seul `t_proc` change : le bridge le pose au moment du
  rejeu (C3).
- **Mêmes indicateurs, deux preuves** :
  1. les indicateurs du moteur sont des événements enregistrés : ils sont rejoués tels quels ;
  2. le service d'indicateurs **recalcule** l'ICC à partir des instantanés rejoués, et retrouve celui du
     moteur.

Les runs enregistrés avant la semaine 7 n'ont pas d'`etats.jsonl`. Leur bouton « Rejouer » est désactivé.

## 4. Archive interrogeable (F3, F5)

- À la fin de chaque run, `services/archiveur.py` transforme `events.jsonl` en 13 tables Parquet dans
  `data/runs/<run_id>/archive/` : runs, agents, événements, décisions, appels LLM, conversations,
  croyances, indicateurs, retraits, souvenirs, communiqués, incidents, violations.
- `data/archive.duckdb` ne contient que des **vues** sur ces fichiers : elle se reconstruit à tout moment.
- Le dossier `requetes/` contient les requêtes du rapport. Une requête rejouée sur les mêmes runs donne
  le même résultat (F5).

| Requête | Ce qu'elle montre |
|---|---|
| `modele_unique.sql` | A5 : aucun agent ne change de modèle (0 ligne attendue) |
| `scenarios.sql` | B3, E2 : moyenne et dispersion par scénario, mode, LLM, mémoire, population |
| `cout.sql` | E4, E5 : appels, tokens et durée par run ; taux de réussite et latences par modèle |
| `decisions.sql` | D2, A4, H4 : origine des décisions (LLM, règle, repli), justifications de la boulangère et du retraité |
| `memoire.sql` | A1, F1, F2, C6 : documents et souvenirs rappelés ; contrôle C6 |
| `icc_sources.sql` | B4, F6 : causes des croyances lors de la plus forte hausse de l'ICC |

**Première version trop lente** : l'insertion ligne par ligne depuis Python prenait plus de dix minutes
pour 22 runs. Dans la version actuelle, DuckDB lit lui-même le JSON et extrait les colonnes en SQL : il
faut **2 secondes**.

## 5. Service d'indicateurs (B2, F6, C7)

Le service `services/indicateurs.py` est un consommateur Kafka indépendant du moteur, lancé par
`lancer.bat`. À chaque instantané, il publie :
- `icc_flux` : l'ICC du pas, recalculé ;
- `icc_2h` : sa moyenne sur une fenêtre glissante de 2 h simulées (`docs/contexte_commun.md`).

Chaque valeur porte ses **sources** (les `belief.updated` du pas, F6) et son **retard** : le temps entre
l'émission de l'instantané par le moteur et la publication de l'indicateur (C7).

## 6. Mesures

### Bout en bout, par le bridge et Kafka, comme Godot

| Mesure | Résultat |
|---|---|
| Rejeu d'un run de 140 déposants demandé par WebSocket | ✅ tous les messages arrivent : 2 023 événements et 216 instantanés |
| ICC recalculé par le service pendant le rejeu, comparé à l'ICC du moteur | ✅ **identique sur les 216 pas** |
| Retard du service en direct, 40 déposants (run à règles) | médiane **70 ms**, maximum 87 ms |
| Retard du service en direct, 140 déposants (run à règles) | médiane **72 ms**, maximum 86 ms |

**Premier constat pour H7** : sans LLM, le retard ne dépend pas de la population. Reste à le mesurer avec
les LLM et en mode asynchrone (semaine 8). Les instantanés arrondissent les croyances à 3 décimales :
l'ICC recalculé peut donc différer de celui du moteur de 0,0002 au plus. La vérification le tolère et
affiche l'écart.

Pendant un rejeu, le retard affiché n'a pas de sens physique : le rejeu rythme les pas autrement que le
run d'origine. Le service compte le retard à partir de l'arrivée du `run.started` rejoué.

### Mode asynchrone avec les vrais LLM

Scénario `emergence`, 20 déposants, 3 jours, graine 1, 0,5 s par pas (run `emergence-g1-20261013-163028`).
Résultat : 40 appels, 0 repli, 0 violation C6, 132 s réelles. Pas de défaut : ICC max 0,03, 2 % retirés.

**Délai entre le début de la réflexion et l'application de la décision**, en minutes simulées :

| Modèle | Fournisseur | Latence médiane | Décisions | Délai moyen | Délai max |
|---|---|---|---|---|---|
| gpt-oss-120b | Groq | 0,8 s | 8 | **47 min** | 90 min |
| gpt-oss-20b | Groq | 0,5 s | 5 | 54 min | 105 min |
| ministral-8b | Mistral | 1,9 s | 27 | **72 min** | 165 min |

Le mécanisme de H2 apparaît : les agents servis par Groq, plus rapide, agissent environ 25 minutes
simulées plus tôt que ceux de Mistral. Le délai dépasse la seule latence (0,8 s ≈ 2 pas ≈ 30 min) :
il comprend la recherche des souvenirs, le passage dans la file des 6 requêtes en vol et l'attente du
pas suivant. Un seul petit run ne permet pas de conclure sur le phénomène : la comparaison des modes sur
plusieurs graines (C5) est l'objet de la semaine 8.

## 7. Fichiers créés et modifiés

| Fichier | Rôle |
|---|---|
| `engine/moteur.py` | Mode asynchrone : réflexions sans barrière, appliquées à leur arrivée ; C6 rapporté au contexte |
| `engine/agents.py` | Réflexion en cours d'un agent |
| `engine/evenements.py` | Instantanés gardés dans `etats.jsonl` |
| `engine/run.py` | Rythme réel imposé en asynchrone ; archivage à la fin de chaque run |
| `llm/passerelle.py` | Requêtes sans barrière limitées à `max_simultanes` |
| `services/archiveur.py` | **Nouveau.** Parquet et DuckDB |
| `services/rejeu.py` | **Nouveau.** Rejeu et vérification |
| `services/indicateurs.py` | **Nouveau.** ICC recalculé, ICC sur 2 h, retard, vérification |
| `services/bridge.py` | Actions `rejouer` et `mode` |
| `services/comparaison.py` | Mode, LLM et « rejouable » de chaque run |
| `requetes/*.sql` | **Nouveau.** 6 fichiers de requêtes |
| `lancer.ps1`, `arreter.ps1` | Service d'indicateurs démarré et arrêté ; arrêt du rejeu |
| `guichet/scripts/hud.gd`, `main.gd` | Mode, bouton Rejouer, ligne du service d'indicateurs |
| `configs/principal.yaml` | Section `asynchrone` |
| `requirements.txt` | `duckdb` |
| `tests/test_semaine7.py` | **Nouveau** : 6 tests |

## 8. Vérifications

| Vérification | Résultat |
|---|---|
| Tests automatiques | ✅ **49 tests** passent. Nouveaux : asynchrone (décision appliquée après son contexte, sans violation), synchrone (décision au même pas), rejeu identique, commandes pendant le rejeu, archive interrogeable, service d'indicateurs conforme et égal au moteur |
| Contrats | ✅ aucun changement de schéma ; `validate_contracts.py` |
| Compilation des scripts Godot | ✅ sans erreur |

## 9. Limites connues

- **Changement de résultat des runs à règles** : depuis la semaine 5, le moteur tire la question posée au
  guichet avant la décision d'éluder. L'ordre des tirages aléatoires a donc changé. À graine égale, un run
  à règles ne redonne pas exactement le résultat de la semaine 2 (ICC max 0,78 au lieu de 0,77, même défaut
  le vendredi à 10:00). Il reste déterministe d'une exécution à l'autre.
- Le rejeu reproduit le flux ; il ne **ré-exécute** pas le moteur. Ré-exécuter un run LLM à l'identique
  demanderait d'enregistrer le texte complet de chaque réponse.
- En asynchrone, le résultat dépend de la charge des fournisseurs à ce moment-là : deux runs de même
  graine diffèrent davantage qu'en synchrone.

## 10. Suite

**Semaine 8** : campagne d'expériences (H1 à H7, au moins 10 graines par configuration), mesure du coût
(E4, E5, O7), qualité de la récupération (F2), comparaison des modes (C5) et des tailles (C8), rapport et
préparation de la soutenance. Questions en attente : le modèle de la direction (Gemma réussit 12 % de ses
appels) et le « monde libre », à rediscuter.
