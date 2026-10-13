# Semaine 3 : Godot devient la vue de la simulation

**Date** : 13 octobre 2026
**Objectif** : relier Godot au moteur Python. Godot ne simule plus rien : il affiche ce que le moteur publie
dans Kafka (D1) et renvoie les actions de l'utilisateur sous forme de commandes (D3). Le tout doit se lancer
en un double-clic.
**Statut** : terminée côté code et vérifiée sans fenêtre. **Le rendu visuel reste à vérifier à l'écran par
l'utilisateur** (voir § 6).

> Le plan initial prévoyait la suite du moteur en semaine 3 et Godot en semaines 4 et 5. Comme le moteur
> était déjà complet, Godot a été avancé à la demande de l'utilisateur.

---

## 1. Ce qui change pour l'utilisateur

**Avant** : Godot contenait sa propre simulation, indépendante du moteur Python.

**Maintenant** :

```
lancer.bat  ──►  Kafka (Docker)  +  bridge  +  Godot
                                         │
Godot : « Lancer »  ──►  bridge  ──►  démarre le moteur Python
moteur  ──►  Kafka  ──►  bridge  ──►  Godot affiche (carte, HUD, journal)
Godot : leviers  ──►  bridge  ──►  Kafka (guichet.commands)  ──►  moteur applique
```

1. Double-clic sur `lancer.bat` (Docker Desktop doit être démarré).
2. Dans Godot : choisir la configuration, le nombre de déposants, la graine et le rythme, puis cliquer sur
   **Lancer**.
3. Regarder, cliquer sur un habitant pour l'inspecter, et agir avec les leviers.
4. `arreter.bat` pour tout arrêter.

## 2. Décisions de conception

| Décision | Choix | Raison |
|---|---|---|
| Lien entre Godot et Kafka | Un **bridge WebSocket** en Python | Godot n'a pas de client Kafka ; il gère nativement les WebSockets (`WebSocketPeer`) |
| Qui lance le moteur ? | Le **bridge**, à la demande de Godot | Tout se pilote depuis Godot, sans terminal ouvert |
| Branchement en cours de run | Le bridge garde en mémoire le dernier `run.started`, le dernier instantané et les derniers indicateurs, et les envoie à chaque nouvelle connexion | Godot peut être fermé et rouvert sans perdre le run |
| Placement des agents | Changement de lieu par `agent.moved`, puis **réconciliation** à chaque `state.snapshot` | Même si un événement est perdu, l'agent rejoint la position que lui donne le moteur |
| Vitesse des déplacements | Durée réelle = durée simulée × intervalle **mesuré** entre deux pas | L'animation suit le rythme du moteur, même pour un run lancé hors de Godot |
| Domiciles | Maison la plus proche du point tiré par le moteur dans le quartier | Les maisons sont générées par Godot ; le moteur ne connaît que les quartiers |
| Indicateurs | Calculés par le **moteur** et publiés à chaque pas (`indicator.computed`) | Godot n'a qu'à afficher (B2) ; il ne calcule rien qui compte pour les résultats |
| Sélection d'un agent | Clic sans glisser, puis agent le plus proche **à l'écran** | Pas de physique à ajouter ; les glissers continuent de faire tourner la caméra |
| Leviers à curseur | Commande envoyée **au relâchement** du curseur | Évite une rafale de commandes pendant le glisser |
| Ancienne simulation Godot | **Retirée**, et sauvegardée dans `archives/maquette_godot_2026-10-13/` | Deux simulations différentes prêteraient à confusion |
| Lancement sous Windows | `lancer.bat` appelle `lancer.ps1` avec `-ExecutionPolicy Bypass` | La politique d'exécution du poste n'est pas définie (donc « Restricted ») : un `.ps1` lancé directement serait bloqué |

## 3. Fichiers créés, modifiés et supprimés

### Bridge et lancement
| Fichier | Rôle |
|---|---|
| `services/bridge.py` | **Nouveau.** Écoute Kafka (events, state, indicators, incidents) et relaie vers Godot en posant `t_proc` (C3). Reçoit de Godot les actions `lister`, `lancer`, `arreter` et `commande`. Fabrique et **valide** les `command.issued` avant de les publier ; une commande non conforme part en DLQ et l'erreur est renvoyée à Godot. Lance et surveille le moteur |
| `lancer.ps1` et `lancer.bat` | **Nouveaux.** Vérifient Docker, démarrent Kafka et attendent qu'il soit prêt, démarrent le bridge (sauf s'il tourne déjà), ouvrent Godot |
| `arreter.ps1` et `arreter.bat` | **Nouveaux.** Arrêtent le bridge et le moteur ; `-Kafka` arrête aussi Kafka |
| `.env.example` | Ajout de `GODOT_EXE`, le chemin de l'exécutable Godot |
| `requirements.txt` | Ajout de `websockets==17.2` |

