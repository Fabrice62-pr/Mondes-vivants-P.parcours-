# Semaine 5 : la passerelle LLM et les agents LLM

**Date** : 13 octobre 2026
**Objectif** : faire décider les personnages et la majorité des déposants avec des LLM gratuits, sans
jamais changer de modèle en cours de run, avec repli sur les règles et un coût mesuré à chaque appel.
**Statut** : code terminé, testé sans réseau (faux fournisseur) et en conditions réelles (runs avec les
API). **Reste à valider à l'écran** et à étalonner (semaine 8).

---

## 1. Ce qui change pour l'utilisateur

- Dans Godot, la case **« Agents LLM »** est cochée par défaut. Décochée, le run se fait entièrement à
  règles, sans consommer de quota. En ligne de commande : `--sans-llm`.
- Le panneau de gauche affiche le **coût des LLM** en direct : appels, appels réussis, tokens, latence
  moyenne, incidents, appels par modèle.
- Le journal affiche les décisions des personnages avec leur source (« LLM gpt-oss-120b ») et chaque
  incident LLM.
- L'inspection d'un habitant dit si sa dernière décision vient du LLM (avec le numéro d'appel), d'une
  règle, ou d'un **repli** sur la règle après un échec.
- Avec les agents LLM, le run **marque des pauses** : en mode synchrone, chaque pas attend les réponses.

## 2. Qui appelle quel modèle, et quand

| Agent | Modèle | Quand il appelle | Ce que le LLM produit |
|---|---|---|---|
| Déposants LLM (3 personnages + ~75 % des ordinaires) | gpt-oss-120b (personnages), ministral-8b ou gpt-oss-20b | Éveillé, au plus une fois par heure simulée, **et seulement si quelque chose a changé** : une nouvelle information dans son carnet, ou un ressenti qui a bougé d'au moins 0,10 | Action (retirer, attendre, rien), peur et estimation des autres **déclarées**, justification, phrase dite aux voisins |
| Employé d'agence | gpt-oss-120b | Chaque question posée au guichet du Bourg | Répondre ou éluder, la réponse, la justification. Il peut s'écarter de la consigne |
| Direction | gemma-31b | Aux heures de contrôle (8 h, 14 h) après l'article, au plus un communiqué par 24 h | Publier ou non, le ton, **le texte du communiqué**, la justification |
| Déposants à règles (~25 %) | aucun | — | — |

**Économie d'appels (E5)** :
- Avant l'article, personne n'a de raison de réfléchir : aucun appel.
- La phrase qu'un agent dit à ses voisins vient de sa dernière réflexion. Une conversation ne coûte donc
  **aucun appel supplémentaire**.

## 3. Décisions de conception

| Décision | Choix | Raison |
|---|---|---|
| Où vit la passerelle | Bibliothèque `llm/` appelée par le moteur, appels en parallèle | En mode synchrone, le pas attend toutes les réponses : passer par Kafka n'ajouterait que de la latence. `guichet.llm.requests` servira au mode asynchrone (semaine 7) |
| Ce que sait un agent | Son **carnet** : les 10 dernières choses qu'il a apprises, datées (article lu, conversations entendues, files vues, réponses au guichet, communiqués, agences fermées) | Il ne reçoit jamais l'état interne de la Caisse ni les pensées des autres (C6). Mémoire courte en attendant la mémoire longue et le RAG (semaine 6) |
| Croyances d'un agent LLM | Celles qu'il **déclare** dans sa réponse remplacent les siennes ; la cause publiée est `reflexion` | Définition de `docs/contexte_commun.md`. Entre deux réflexions, la perception (files, ambiance) continue de les faire évoluer |
| Connaissance commune | Le prompt rappelle qu'une banque ne garde qu'une partie des dépôts en caisse et que les derniers servis risquent de tout perdre | Sans elle, les LLM restaient calmes : voir § 5. Tout déposant réel le sait ; c'est ce qui fait la logique d'un bank run |
| Direction | Elle **délibère sans arrêter la vallée** : sa décision s'applique quand la réponse arrive, avec `t_sim_contexte` = l'instant où elle a commencé à réfléchir | Gemma met 30 à 60 s à répondre. Bloquer le run aurait figé Godot une minute à chaque contrôle |
| Quota | Le pas **attend** que le quota du modèle se libère, jusqu'à 2 min, au lieu de faire décider l'agent par règle | C'est la barrière du mode synchrone : tous agissent au même pas |
| Échec | Un nouvel essai sur le même modèle, avec un rappel du format, puis repli sur la règle et incident | Règle du projet : jamais un autre modèle (A4, A5) |
| Contrat | Une seule modification : `reflexion` ajouté aux causes de `belief.updated` (compatible, reste en v1) | Ajouter une valeur permise ne rend aucun ancien événement invalide |

## 4. Fichiers créés et modifiés

| Fichier | Rôle |
|---|---|
| `llm/fournisseurs.py` | **Nouveau.** Adaptateurs `openai` (Groq, Mistral) et `gemini_native` (Google), `User-Agent`, mode JSON, lecture du délai après un 429 |
| `llm/limites.py` | **Nouveau.** Limiteur par modèle (requêtes et tokens par minute, requêtes par jour) ; compteur du jour gardé dans `data/logs/llm_quota.json` |
| `llm/passerelle.py` | **Nouveau.** Lots en parallèle, refus d'un modèle non attribué, budget, nouvel essai, incidents, `llm.called` par essai, bilan du coût ; requête sans barrière pour la direction |
| `llm/prompts.py` | **Nouveau.** Prompts du déposant, de l'employé et de la direction ; extraction et validation du JSON (statuts `json_invalide`, `hors_role`) |
| `engine/moteur.py` | Réflexion des déposants LLM, employé et direction LLM, carnet, repli, coût dans le résumé |
| `engine/agents.py`, `engine/population.py` | Carnet et suivi des réflexions ; persona des personnages |
| `engine/run.py` | Option `--sans-llm` |
| `configs/principal.yaml` | `llm.actif: true`, persona des 5 personnages, réglages de réflexion et d'attente de quota |
| `configs/models.yaml` | Par modèle : `max_tokens`, `json`, `timeout_s`, `limites` |
| `schemas/belief.updated/v1.json` | Cause `reflexion` |
| `services/bridge.py` | Relaie `guichet.llm.responses` ; option `llm` au lancement |
| `guichet/scripts/hud.gd`, `main.gd` | Case « Agents LLM », coût des LLM, incidents, source des décisions |
| `tests/test_llm.py` | **Nouveau** : 13 tests sans réseau (voir § 6) |

## 5. Premiers résultats en conditions réelles

Scénario `emergence`, graine 1. Les deux premiers runs utilisaient le prompt **sans** la connaissance
commune sur les banques ; le troisième **avec**.

| Run | Population | Appels | Décisions LLM | Replis | ICC max | Défaut | Durée réelle |
|---|---|---|---|---|---|---|---|
| Règles seules (référence semaine 2) | 140 | 0 | 0 | — | 0,77 | J3 vendredi 10:00 | 1 s |
| LLM, sans connaissance commune | 20, 4 jours | 102 | 94 | 4 | 0,06 | aucun | 309 s |
| LLM, sans connaissance commune | **140, 7 jours** | **1 004** | **981** | 17 | 0,40 | J6 lundi 09:15 | 500 s |
| LLM, avec connaissance commune | 20, 4 jours | 25 | 24 | 0 | 0,49 | J3 vendredi 13:15 | 16 s |

**Coût du run complet (E4, E5)** : 537 000 tokens en entrée et 130 000 en sortie, soit environ
670 tokens par appel.

| Modèle | Appels | Réussis | Latence moyenne |
|---|---|---|---|
| ministral-8b (Mistral) | 888 | 887 | 1,5 s |
| gpt-oss-20b (Groq) | 78 | 66 | 1,1 s |
| gpt-oss-120b (Groq) | 28 | 28 | 0,8 s |
| gemma-31b (Google) | 10 | **0** | — |

Les 12 échecs de gpt-oss-20b sont des dépassements de quota (8 000 tokens/min) le vendredi matin. Ils
ont conduit à faire attendre le pas plutôt que de replier l'agent. Les 10 échecs de Gemma sont des
erreurs 500 et 503 de Google.

**Ce qu'on observe** :
- **Sans la connaissance commune, les LLM restent calmes** : 961 décisions « attendre » pour 18 « retirer ».
  Leurs justifications répètent qu'ils n'ont « pas de preuve concrète ». La Caisse tombe quand même le
  lundi, sous l'effet de retraits lents et dispersés (15 % des dépôts), **sans contexte commun**
  (ICC max 0,40) : c'est une faillite par érosion, pas un bank run.
- **Avec la connaissance commune**, les justifications deviennent stratégiques (« Si tout le monde se
  presse comme ça, je ne veux pas finir dans la queue des oubliés ») et le défaut arrive le vendredi.
- 92 % des conversations portent une phrase produite par un LLM, sans appel supplémentaire.

Ces runs sont trop peu nombreux pour conclure : la campagne de la semaine 8 (au moins 10 graines par
configuration) dira si l'écart est robuste. Il nourrit déjà H1 et le rapport : **la réaction des LLM
dépend de ce qu'on leur dit du fonctionnement d'une banque**, ce qui doit être déclaré comme un choix de
modélisation.

## 6. Vérifications

| Vérification | Résultat |
|---|---|
| Tests automatiques | ✅ **37 tests** passent, dont 13 nouveaux sans réseau : validation des réponses, nouvel essai sur le même modèle, échec persistant sans changement de modèle, modèle non attribué refusé, budget, 429, limiteur, run complet conforme avec agents LLM, carnet sans information future (C6), panne totale avec repli, direction qui délibère (`t_sim_contexte ≤ t_sim`) |
| Contrats | ✅ `validate_contracts.py` ; 0 rejet en DLQ sur tous les runs réels |
| Un agent ne change jamais de modèle | ✅ vérifié sur chaque `llm.called` des runs réels |
| Vue Godot | ✅ rejeu sans fenêtre d'un run LLM : coût, inspection, journal |

## 7. Limites connues

- **Google est instable** : erreurs 500 et 503 fréquentes sur Gemma. La direction se replie alors sur sa
  règle. Si cela persiste, on pourra lui attribuer gpt-oss-120b (même catégorie « grand »), au prix de
  quelques appels de plus sur le quota Groq.
- La réponse d'un interlocuteur dans une conversation reste une phrase de règle ; seule la phrase du
  locuteur LLM vient du modèle.
- Les runs avec LLM ne sont **pas reproductibles à l'identique** : les réponses des modèles varient. La
  dispersion entre graines se mesurera en semaine 8, et le rejeu (semaine 7) relira les réponses enregistrées.
- Pas de cache des réponses : pour la mise au point, utiliser `--sans-llm` ou une petite population.

## 8. Suite

**Semaine 6** : mémoire longue des agents et RAG (Chroma, embeddings locaux), souvenirs et documents cités
dans `decision.made`, mémoire visible dans l'inspection (D2).
