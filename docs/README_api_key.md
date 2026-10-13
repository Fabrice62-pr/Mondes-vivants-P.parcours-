# Clés API et modèles LLM

Ce document décrit comment le projet GUICHET accède aux LLM gratuits : les clés, les fournisseurs, les
modèles retenus, les quotas, les problèmes rencontrés et leurs corrections, et l'attribution des modèles
aux agents.

**Dernière vérification** : 13 octobre 2026.

---

## 1. Pourquoi ces fournisseurs

La contrainte G du sujet impose des **LLM 100 % gratuits, par API ou en local**. L'API Claude, utilisée
par la maquette d'origine (`guichet/proxy/server.py`), est payante et a donc été abandonnée.

Trois fournisseurs aux offres gratuites sont utilisés **en parallèle** :

| Fournisseur | Console pour créer la clé | Offre | Type d'API utilisée |
|---|---|---|---|
| **Groq** | https://console.groq.com/keys | gratuite | compatible OpenAI |
| **Mistral** | https://console.mistral.ai/api-keys | gratuite « Experiment » | compatible OpenAI |
| **Google AI Studio** (Gemini, Gemma) | https://aistudio.google.com/apikey | gratuite | **native Gemini** (`generateContent`) |

Les combiner apporte plus de quota au total et une offre variée de tailles de modèles (A5).

> Les offres gratuites peuvent utiliser les requêtes pour entraîner leurs modèles. Comme le monde de
> GUICHET est entièrement fictif, ce n'est pas gênant, mais il faut le mentionner dans le rapport.

## 2. Où mettre les clés

### Fichiers

| Fichier | Rôle | Versionné dans git ? |
|---|---|---|
| `guichet-godot/.env.example` | Modèle vide qui donne la liste des variables attendues | Oui |
| `guichet-godot/.env` | **Les vraies clés** | **Non** : exclu par `.gitignore` |

Chemin complet : `C:\Users\LEGION\Desktop\EIGSI 5A\projet de parcours\guichet-godot\.env`

### Format

```
GEMINI_API_KEY=votre_cle_google
GROQ_API_KEY=votre_cle_groq
MISTRAL_API_KEY=votre_cle_mistral
KAFKA_BOOTSTRAP=localhost:9092
```

Mettez la clé juste après le `=`, sans espace ni guillemets.

### Règles de sécurité

- **Ne jamais mettre de clé dans le projet Godot** (`guichet/`) : tout ce qui est exporté par Godot peut
  être extrait.
- Ne jamais coller une clé dans une conversation, un rapport, une capture d'écran ou un commit.
- Ne jamais modifier `.env.example` avec de vraies clés : c'est un modèle partagé.
- Les outils lisent `.env` mais **n'affichent jamais les clés**.
- Si une clé a été exposée, révoquez-la dans la console du fournisseur et créez-en une nouvelle.
- Ne pas créer plusieurs comptes chez un même fournisseur pour contourner ses quotas : c'est contraire à
  ses conditions d'utilisation, et indéfendable en soutenance.

## 3. Outil de vérification : `tools/check_llm.py`

L'outil utilise la bibliothèque standard Python et `pyyaml`. Il lit `.env` et `configs/models.yaml`.

```powershell
cd "C:\Users\LEGION\Desktop\EIGSI 5A\projet de parcours\guichet-godot"
.venv\Scripts\python tools\check_llm.py --list   # liste les modèles proposés par chaque fournisseur
.venv\Scripts\python tools\check_llm.py          # 1 petit appel par modèle déclaré
```

Le second mode affiche, pour chaque modèle, son statut HTTP, si la réponse est un JSON valide, la latence
et les tokens consommés (entrée + sortie). Il se termine en erreur si un modèle échoue.

Chaque test complet consomme environ 5 requêtes et quelques centaines de tokens : c'est négligeable
devant les quotas.

## 4. Problèmes rencontrés et corrections

### 4.1 Groq : `HTTP 403, error code: 1010`

- **Symptôme** : `--list` renvoyait `[groq] HTTP 403 / error code: 1010`, alors que la clé était valide.
- **Cause** : Groq est protégé par Cloudflare, qui bloque l'identifiant client par défaut de Python
  (`User-Agent: Python-urllib/3.x`). Le code 1010 de Cloudflare signifie « signature de client refusée ».