### Moteur
| Fichier | Modification |
|---|---|
| `engine/moteur.py` | Publication à chaque pas de `indicator.computed` pour l'**ICC**, avec dans `sources` les événements de croyance du pas (base de F6), et pour le **basculement** quand son niveau change (Faible, Proche, Irréversible, Défaut) |

### Godot (`guichet/scripts/`)
| Fichier | État | Rôle |
|---|---|---|
| `main.gd` | **Réécrit** | Reçoit les événements, crée les agents à partir de `run.started`, les anime, gère les files, la sélection et le journal |
| `bridge_client.gd` | **Nouveau** | Client WebSocket avec reconnexion automatique toutes les 2 s |
| `agent_view.gd` | **Nouveau** (remplace `depositor.gd`) | Affichage d'un habitant : couleur selon son état, bulle, anneau de sélection, déplacement |
| `hud.gd` | **Réécrit** | Panneau de lancement, état de la Caisse, **courbe de l'ICC** avec le seuil 0,60, basculement, leviers, **inspection d'un agent** (D2), journal |
| `agency.gd` | **Simplifié** | Emplacements de la file et panneau « FERMÉE » ; plus de logique de service |
| `world_builder.gd`, `world_data.gd`, `camera_rig.gd` | Inchangés | — |
| `depositor.gd`, `caisse.gd`, `llm_client.gd`, `proxy/` | **Supprimés** | Logique déplacée dans le moteur ; le proxy Claude, payant, est abandonné. Tout est conservé dans `archives/` |

### Tests et documentation
| Fichier | Contenu |
|---|---|
| `tests/test_monde.py` | **Nouveau** : vérifie que `configs/monde.json` et `world_data.gd` ont les mêmes lieux, coordonnées et échelle |
| `guichet/README.md` | Réécrit pour le rôle de vue |
| `README.md` | Démarrage rapide avec `lancer.bat`, nouveaux dossiers |

## 4. Ce que montre Godot

| Zone | Contenu | Exigence |
|---|---|---|
| Carte | Habitants colorés selon leur état, qui se déplacent entre domicile, lieux de rencontre et files ; bulles de dialogue ; panneau « FERMÉE » sur les agences | D1 |
| Panneau gauche : lancement | Configuration, déposants, graine, rythme ; boutons Lancer, Arrêter, Pause | C2 |
| Panneau gauche : état | Heure simulée, état de la Caisse, barre des réserves, **basculement**, **ICC avec sa courbe**, répartition des habitants | B2, D1 |
| Panneau gauche : leviers | Garantie et plafond, retrait maximal, communiqué selon le ton, fermeture et réouverture des agences, consigne de l'employé, rumeur, pertes réelles | D3 |
| Panneau droit : inspection | Identité, type d'agent et modèle, état, croyances, dépôt, **dernière décision avec sa règle et sa justification**, derniers échanges | D2 |
| Panneau droit : journal | Article, communiqués, conversations des personnages principaux (ou toutes, au choix), échanges au guichet, décisions, commandes appliquées, défaut, bilan de fin de run | D1 |

## 5. Vérifications effectuées

| Vérification | Résultat |
|---|---|
| Tests automatiques | ✅ **22 tests** passent, dont la cohérence des cartes moteur et Godot |
| Bridge avec un faux client Godot (Python) | ✅ Configurations reçues ; run lancé ; **1 542 événements** relayés, tous avec `t_proc` ; communiqué envoyé et appliqué ; commande inconnue refusée avec un message clair ; fin du moteur signalée |
| Import du projet Godot sans fenêtre | ✅ Toutes les classes compilent (`AgentView`, `BridgeClient`, `Hud`…) |
| Godot sans fenêtre, connecté pendant un run réel | ✅ **Aucune erreur de script**. 65 agents créés, run suivi jusqu'au défaut du vendredi ; à la fin, 53 habitants « ont perdu », 10 « ont retiré », 2 membres du personnel, ce qui correspond au résumé du moteur |
| `lancer.ps1 -SansGodot` | ✅ Kafka prêt, bridge démarré ; un second lancement détecte le bridge déjà en marche |
| `arreter.ps1` | ✅ Arrête le bridge et le moteur |

