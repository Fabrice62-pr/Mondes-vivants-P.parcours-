# Semaine 4 : lisibilité, sources de l'ICC, comparaison des runs

**Date** : 13 octobre 2026
**Objectif** : tenir compte des premiers retours à l'écran, rendre l'ICC explicable depuis Godot (B4, F6) et
comparer les runs entre eux.
**Statut** : code terminé et vérifié sans fenêtre (rejeu d'un run enregistré dans la vue Godot). **Le rendu
reste à valider à l'écran** (voir § 5).

> Le plan a été resserré à 8 semaines, comme le prévoit le sujet : voir [../plan.md](../plan.md).

---

## 1. Retours de l'utilisateur et réponses

| Retour | Cause | Correction |
|---|---|---|
| Les habitants bougent trop vite, le déplacement n'est pas naturel | Godot calait chaque trajet sur l'horloge du moteur : 15 min simulées passent en 0,5 s réelle, un trajet durait donc moins d'une demi-seconde | Durée minimale visible (0,8 à 4 s selon la distance), accélération et ralentissement, départs décalés, allure propre à chacun (±15 %), balancement de marche, rotation douce |
| « La population a-t-elle été réduite ? » | Non : 145 agents (140 déposants, 3 personnages principaux déposants, 2 membres du personnel). Beaucoup partageaient la même maison et se superposaient | Domiciles répartis : chaque habitant déjà placé éloigne la maison de 25 m dans le choix ; les colocataires se placent autour de la porte. **144 emplacements distincts** au lieu d'une cinquantaine |
| `lancer.bat` n'ouvre pas la fenêtre de Godot | Sous PowerShell 5.1, `Start-Process` ne met pas les arguments entre guillemets : le chemin du projet était coupé à l'espace de « EIGSI 5A » et Godot se fermait aussitôt. En semaine 3, seul `-SansGodot` avait été testé | Chemin passé entre guillemets explicites ; le script vérifie que Godot tourne encore 3 s après son lancement, sinon il affiche un message |
| « Plus rien ne s'affiche dans le journal » | Normal avant l'article du vendredi : la vallée vit sa routine, sans conversation sur la Caisse. Les runs avaient été arrêtés le jeudi | Repères dans le journal : nombre d'habitants au lancement, changement de jour, ouverture et fermeture des agences, et une ligne qui explique pourquoi le journal reste calme jusqu'au vendredi |

## 2. Nouveautés

### Sources de l'ICC (B4, F6)
Un **clic sur la courbe de l'ICC** affiche, à la place de l'inspection :
- le moment, la valeur de l'ICC et sa variation sur l'heure précédente ;
- le nombre de croyances qui ont changé dans l'heure, **regroupées par cause** (agence fermée, ambiance du
  lieu, file observée, guichet, article, conversation…) ;
- des exemples d'habitants, avec leurs croyances avant et après ;
- **ce qui a été dit ou publié juste avant** : conversations, article, communiqués, échanges au guichet,
  commandes.

