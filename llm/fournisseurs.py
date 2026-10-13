"""Adaptateurs HTTP des fournisseurs gratuits (contrainte G), repris de tools/check_llm.py.

Deux familles d'API :
  openai         Groq, Mistral : POST /chat/completions
  gemini_native  Google        : POST /models/<id>:generateContent (l'interface compatible OpenAI de
                                 Google renvoie HTTP 500 pour Gemma 4, constaté le 13/10/2026)

Bibliothèque standard uniquement. Les clés sont lues dans l'environnement et ne sont jamais écrites
dans un événement ni dans un journal.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

# Sans User-Agent explicite, Cloudflare (devant Groq) répond 403 / 1010.
USER_AGENT = "guichet-sim/0.1"


@dataclass
class Reponse:
    statut: int                       # code HTTP ; 0 = réseau ou délai dépassé
    texte: str = ""
    tokens_entree: int = 0
    tokens_sortie: int = 0
    latence_ms: int = 0
    erreur: str = ""
    attente_s: float | None = None    # délai demandé par le fournisseur après un 429 (retry-after)
    entetes: dict = field(default_factory=dict)


def _entetes(api: str, cle: str) -> dict:
    h = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    if api == "gemini_native":
        h["x-goog-api-key"] = cle
    else:
        h["Authorization"] = f"Bearer {cle}"
    return h


def _attente(entetes) -> float | None:
    """Délai conseillé après un 429 : retry-after, sinon x-ratelimit-reset-requests/tokens (« 2.5s », « 1m3s »)."""
    for k in ("retry-after", "x-ratelimit-reset-requests", "x-ratelimit-reset-tokens"):
        v = entetes.get(k)
        if not v:
            continue
        try:
            return float(v)
        except ValueError:
            pass
        unites = {"ms": 0.001, "s": 1, "m": 60, "h": 3600}
        total = sum(float(n) * unites[u] for n, u in re.findall(r"(\d+(?:\.\d+)?)(ms|h|m|s)", v))
        if total:
            return total
    return None


def _post(url: str, entetes: dict, corps: dict, delai_s: float) -> tuple[int, dict | str, dict, float]:
    req = urllib.request.Request(url, data=json.dumps(corps).encode("utf-8"), method="POST", headers=entetes)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=delai_s) as r:
            return r.status, json.load(r), {k.lower(): v for k, v in r.headers.items()}, time.perf_counter() - t0
    except urllib.error.HTTPError as e:
        h = {k.lower(): v for k, v in e.headers.items()} if e.headers else {}
        return e.code, e.read().decode("utf-8", "ignore")[:300], h, time.perf_counter() - t0
    except Exception as e:  # réseau, délai dépassé
        return 0, str(e)[:300], {}, time.perf_counter() - t0


class Fournisseur:
    """Un appel « système + utilisateur -> texte » vers un modèle du catalogue (configs/models.yaml)."""

    def __init__(self, nom: str, cfg: dict):
        self.nom = nom
        self.api = cfg["api"]
        self.base = cfg["base_url"].rstrip("/")
        self.env_key = cfg["env_key"]

    def disponible(self) -> bool:
        return bool(os.environ.get(self.env_key))

    def appeler(self, modele: dict, systeme: str, utilisateur: str, temperature: float, delai_s: float) -> Reponse:
        cle = os.environ.get(self.env_key, "")
        if not cle:
            return Reponse(0, erreur=f"clé {self.env_key} absente de .env")
        max_tokens = int(modele.get("max_tokens", 400))
        if modele.get("system_in_user"):
            utilisateur, systeme = f"{systeme}\n\n{utilisateur}", ""
        if self.api == "gemini_native":
            corps = {"contents": [{"role": "user", "parts": [{"text": utilisateur}]}],
                     "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens}}
            if systeme:
                corps["systemInstruction"] = {"parts": [{"text": systeme}]}
            if modele.get("json"):
                corps["generationConfig"]["responseMimeType"] = "application/json"
            code, data, h, dt = _post(f"{self.base}/models/{modele['id']}:generateContent",
                                      _entetes(self.api, cle), corps, delai_s)
            r = Reponse(code, latence_ms=int(dt * 1000), entetes=h)
            if code != 200 or not isinstance(data, dict):
                r.erreur, r.attente_s = str(data)[:300], _attente(h)
                return r
            try:
                parts = data["candidates"][0]["content"]["parts"]
                r.texte = "".join(p.get("text", "") for p in parts if not p.get("thought"))
            except (KeyError, IndexError):
                r.erreur = "réponse sans contenu"
            u = data.get("usageMetadata", {})
            r.tokens_entree, r.tokens_sortie = int(u.get("promptTokenCount", 0)), int(u.get("candidatesTokenCount", 0))
            return r
        messages = ([{"role": "system", "content": systeme}] if systeme else []) + \
                   [{"role": "user", "content": utilisateur}]
        corps = {"model": modele["id"], "messages": messages, "max_tokens": max_tokens,
                 "temperature": temperature, **modele.get("extra", {})}
        if modele.get("json"):
            corps["response_format"] = {"type": "json_object"}
        code, data, h, dt = _post(f"{self.base}/chat/completions", _entetes(self.api, cle), corps, delai_s)
        r = Reponse(code, latence_ms=int(dt * 1000), entetes=h)
        if code != 200 or not isinstance(data, dict):
            r.erreur, r.attente_s = str(data)[:300], _attente(h)
            return r
        try:
            r.texte = data["choices"][0]["message"].get("content") or ""
        except (KeyError, IndexError):
            r.erreur = "réponse sans contenu"
        u = data.get("usage", {})
        r.tokens_entree, r.tokens_sortie = int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0))
        return r