- **Correction** : toutes les requêtes envoient `User-Agent: guichet-sim/0.1`.
- **À reprendre** dans la future passerelle LLM (`llm/`), quelle que soit la bibliothèque HTTP utilisée.

### 4.2 Les modèles prévus n'existent plus en offre gratuite

Le premier catalogue reposait sur ce qu'on connaissait de ces offres. La liste réelle obtenue avec
`--list` a montré que :

| Modèle prévu au départ | Fournisseur | État constaté |
|---|---|---|
| Llama 3.3 70B, Llama 3.1 8B | Groq | **Retirés** : Groq ne sert plus aucun Llama |
| Mistral Large 2411 (123B) | Mistral | **Retiré** |
| Gemma 3 12B | Google | **Retiré** (Gemma 4 l'a remplacé) |

Le catalogue a été reconstruit à partir des modèles réellement disponibles (§ 5).

### 4.3 Google : HTTP 500 sur Gemma 4

- **Symptôme** : `gemma-4-31b-it` renvoyait `500 Internal error`, y compris en plaçant l'instruction
  système dans le message utilisateur.
- **Diagnostic** : le même modèle appelé par l'**API native** de Google répond correctement. L'erreur vient
  de l'interface compatible OpenAI de Google, pas du modèle.
- **Correction** : Google est déclaré avec `api: gemini_native` dans `configs/models.yaml`. L'outil (puis
  la passerelle) appelle `POST /v1beta/models/<modèle>:generateContent` avec l'en-tête `x-goog-api-key`.
- `gemma-4-26b-a4b-it` renvoie aussi 500 en API native : il n'est pas utilisé.
- `gemini-2.5-flash-lite` renvoie 404 (« plus disponible pour les nouveaux utilisateurs ») : on utilise
  l'alias `gemini-flash-lite-latest`.

### 4.4 Google : lenteur et surcharge

- Latence mesurée : **28 à 46 s** pour Gemma 4 31B, **35 à 71 s** pour gemini-flash-lite.
- Erreurs ponctuelles `503 UNAVAILABLE` (« forte demande ») et un dépassement de délai de 60 s.
- **Conséquences** : le délai maximal des outils a été porté à 90 s, et Google n'est attribué qu'à un
  agent qui fait peu d'appels, la direction.

### 4.5 Modèles à raisonnement (gpt-oss)

Les modèles gpt-oss « réfléchissent » avant de répondre, ce qui consomme des tokens.
- On envoie `reasoning_effort: low`, déclaré dans `extra` dans `configs/models.yaml`.
- `max_tokens` est fixé à 500 pour qu'il reste de la place pour la réponse après la réflexion.

## 5. Modèles retenus

Source : `configs/models.yaml`.

Seuls des modèles de **taille publiée** sont attribués aux agents, puisque A5 pose comme hypothèse que la
taille représente la capacité cognitive.

| Clé interne | Identifiant API | Fournisseur | Catégorie | Taille publiée | Latence mesurée | JSON valide |
|---|---|---|---|---|---|---|
| `gpt-oss-120b` | `openai/gpt-oss-120b` | Groq | **grand** | 117 milliards (MoE, 5,1 actifs) | 0,4 à 1,0 s | ✅ |
| `gemma-31b` | `gemma-4-31b-it` | Google | **grand** | 31 milliards (dense) | 28 à 46 s | ✅ |
| `gpt-oss-20b` | `openai/gpt-oss-20b` | Groq | **petit** | 21 milliards (MoE, 3,6 actifs) | 0,3 à 0,7 s | ✅ |
| `ministral-8b` | `ministral-8b-2512` | Mistral | **petit** | 8 milliards (dense) | 0,7 à 0,9 s | ✅ |
| `aux:classifier` | `gemini-flash-lite-latest` | Google | auxiliaire | non publiée | 35 à 71 s | ✅ |

**Le modèle auxiliaire n'est attribué à aucun agent.** Il ne sert qu'à l'analyse, par exemple pour
classer les positions exprimées dans les messages pour l'indicateur de contexte commun. Comme sa taille
n'est pas publiée, il ne peut pas servir l'hypothèse A5.