La chaîne est celle des événements : chaque point de la courbe porte les `belief.updated` du pas
(`sources`), et chaque `belief.updated` porte ses causes (`causes` de l'enveloppe). Godot garde ces
événements en mémoire pendant le run.

Exemple obtenu sur le scénario émergence, graine 1, à J3 10:00 : ICC 0,77 (+0,77 en 1 h), 247 croyances
modifiées dont 246 à la hausse, d'abord à cause des **agences fermées** (117) et de l'**ambiance des lieux**
(91), loin devant l'article (1) et les conversations (1). Pour la soutenance, c'est un résultat à
noter : **une fois les files visibles, ce n'est plus la rumeur qui fait monter le contexte commun, c'est
ce que les habitants voient**.

### Comparaison des runs
Le bouton **« Comparer les runs passés »** ouvre un panneau :
- les **courbes de l'ICC** des derniers runs, superposées sur l'axe du temps simulé (J1 à J7), avec le
  seuil de 0,60 ;
- un **tableau** : configuration, graine, déposants, ICC max, émergence (et quand), défaut (et quand,
  solvable ou non), part retirée et file maximale ;
- une case par run pour afficher ou masquer sa courbe.

Les données viennent de `data/runs/<run_id>/` : le bridge les lit à la demande (action `comparer`). Les
runs arrêtés avant la fin n'ont pas de `resume.json` et ne sont pas listés.

## 3. Fichiers modifiés

| Fichier | Modification |
|---|---|
| `guichet/scripts/agent_view.gd` | Déplacements : durée minimale, accélération et ralentissement, départs décalés, allure individuelle, balancement |
| `guichet/scripts/main.gd` | Répartition des domiciles ; mémoire des événements utiles aux sources de l'ICC ; demande de comparaison au bridge |
| `guichet/scripts/hud.gd` | Courbe de l'ICC cliquable ; panneau « Sources de l'ICC » ; écran de comparaison ; repères du journal |
| `services/comparaison.py` | **Nouveau.** Résumés des derniers runs terminés et courbe de l'ICC échantillonnée à l'heure, avec cache |
| `services/bridge.py` | Action `comparer` |
| `engine/moteur.py` | `clock.tick` publié **en premier** dans chaque pas. Avant, l'article et les commandes arrivaient avant le tick et Godot les datait du pas précédent (article affiché à 07:15 au lieu de 07:30). Les résultats ne changent pas |
| `tests/test_comparaison.py` | **Nouveau** : échantillonnage de la courbe, runs inachevés ignorés, limite |
| `docs/plan.md` | **Nouveau** : plan resserré à 8 semaines |

## 4. Vérifications effectuées

| Vérification | Résultat |
|---|---|
| Tests automatiques | ✅ **25 tests** passent (3 nouveaux) |
| Rejeu d'un run enregistré dans la vue Godot, sans fenêtre | ✅ 2 054 événements, 145 agents, aucune erreur de script |
| Sources de l'ICC au moment de la plus forte hausse | ✅ causes, exemples et conversations affichés (voir § 2) |
| Sources sur un point calme (J1 10:30) | ✅ « Aucune croyance n'a changé pendant cette heure » |
| Comparaison sur les 12 derniers runs réels | ✅ 12 courbes, tableau complet |
| Domiciles | ✅ 144 emplacements distincts, au plus 2 habitants au même endroit |
| Horodatage du journal | ✅ article à 07:30 |
| Déterminisme après le changement d'ordre du tick | ✅ même résultat à graine égale (défaut J3 10:00, ICC max 0,77, file max 29) |

## 5. À vérifier à l'écran

**Avant de tester, relancer le bridge** (`arreter.bat` puis `lancer.bat`) : l'ancien bridge ne connaît pas
l'action `comparer`.

- [ ] Les déplacements paraissent naturels, ni trop rapides ni trop lents (réglage : `WALK_SPEED` dans `agent_view.gd`).
- [ ] Les habitants ne se superposent plus devant les maisons.
- [ ] Le journal affiche les repères (jours, ouverture des agences) dès le début du run.
- [ ] Un clic sur la courbe de l'ICC affiche ses sources ; le point choisi est marqué en jaune.
- [ ] Le bouton « Comparer les runs passés » affiche les courbes et le tableau, sans masquer les panneaux.

## 6. Exigences avancées

| Exigence | Avancement |
|---|---|
| B4 | 🟡 Dans Godot, on remonte d'une valeur de l'ICC aux croyances, puis aux conversations et aux faits qui l'ont fait varier. Reste la même remontée dans l'analyse hors ligne (semaine 7) |
| F6 | 🟡 Chaque valeur de l'ICC est reliée à ses événements sources, et ceux-ci à leurs causes |
| E1, E2 | 🟡 Comparaison visuelle des runs dans Godot. Moyennes et dispersion restent en ligne de commande (`--graines 1-5`) |
| D1 | 🟡 Lisibilité améliorée ; reste la validation à l'écran |

## 7. Limites connues

- Les sources de l'ICC ne sont disponibles que pour le run en cours, et seulement à partir du moment où
  Godot s'est connecté.
- La comparaison ne montre que l'ICC. Les retraits cumulés et les files pourront s'y ajouter.
- Le basculement passe à « Irréversible » dès 07:30 le vendredi, avant toute file : les retraits
  « engagés » comptent les intentions. À rediscuter avec l'étalonnage des LLM.

## 8. Suite

**Semaine 5** : passerelle LLM et agents LLM (grands et petits modèles, repli sur les règles, quotas,
incidents), d'après ce qui a été appris dans [../README_api_key.md](../README_api_key.md).
