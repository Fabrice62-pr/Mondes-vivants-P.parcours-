# Contexte commun : définition observable (B1)

## Définition

Dans GUICHET, le **contexte commun** est la **croyance partagée sur ce que les autres déposants vont faire**,
et non seulement sur la santé de la Caisse.

> Un contexte commun a émergé quand une majorité de déposants **s'attend à ce que les autres retirent**
> et que ces attentes **convergent** : les déposants ne se contentent plus d'avoir peur chacun de leur côté,
> ils partagent la même lecture de la situation (« tout le monde va retirer »).

On peut avoir peur sans contexte commun : des inquiétudes dispersées et contradictoires. On peut aussi avoir
un contexte commun rassurant : tout le monde s'attend à ce que personne ne retire. L'indicateur mesure la
**convergence des attentes**, quelle que soit leur direction, et on lit ensuite leur sens.

## Ce qui est observable

| Observable | Source | Agents concernés |
|---|---|---|
| `estime_autres` : part des autres qui vont retirer selon l'agent (0 à 1) | `belief.updated` | Tous (LLM et règles) |
| `peur` : croyance personnelle que la Caisse est en danger (0 à 1) | `belief.updated` | Tous |
| Position exprimée dans un message : *solide*, *inquiet*, *tout le monde retire* | `conversation.held`, classée par le modèle auxiliaire | Agents LLM |
| Actions : départs vers les agences, longueur des files | `decision.made`, `queue.changed` | Tous |

Les croyances des agents LLM sont celles qu'ils déclarent dans leur sortie JSON. Celles des agents à règles
sont leurs variables d'état. Les positions exprimées permettent de vérifier que les nombres déclarés
correspondent à ce que les agents **disent**.

## Indicateurs calculés pendant le run (B2)

Ils sont calculés en continu sur une fenêtre glissante de 2 h simulées et publiés dans `guichet.indicators`.

1. **ICC, indice de contexte commun**, entre 0 et 1 :
   `ICC = A × C`
   - `A` (attente) : part des déposants dont `estime_autres > 0,5` ;
   - `C` (convergence) : `1 − écart-type(estime_autres) / 0,5`, borné entre 0 et 1.

   L'indice est élevé seulement si beaucoup de déposants s'attendent à des retraits **et** s'accordent là-dessus.

2. **Accord des positions** : pour les conversations de la fenêtre, part des paires d'interlocuteurs qui
   expriment la même position. C'est le versant « langage naturel » du contexte commun.

3. **Indicateur de basculement**, déjà présent dans la maquette Godot : *Faible*, *Proche*, *Irréversible*
   ou *Défaut*, selon la comparaison entre les retraits engagés ou imminents et les réserves.

Chaque valeur publiée porte la liste des `event_id` qui l'ont produite (F6). Un clic sur un point de la
courbe dans Godot remonte aux conversations qui l'ont précédé (B4).

## Critère d'émergence (B3)

Un contexte commun **a émergé** dans un run si `ICC ≥ 0,6` pendant au moins 4 h simulées consécutives.
Les seuils (0,6 et 4 h) sont fixés **avant** les expériences et ne seront pas ajustés après coup.

Scénarios de démonstration, avec 5 runs chacun :

| Scénario | Paramètres | Attendu |
|---|---|---|
| **Émergence** | rumeur forte (0,8), sans garantie, 30 % de connectés | ICC franchit le seuil, file d'attente, risque de défaut |
| **Absence** | rumeur faible (0,2), garantie à 100 000, communiqué factuel tôt | ICC reste bas, inquiétudes dispersées sans convergence |

## Limites connues

- `estime_autres` est déclaré par l'agent LLM : il peut être incohérent avec ce qu'il dit. On mesurera
  cet écart entre le nombre déclaré et la position exprimée (classée).
- La classification des positions par un modèle auxiliaire introduit son propre bruit : on l'évaluera
  sur un échantillon annoté à la main.