**MoE** (« mixture of experts ») : seule une partie des paramètres sert pour chaque token. On rapporte la
taille totale et la taille active.

**Paire de même famille** : gpt-oss-120b et gpt-oss-20b ont la même architecture et le même
entraînement, avec un rapport de taille de 117 à 21. C'est la comparaison la plus propre pour isoler
l'effet de la taille (A5, H4, E5).

## 6. Quotas gratuits mesurés

Relevés le 13 octobre 2026 dans les en-têtes de réponse `x-ratelimit-*`. Les offres gratuites changent :
**revérifiez avant chaque campagne de runs.**

| Fournisseur | Requêtes | Tokens | Remarque |
|---|---|---|---|
| **Groq** | **1 000 par jour**, par modèle | **8 000 par minute**, par modèle | C'est la contrainte principale du projet |
| **Mistral** | 188 par minute | 625 000 par minute | Très large : Mistral absorbe le volume |
| **Google** | non communiqués | non communiqués | Lent et instable le jour de la mesure |

### Ce que ça permet

| Usage | Estimation | Conséquence |
|---|---|---|
| Personnages principaux (gpt-oss-120b) | ~250 à 350 appels par run | **Environ 2 à 3 runs complets par jour** |
| Petits modèles (~105 déposants LLM) | ~3 000 appels par run, dont environ 80 % sur Mistral | Mistral suffit ; gpt-oss-20b est limité à environ 1 run par jour avec sa part de 20 % |
| 8 000 tokens par minute sur Groq | ~1 000 tokens par appel avec mémoire et RAG | **Environ 8 appels par minute** par modèle Groq : la passerelle devra limiter le débit |
| Configuration E5 « tous sur le grand modèle » | impossible à 140 déposants | À faire tourner à **population réduite** (par exemple 40), en le justifiant (E2) |

Ces estimations seront remplacées par des mesures dès les premiers runs : chaque appel publie un
événement `llm.called` avec ses tokens et sa latence (E4).

## 7. Attribution des modèles aux agents

Source : `configs/principal.yaml`.

