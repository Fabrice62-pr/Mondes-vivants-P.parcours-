# GUICHET : visualisation Godot

Ce projet Godot 4.7 est la **vue** de la simulation GUICHET. Il affiche en direct la vallée de Brévanne, ses
habitants, les files d'attente et l'état de la Caisse (D1). Il permet aussi d'inspecter un habitant (D2) et
d'agir sur la simulation (D3).

**La simulation ne tourne pas dans Godot.** Elle tourne dans le moteur Python (`../engine/`). Godot reçoit
ses événements par le bridge (`../services/bridge.py`), qui relaie Kafka, et lui renvoie les actions de
l'utilisateur sous forme de commandes.

> La maquette d'origine, où toute la simulation tournait dans Godot, est conservée dans
> `../archives/maquette_godot_2026-10-13/`.

## Lancer

Depuis le dossier parent `guichet-godot\`, double-cliquez sur **`lancer.bat`**. Le script démarre Kafka et
le bridge, puis ouvre ce projet dans Godot. Ensuite, dans Godot :

1. choisissez une configuration (`principal.yaml`, `scenarios/emergence.yaml`…), le nombre de déposants
   et la graine ;
2. réglez le rythme, en secondes réelles par pas de 15 minutes simulées ;
3. laissez cochée la case **Agents LLM** pour que les personnages et les déposants réfléchissent avec un
   LLM gratuit, ou décochez-la pour un run uniquement à règles, sans consommer de quota ;
4. choisissez le **mode** : synchrone (tous agissent au même pas), asynchrone (chacun à son rythme, les
   décisions arrivent avec la latence réelle des modèles) ou hybride (le pas attend les réponses quelques
   secondes au plus ; celles qui arrivent après s'appliquent plus tard, et l'inspection l'indique) ;
5. cliquez sur **Lancer**.

**Rejouer un run** : bouton **Comparer les runs passés**, puis **Rejouer** sur la ligne du run. Le run
est rejoué à l'identique à partir de ses données enregistrées, sans aucun appel LLM ; la ligne du run
affiche « REJEU ». Pause et vitesse fonctionnent ; les autres leviers n'ont pas d'effet.

Avec les agents LLM, le run peut **marquer des pauses** : en mode synchrone, chaque pas attend les
réponses des modèles, et parfois que le quota gratuit de Groq se libère (surtout le vendredi matin,
quand tout le monde apprend la nouvelle en même temps).

On peut aussi ouvrir le projet à la main avec F5 : Godot se connecte au bridge dès qu'il est disponible et
se reconnecte tout seul. Il peut même rejoindre un run déjà en cours.

## Structure

| Fichier | Rôle |
|---|---|
| `scenes/main.tscn` | Scène principale (un seul nœud ; tout le reste est créé par script) |
| `scripts/main.gd` | Reçoit les événements, place et anime les agents, gère la sélection d'un agent |
| `scripts/bridge_client.gd` | Connexion WebSocket au bridge, avec reconnexion automatique |
| `scripts/agent_view.gd` | Un habitant à l'écran : silhouette, couleur, bulle de dialogue, déplacement |
| `scripts/hud.gd` | Lancement, état de la Caisse, indicateurs (ICC, basculement), leviers, inspection, journal |
| `scripts/agency.gd` | Une agence : emplacements de la file et panneau « FERMÉE » |
| `scripts/world_builder.gd` | Construction de la vallée : terrain, routes, ponts, bâtiments, arbres |
| `scripts/world_data.gd` | Coordonnées des lieux. **Doit rester identique à `../configs/monde.json`** (un test le vérifie) |
| `scripts/camera_rig.gd` | Caméra orbitale |

## Commandes

- **Clic** sur un habitant : l'inspecter (état, croyances, dernière décision et sa justification, derniers échanges)
- **Clic sur la courbe de l'ICC** : ce qui l'a fait varier pendant l'heure précédente (causes, exemples, conversations et faits à l'origine)
- **Comparer les runs passés** (panneau de gauche) : courbes de l'ICC superposées et tableau des derniers runs terminés
- Glisser (clic gauche ou droit) : tourner la caméra
- Molette : zoomer
- Maj + glisser, clic molette, ou ZQSD / WASD / flèches : se déplacer
- R : vue d'ensemble, T : vue de dessus

## Ce qu'on voit

**Couleurs des habitants** : vert calme, orange inquiet, rouge va retirer, gris a retiré, noir a perdu de
l'argent, bleu personnel de la Caisse. Les cinq personnages principaux (la boulangère, le retraité,
l'entrepreneur, l'employé d'agence, la direction) sont plus grands et portent leur nom.

**Indicateurs** (panneau de gauche), calculés par le moteur et affichés au rythme de la scène :
- **ICC**, l'indice de contexte commun, avec sa courbe et le seuil d'émergence de 0,60 ;
- **basculement** : Faible, Proche, Irréversible ou Défaut ;
- les réserves de la Caisse ;
- la répartition des habitants par état ;
- le **coût des agents LLM** : appels, appels réussis, tokens, latence moyenne, incidents, appels par modèle ;
- le **service d'indicateurs** : ICC moyenné sur 2 h et retard entre un instantané du monde et sa prise en
  compte dans l'indicateur (C7).

**Leviers**, envoyés au moteur sous forme de commandes : garantie des dépôts et son plafond, retrait
maximal par passage, communiqué (rassurant, factuel ou vague), fermeture et réouverture de chaque agence,
consigne de l'employé, injection d'une rumeur, pertes réelles sur les prêts, pause et vitesse.
