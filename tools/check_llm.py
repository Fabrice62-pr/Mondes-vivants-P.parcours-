"""Vérifie les clés API et les modèles déclarés dans configs/models.yaml.

    .venv/Scripts/python tools/check_llm.py          # teste chaque modèle (1 petit appel chacun)
    .venv/Scripts/python tools/check_llm.py --list   # liste les modèles proposés par chaque fournisseur

Les clés sont lues dans .env (jamais affichées). Bibliothèque standard + pyyaml uniquement.
Deux familles d'API : « openai » (Groq, Mistral) et « gemini_native » (Google).
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SYSTEM = "Tu es un habitant d'une vallée fictive."
PROMPT = ('Réponds UNIQUEMENT avec ce JSON, sans texte autour : '
          '{"phrase": "<une phrase de salutation en français>", "ok": true}')
TIMEOUT_S = 90


def load_env() -> None:
    env = ROOT / ".env"
    if not env.exists():
        sys.exit("Fichier .env absent : copiez .env.example en .env et collez-y vos clés.")
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def headers(prov: dict, key: str) -> dict:
    h = {"Content-Type": "application/json",
         # Sans User-Agent explicite, Cloudflare (devant Groq) répond 403 / 1010.
         "User-Agent": "guichet-sim/0.1"}
    if prov["api"] == "gemini_native":
        h["x-goog-api-key"] = key
    else:
        h["Authorization"] = f"Bearer {key}"
    return h


def request(url: str, hdrs: dict, body: dict | None = None) -> tuple[int, dict | str, float]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET", headers=hdrs)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return r.status, json.load(r), time.perf_counter() - t0
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")[:300], time.perf_counter() - t0
    except Exception as e:  # réseau, délai
        return 0, str(e)[:300], time.perf_counter() - t0


def chat(prov: dict, key: str, m: dict) -> tuple[int, dict | str, float, str, int | str, int | str]:
    """Un appel minimal. Renvoie (code, données brutes, durée, texte, tokens entrée, tokens sortie)."""
    base = prov["base_url"].rstrip("/")
    user = f"{SYSTEM}\n\n{PROMPT}" if m.get("system_in_user") else PROMPT
    if prov["api"] == "gemini_native":
        body = {"contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"temperature": 0.3, "maxOutputTokens": 500}}
        if not m.get("system_in_user"):
            body["systemInstruction"] = {"parts": [{"text": SYSTEM}]}
        code, data, dt = request(f"{base}/models/{m['id']}:generateContent", headers(prov, key), body)
        if code != 200 or not isinstance(data, dict):
            return code, data, dt, "", "?", "?"
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        u = data.get("usageMetadata", {})
        return code, data, dt, text, u.get("promptTokenCount", "?"), u.get("candidatesTokenCount", "?")
    msgs = [{"role": "user", "content": user}] if m.get("system_in_user") else \
        [{"role": "system", "content": SYSTEM}, {"role": "user", "content": PROMPT}]
    # max_tokens large : les modèles à raisonnement consomment des tokens avant de répondre.
    body = {"model": m["id"], "messages": msgs, "max_tokens": 500, "temperature": 0.3, **m.get("extra", {})}
    code, data, dt = request(f"{base}/chat/completions", headers(prov, key), body)
    if code != 200 or not isinstance(data, dict):
        return code, data, dt, "", "?", "?"
    u = data.get("usage", {})
    text = data["choices"][0]["message"].get("content") or ""
    return code, data, dt, text, u.get("prompt_tokens", "?"), u.get("completion_tokens", "?")


def list_models(cat: dict) -> None:
    for name, prov in cat["providers"].items():
        key = os.environ.get(prov["env_key"])
        if not key:
            print(f"\n[{name}] clé {prov['env_key']} absente")
            continue
        code, data, _ = request(prov["base_url"].rstrip("/") + "/models", headers(prov, key))
        print(f"\n[{name}] HTTP {code}")
        if code != 200 or not isinstance(data, dict):
            print("  ", data)
            continue
        if prov["api"] == "gemini_native":
            ids = [x["name"].removeprefix("models/") for x in data.get("models", [])
                   if "generateContent" in x.get("supportedGenerationMethods", [])]
        else:
            ids = [x.get("id", "?") for x in data.get("data", [])]
        for i in sorted(ids):
            print("  ", i)


def test_models(cat: dict) -> bool:
    all_ok = True
    print(f"{'modèle':<15} {'fournisseur':<9} {'taille':>6}  {'statut':<22} {'latence':>8}  {'tokens':>9}")
    for name, m in {**cat["models"], **{f"aux:{k}": v for k, v in cat.get("auxiliary", {}).items()}}.items():
        prov = cat["providers"][m["provider"]]
        key = os.environ.get(prov["env_key"])
        size = f"{m['params_b']}B" if "params_b" in m else "—"
        if not key:
            print(f"{name:<15} {m['provider']:<9} {size:>6}  clé absente")
            all_ok = False
            continue
        code, data, dt, text, t_in, t_out = chat(prov, key, m)
        if code != 200:
            print(f"{name:<15} {m['provider']:<9} {size:>6}  ÉCHEC HTTP {code:<9} {dt:7.1f}s")
            print(f"{'':<17}{str(data)[:200]}")
            all_ok = False
            continue
        try:
            json.loads(text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip())
            status = "ok (JSON valide)"
        except ValueError:
            status = "ok (JSON INVALIDE)"
        print(f"{name:<15} {m['provider']:<9} {size:>6}  {status:<22} {dt:7.1f}s  {t_in}+{t_out}")
    return all_ok


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    load_env()
    catalog = yaml.safe_load((ROOT / "configs" / "models.yaml").read_text(encoding="utf-8"))
    if "--list" in sys.argv:
        list_models(catalog)
    else:
        sys.exit(0 if test_models(catalog) else 1)
