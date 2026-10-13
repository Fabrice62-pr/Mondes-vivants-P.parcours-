# GUICHET — La Caisse de Brévanne (projet Godot 4)

Simulation d'un bank run dans la vallée de Brévanne : la carte 3D, ses trois agences,
ses lieux de rencontre, et une population de déposants qui discutent, observent les files
et décident (ou non) de retirer leur argent.

Développé avec **Godot 4.7**.

> Ce README décrit la maquette d'origine, où toute la simulation tourne dans Godot. Le projet évolue vers
> un moteur Python publié dans Kafka, avec Godot comme vue : voir `../README.md` et `../docs/`.

## Ouvrir le projet

1. Décompressez `guichet-godot.zip`.
2. Dans le gestionnaire de projets Godot, cliquez sur **Importer**, puis sélectionnez
   le fichier `project.godot` du dossier décompressé.
3. Cliquez sur **Importer et éditer**, puis lancez avec **F5**.

Toute la carte est générée par script au lancement : il n'y a aucun modèle 3D à importer.

## Structure

| Fichier | Rôle |
|---|---|
| `scenes/main.tscn` | Scène principale (un seul nœud, tout le reste est créé par script) |
| `scripts/world_data.gd` | Coordonnées des lieux, routes, rivière, fonction de hauteur du terrain |
| `scripts/world_builder.gd` | Construction de la vallée : terrain, routes, ponts, bâtiments, arbres |
| `scripts/agency.gd` | Une agence : guichet et file d'attente |
| `scripts/caisse.gd` | Comptabilité stricte : réserves, prêts, fonds propres, défaut |
| `scripts/depositor.gd` | Un déposant : croyances, déplacements, décision de retirer |
| `scripts/main.gd` | Orchestration : rumeur, conversations, leviers, indicateur de basculement |
| `scripts/hud.gd` | Interface : état de la Caisse, leviers, journal des conversations |
| `scripts/camera_rig.gd` | Caméra orbitale |
| `scripts/llm_client.gd` | Appels optionnels à un LLM via le proxy |
| `proxy/server.py` | Proxy local qui garde la clé API hors du jeu |

Repère : 1 unité = 1 mètre, Y vers le haut, nord vers −Z. Terrain de 280 × 228 m.
Les positions sont celles de la maquette web (plan × 1,75).

## Commandes

- Glisser (clic gauche ou droit) : tourner la caméra
- Molette : zoomer
- Maj + glisser, clic molette, ou ZQSD / WASD / flèches : se déplacer
- R : vue d'ensemble, T : vue de dessus

Couleurs des déposants : vert calme, orange inquiet, rouge en route vers l'agence,
gris a retiré, noir a perdu de l'argent. Les trois personnages nommés (la boulangère,
le retraité, l'entrepreneur) sont plus grands et portent leur nom.

## Le modèle

Chaque déposant a deux croyances :

- `fear` : sa propre crainte que la Caisse soit en danger ;
- `est_others` : son estimation de la part des **autres** qui vont retirer.
  C'est le raisonnement de second ordre : « je ne crois pas à la faillite, mais si mes
  voisins retirent, je serai le dernier servi ».

`est_others` monte quand il voit une file s'allonger, quand les gens autour de lui sont
inquiets, ou quand on lui parle de retraits. Sa pression à retirer combine ces deux
croyances, réduite par la garantie des dépôts (s'il est couvert) et par sa loyauté.
Au-delà de son seuil personnel, il part au guichet.

La Caisse ne garde en caisse qu'une fraction des dépôts. Quand un retrait dépasse les
réserves, elle fait défaut, même si elle reste solvable. Le journal indique alors s'il
s'agissait d'une panique autoréalisatrice (Caisse solvable) ou justifiée (pertes réelles
sur les prêts, réglables avec « Pertes réelles sur prêts »).

**Indicateur de basculement** : « Proche » quand les retraits engagés plus ceux des
déposants au bord du seuil dépassent les réserves ; « Irréversible » quand les retraits
déjà engagés dépassent à eux seuls ce qui reste en caisse.

## Leviers

En direct : garantie des dépôts et son plafond, retrait maximum par passage, communiqué
de la direction (rassurant, factuel ou vague, dont la crédibilité baisse quand les files
s'allongent), fermeture de chaque agence, employé d'agence tenu au silence ou non.

Au prochain lancement (bouton « Relancer ») : nombre de déposants, part des dépôts en
caisse, part de déposants très connectés, pertes réelles, force et moment de la rumeur,
activation du LLM et budget d'appels.

## Dialogues par LLM (optionnel)

Sans LLM, les conversations suivent des règles locales. Pour faire raisonner les
déposants avec Claude :

```bash
export ANTHROPIC_API_KEY="votre_clé"      # PowerShell : $env:ANTHROPIC_API_KEY="votre_clé"
python proxy/server.py
```

Puis, dans le jeu, cochez « Dialogues par LLM », vérifiez l'adresse
`http://127.0.0.1:8787/dialogue` et cliquez sur « Relancer ».

**Ne mettez jamais la clé API dans le projet Godot** : tout ce qui est dans un export
peut être extrait. Le proxy la garde côté serveur, limite le nombre de requêtes par
minute, et le jeu applique un budget total d'appels. Si le proxy ne répond pas, le jeu
repasse automatiquement aux règles locales.

Le modèle par défaut est `claude-haiku-4-5-20251001` (rapide et économique). Vous pouvez
en choisir un autre avec la variable `GUICHET_MODEL`.

## Pistes pour aller plus loin

- Remplacer les primitives par des modèles low-poly (Kenney, Quaternius) en gardant les
  mêmes positions.
- Ajouter un `NavigationRegion3D` pour que les déposants suivent les routes.
- Enregistrer les indicateurs dans un CSV pour comparer plusieurs lancements.
