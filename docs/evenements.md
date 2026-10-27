# Événements : topics, granularité et contrat (C1, C3, F4)

## Topics Kafka

| Topic | Producteur | Consommateurs | Conservation |
|---|---|---|---|
| `guichet.events` | moteur | bridge (Godot), indicateurs, archiveur | 7 jours |
| `guichet.commands` | bridge (actions de l'utilisateur dans Godot) | moteur | 7 jours |
| `guichet.llm.requests` | moteur | passerelle LLM | 7 jours |
| `guichet.llm.responses` | passerelle LLM | moteur, archiveur | 7 jours |
| `guichet.indicators` | service d'indicateurs | bridge, archiveur | 7 jours |
| `guichet.incidents` | passerelle LLM, moteur | bridge, archiveur | 7 jours |
| `guichet.state` | moteur | bridge (instantané quand Godot se connecte) | compacté |
| `guichet.dlq` | tout consommateur | inspection manuelle | 30 jours |

Kafka sert au **flux**, pas à l'historique long : l'archiveur recopie chaque run dans DuckDB et Parquet
(voir [architecture.md](architecture.md)).

**Clé de partition** : l'`agent_id` quand l'événement concerne un agent, sinon l'identifiant de l'agence ou
de la Caisse. L'ordre est garanti pour un même agent. L'ordre global d'un run se reconstruit avec
`(t_sim, producer, seq)`.

## Granularité (C1)

**Principe** : on publie un événement quand quelque chose change **ce qu'un agent sait ou peut observer**.

| Publié | Non publié | Pourquoi |
|---|---|---|
| Changement de lieu (`agent.moved` : domicile → café) | Position à chaque image | Une position ne change pas les croyances ; Godot interpole le trajet. Environ 1 000 fois moins de messages |
| Chaque conversation, avec son texte | — | C'est le vecteur du contexte commun (A2, B4) |
| Variation de croyance avec sa cause | Recalcul identique sans variation | Traçabilité de l'indicateur jusqu'à sa cause (F6) |
| Changement de longueur de file | Chaque pas d'attente | La file est ce que voient les passants |
| Pas de l'horloge (`clock.tick`, toutes les 15 min simulées) | Les secondes réelles | Échéance d'attente des réponses LLM (barrière en synchrone) et repère d'affichage |
| Chaque appel LLM (coût, issue) | Le texte complet du prompt | Le coût (E4) passe dans le flux ; le prompt complet va directement dans l'archive |

Ordre de grandeur : environ 30 000 à 60 000 événements par run de 7 jours avec 145 agents.

## Les deux temps (C3)

| Champ | Sens | Posé par |
|---|---|---|
| `t_sim` | Minutes **simulées** depuis le début du run : quand ça se passe dans le monde | producteur |
| `t_emit` | Heure **réelle** d'émission | producteur |
| `t_proc` | Heure **réelle** de traitement | consommateur, au moment où il le traite |
| `payload.t_sim_contexte` (`decision.made`) | Instant simulé de la photographie du monde utilisée pour décider | moteur |

En mode hybride ou asynchrone, une décision demandée à `t_sim = 4320` peut s'appliquer à `t_sim = 4335`
parce que le LLM a répondu après l'échéance du pas. L'enveloppe porte 4335, `t_sim_contexte` porte 4320 et
`voie` vaut `en_retard` ; une réponse arrivée à temps porte `voie = dans_le_pas` et les deux instants sont
égaux. Toute information
utilisée dont la date dépasse `t_sim_contexte`, ou que l'agent n'a jamais reçue, déclenche un
`temporal.violation` (C6).

## Contrat et versions (F4)

- Chaque message suit l'enveloppe [`schemas/envelope/v1.json`](../schemas/envelope/v1.json) ; la charge utile
  suit `schemas/<type>/v<version>.json`.
- **Versions** : une modification compatible (champ optionnel ajouté) garde la version. Une modification
  incompatible (champ supprimé, renommé ou dont le type change) crée `v2.json`, et l'ancien fichier est
  conservé tant que des runs archivés l'utilisent.
- **Non-conformité** : tout consommateur valide ce qu'il lit. Un message non conforme, ou d'un type ou d'une
  version inconnus, est recopié dans `guichet.dlq` avec les erreurs de validation, puis ignoré. La simulation
  continue et le compteur de rejets s'affiche dans le HUD.
- Vérification : `.venv/Scripts/python tools/validate_contracts.py`.

## Types d'événements (v1)

`run.started`, `run.ended`, `clock.tick`, `agent.moved`, `conversation.held`, `counter.exchanged`,
`belief.updated`, `decision.made`, `queue.changed`, `withdrawal.served`, `caisse.updated`, `caisse.defaulted`,
`article.published`, `communique.published`, `command.issued`, `memory.written`, `llm.called`,
`incident.raised`, `temporal.violation`, `indicator.computed`, `state.snapshot`.

`state.snapshot` n'est pas un événement de l'historique : c'est la photographie de l'état courant, publiée à
chaque pas dans le topic compacté `guichet.state`. Elle n'est pas écrite dans `events.jsonl` ni archivée.
Depuis la semaine 7, elle est gardée à part dans `etats.jsonl` : le **rejeu** (E3) en a besoin pour
reproduire exactement la même scène dans Godot, et le service d'indicateurs recalcule l'ICC à partir
d'elle.

**Seuil de publication de `belief.updated`** : une variation n'est publiée que si la peur ou l'estimation
change d'au moins 0,05 depuis la dernière publication. On obtient environ 600 événements de croyance par
run au lieu d'environ 90 000 (143 agents × 640 pas).

**Volume mesuré** (semaine 2, sans LLM, 143 déposants, 7 jours) : environ 6 000 événements par run quand
la rumeur s'éteint, environ 2 000 quand un défaut arrête le run le vendredi matin. Les déplacements
(`agent.moved`) représentent environ la moitié du volume.

La description de chaque champ se trouve dans son schéma.