### Problèmes rencontrés pendant les tests

| Problème | Cause | Correction |
|---|---|---|
| Bridge : traces d'erreur quand Godot est fermé brutalement | Fermeture sans message de clôture WebSocket | Les déconnexions brutales sont désormais traitées normalement |
| Résultats incohérents lors d'un test | **Deux bridges** lancés en même temps sur le même port | `lancer.ps1` vérifie que le port 8765 est libre ; `arreter.ps1` arrête tous les bridges |
| `lancer.ps1` s'arrêtait sur un simple message de Docker | Sous PowerShell 5.1, en mode `Stop`, toute ligne écrite par Docker sur stderr devient une erreur fatale | Contrôle explicite des codes de retour |
| Accents mal affichés par PowerShell | Script enregistré en UTF-8 sans BOM | Scripts `.ps1` enregistrés **avec BOM** |

## 6. À vérifier à l'écran

Les tests sans fenêtre prouvent que les scripts tournent sans erreur et que les données arrivent. Ils ne
prouvent pas que **le rendu est lisible**. À vérifier en lançant `lancer.bat` :

- [ ] Les habitants apparaissent près des maisons et se déplacent vers les lieux de rencontre.
- [ ] Le vendredi matin, des files se forment devant les agences et les couleurs virent à l'orange puis au rouge.
- [ ] La courbe de l'ICC monte dans le scénario `scenarios/emergence.yaml` et reste plate dans `scenarios/absence.yaml`.
- [ ] Un clic sur un habitant ouvre l'inspection, avec sa dernière décision et sa justification.
- [ ] Les leviers produisent une ligne « Commande … appliquée » dans le journal.
- [ ] Les panneaux ne masquent pas trop la carte en 1600 × 900.

Les retours sur ces points serviront à ajuster l'affichage en semaine 4.

## 7. Exigences avancées

| Exigence | Avancement |
|---|---|
| D1 | 🟡 Affichage en direct de la carte, des agents, de la Caisse et des indicateurs ; reste la validation à l'écran |
| D2 | 🟡 Inspection d'un agent à règles : état et règle appliquée. La mémoire des agents LLM viendra en semaine 8 |
| D3 | ✅ Injection d'événements depuis l'interface : leviers devenus commandes Kafka, effets visibles dans le journal et sur la carte |
| B2 | 🟡 ICC et basculement calculés pendant le run et affichés à côté de la scène, au rythme du moteur |
| C2 | ✅ Population choisie au lancement, depuis Godot ou la ligne de commande |
| C3 | 🟡 `t_proc` posé par le bridge à la réception |
| F6 | 🟡 Chaque valeur de l'ICC porte les événements de croyance du pas qui l'ont fait varier |

## 8. Limites connues

- Les agents se déplacent **en ligne droite** ; ils ne suivent pas les routes. Ce sera possible plus tard
  avec un `NavigationRegion3D`.
- Plusieurs habitants peuvent partager une même maison, avec un léger décalage.
- Les premiers événements d'un run peuvent arriver avant `run.started` (topics et partitions différents) :
  ils sont ignorés, puis le premier instantané remet chaque agent à sa place.
- Le **rejeu** d'un run passé n'est pas encore proposé dans Godot (semaine 10).
- Les commandes envoyées dans les premières secondes d'un run peuvent être perdues : le moteur rejoint le
  topic `guichet.commands` au démarrage.

## 9. Suite proposée

- **Semaine 4** : retours visuels de l'utilisateur, ajustements de lisibilité, affichage des sources de
  l'ICC (clic sur la courbe : les conversations qui ont précédé, base de B4), et un premier écran de
  comparaison des runs.
- **Semaines 6 et 7** : passerelle LLM et agents LLM.

## 10. Commandes utiles

```powershell
cd "C:\Users\LEGION\Desktop\EIGSI 5A\projet de parcours\guichet-godot"
.\lancer.bat                      # Kafka + bridge + Godot (double-clic possible)
.\lancer.bat -SansGodot           # Kafka + bridge seulement
.\arreter.bat                     # arrête le bridge et le moteur
.\arreter.bat -Kafka              # arrête aussi Kafka
.venv\Scripts\python -m services.bridge     # bridge seul, dans un terminal (journal visible)
.venv\Scripts\python -m pytest -q tests     # tests
```

Journaux : `data\logs\bridge.log`, `data\logs\moteur-<date>.log`, `data\logs\docker.log`.
