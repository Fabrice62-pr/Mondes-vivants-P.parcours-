# Plan de travail

Le sujet prévoit **8 semaines**. Une « semaine » de ce plan est une **étape de travail** : elle peut être
plus courte qu'une semaine de calendrier. Chaque étape se termine par un compte rendu dans `docs/journal/`.

Plan resserré le 13 octobre 2026. Le premier plan allait jusqu'à la semaine 10 : les étapes 6 à 10 ont été
regroupées pour tenir dans les 8 semaines du sujet.

| Semaine | Contenu | État |
|---|---|---|
| 1 | Cadrage, contrats d'événements, Kafka, hypothèses | ✅ |
| 2 | Moteur Python sans LLM, étalonnage, tests, Kafka de bout en bout | ✅ |
| 3 | Godot devient la vue : bridge WebSocket, HUD, inspection, leviers, lancement en un clic | ✅ |
| 4 | Lisibilité (déplacements, journal, domiciles), sources de l'ICC dans Godot (B4, F6), comparaison des runs | ✅ (validation à l'écran en attente) |
| 5 | Passerelle LLM et agents LLM (grands et petits modèles, repli sur les règles, quotas, incidents) | ✅ (validation à l'écran en attente) |
| 6 | Mémoire des agents et RAG (Chroma, embeddings locaux), mémoire visible dans l'inspection (D2) | ✅ (validation à l'écran en attente) |
| 7 | Mode asynchrone (C4), rejeu d'un run (E3), archiveur DuckDB/Parquet, service d'indicateurs | ✅ (validation à l'écran en attente) |
| 8 | Campagne d'expériences (H1 à H7, au moins 10 runs par scénario), coût (E5, O7), rapport, soutenance | |

## Correspondance avec l'ancien plan

| Ancien plan | Nouveau plan |
|---|---|
| Semaines 4 et 5 (Godot) | Semaines 3 et 4 |
| Semaines 6 et 7 (LLM) | Semaine 5 |
| Semaine 8 (mémoire) | Semaine 6 |
| Semaine 10 (asynchrone, rejeu, indicateurs) | Semaine 7 |

Les comptes rendus des semaines 2 et 3 citent l'ancienne numérotation ; ce tableau fait foi.