| Agent | Rôle | Modèle | Justification (rôle et expérience, jamais l'âge) |
|---|---|---|---|
| La boulangère | déposante | gpt-oss-120b | Gère seule la trésorerie de son commerce, entièrement déposée à la Caisse |
| Le retraité | déposant | gpt-oss-120b | Quarante ans d'expérience comme client de la Caisse |
| L'entrepreneur | déposant | gpt-oss-120b | Gère la trésorerie d'une entreprise et suit l'actualité économique |
| L'employé d'agence | employé | gpt-oss-120b | Rôle institutionnel : informe les clients au guichet |
| La direction | direction | gemma-31b | Rôle institutionnel : communiqués et mesures. Elle fait peu d'appels, donc la latence de Google est acceptable |
| Déposants ordinaires LLM (~75 %) | déposants | 80 % ministral-8b, 20 % gpt-oss-20b | Reçoivent et relaient l'information sans responsabilité particulière |
| Déposants à règles (~25 %) | déposants | aucun | Les moins exposés à l'information (A6, sobriété E5) |

### Règle : un agent ne change jamais de modèle

1. L'attribution est **fixée au lancement** à partir de la graine et de la configuration, puis publiée
   dans l'événement `run.started`.
2. La passerelle LLM ne prend le modèle **que** dans cette table. Une requête avec un autre modèle est
   refusée et comptée comme incident `modele_non_attribue`.
3. En cas d'échec (délai dépassé, quota, erreur 5xx, JSON invalide), la passerelle **refait un essai sur
   le même modèle**. Si l'échec persiste, l'agent décide cette fois avec sa règle
   (`decision.made.type = repli_regles`) et un `incident.raised` est publié.
   **On ne bascule jamais vers un autre modèle.**
4. Vérification après un run, qui doit renvoyer 0 ligne :
   ```sql
   SELECT agent_id FROM llm_calls WHERE run_id = ? GROUP BY agent_id HAVING COUNT(DISTINCT modele) > 1;
   ```

La répartition des petits modèles entre Mistral et Groq est un **choix de charge, pas un choix
cognitif**. Le tirage est équilibré à l'intérieur de chaque profil, pour qu'on ne confonde pas l'effet du
fournisseur avec celui du profil.

## 8. Passerelle LLM (`llm/`, semaine 5)

Réalisée en semaine 5 à partir de ce qui a été appris ici (détail : `docs/journal/README_semaine_05.md`) :

- [x] Deux adaptateurs : `openai` (Groq, Mistral) et `gemini_native` (Google) — `llm/fournisseurs.py`.
- [x] L'en-tête `User-Agent` sur toutes les requêtes.
- [x] Une limite de débit **par modèle** (`limites:` dans `configs/models.yaml`) : requêtes et tokens par
      minute, requêtes par jour. Le compteur du jour est gardé d'un run à l'autre dans
      `data/logs/llm_quota.json`. Après un 429, le modèle est bloqué pendant le délai indiqué par le
      fournisseur (`retry-after`, `x-ratelimit-reset-*`) — `llm/limites.py`.
- [x] `reasoning_effort: low` et `max_tokens: 700` pour les modèles gpt-oss.
- [x] `system_in_user` pour Gemma, et `max_tokens: 1500` : à 500, Gemma rendait des réponses vides ou
      tronquées.
- [x] Délai de réponse par modèle (`timeout_s`) : 30 s pour Groq et Mistral, 120 s pour Gemma.
- [x] Un nouvel essai sur le **même** modèle (avec un rappel du format si la réponse était refusée), puis le
      repli sur la règle, avec un incident tracé (A4).
- [x] Un événement `llm.called` par essai, avec les tokens, la latence, le statut et le numéro d'essai (E4).
- [ ] Un cache des réponses pendant le développement : pas fait. Pour économiser le quota, on lance les
      runs de mise au point avec `--sans-llm` ou une petite population.

**Constaté au premier run complet (13/10/2026)** : à la parution de l'article, une vingtaine d'agents sur
gpt-oss-20b réfléchissent au même pas. Les 8 000 tokens par minute de Groq ne suffisent pas : le pas
**attend** que le quota se libère (`attente_quota_max_s: 120` dans `configs/principal.yaml`), comme le veut
la barrière du mode synchrone. Au-delà, l'agent décide avec sa règle et un incident `quota` est publié.

## 9. Dépannage

| Symptôme | Cause probable | Que faire |
|---|---|---|
| `Fichier .env absent` | `.env` n'a pas été créé | `copy .env.example .env`, puis y coller les clés |
| `clé XXX absente` | Variable vide dans `.env` | Vérifier le nom exact et l'absence d'espace autour du `=` |
| `HTTP 401` | Clé invalide ou révoquée | Créer une nouvelle clé dans la console du fournisseur |
| `HTTP 403 / 1010` (Groq) | Pas de `User-Agent` | Vérifier que l'en-tête est bien envoyé |
| `HTTP 404` sur un modèle | Modèle retiré ou renommé | `check_llm.py --list`, puis mettre à jour `configs/models.yaml` |
| `HTTP 429` | Quota atteint | Attendre la remise à zéro (en-tête `x-ratelimit-reset-*`) ; en run, l'agent passe sur sa règle |
| `HTTP 500` (Google) | Interface compatible OpenAI | Vérifier `api: gemini_native` pour Google |
| `HTTP 503` (Google) | Forte demande temporaire | Réessayer plus tard |
| `JSON INVALIDE` | Le modèle ajoute du texte autour du JSON | Sera traité par la validation et la réparation dans la passerelle (A4) |
| `The read operation timed out` | Google très lent | Réessayer ; le délai est de 90 s dans `check_llm.py` |

## 10. Fichiers concernés

| Fichier | Rôle |
|---|---|
| `.env.example` | Modèle des variables, sans clé |
| `.env` | Les clés, jamais versionné |
| `.gitignore` | Exclut `.env` |
| `configs/models.yaml` | Fournisseurs, type d'API, modèles, tailles, catégories, quotas relevés |
| `configs/principal.yaml` | Attribution des modèles aux agents et paramètres LLM d'un run (budget, délai, essais) |
| `tools/check_llm.py` | Vérification des clés et des modèles |
| `docs/architecture.md` | Répartition des agents et justification (A5, A6) |
| `docs/journal/README_semaine_01.md` | Historique de la semaine 1, dont ce diagnostic (§ 7) |
