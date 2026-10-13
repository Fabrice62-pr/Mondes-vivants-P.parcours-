"""
Proxy local entre le jeu Godot et l'API Claude.

Le jeu n'a JAMAIS la clé API : elle reste ici, dans une variable d'environnement.
Aucune dépendance externe (bibliothèque standard Python 3.9+).

Lancement :
    export ANTHROPIC_API_KEY="votre_clé"        (Windows PowerShell : $env:ANTHROPIC_API_KEY="votre_clé")
    python proxy/server.py

Le jeu appelle ensuite http://127.0.0.1:8787/dialogue
"""
import json
import os
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = os.environ.get("GUICHET_MODEL", "claude-haiku-4-5-20251001")
PORT = int(os.environ.get("GUICHET_PORT", "8787"))
MAX_PER_MINUTE = int(os.environ.get("GUICHET_MAX_PER_MINUTE", "40"))

SYSTEM = """Tu fais parler deux habitants de la vallée de Brévanne, dont toute l'épargne est à la
Caisse Coopérative de Brévanne. Un article a affirmé qu'un gros emprunteur de la Caisse est en
difficulté. Tu n'es PAS un économiste : chaque habitant raisonne avec ses propres intérêts,
sa loyauté envers la Caisse et sa peur d'être le dernier servi au guichet. Il se demande surtout
ce que les AUTRES vont faire. Il peut croire un communiqué, s'en méfier, ou s'inquiéter des files
d'attente qu'il a vues. Varie les réactions selon les profils : deux personnes différentes ne
réagissent pas pareil à la même nouvelle. Pas de jargon ("prophétie autoréalisatrice", "Diamond-Dybvig").

Réponds UNIQUEMENT avec un objet JSON, sans texte autour ni balises :
{"phrase": "<ce que dit le locuteur, 25 mots max>",
 "reaction": "<réponse de l'auditeur, 20 mots max>",
 "delta_peur_auditeur": <nombre entre -0.3 et 0.3>,
 "intention_auditeur": "retirer" | "attendre" | "rassurer"}"""

_calls = []


def call_claude(context: dict) -> dict:
    user = "Situation (JSON) :\n" + json.dumps(context, ensure_ascii=False, indent=1)
    body = json.dumps({
        "model": MODEL,
        "max_tokens": 300,
        "system": SYSTEM,
        "messages": [{"role": "user", "content": user}],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    out = json.loads(text)
    out["delta_peur_auditeur"] = max(-0.3, min(0.3, float(out.get("delta_peur_auditeur", 0))))
    if out.get("intention_auditeur") not in ("retirer", "attendre", "rassurer"):
        out["intention_auditeur"] = "attendre"
    return out


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: dict) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:
        if self.path != "/dialogue":
            self._send(404, {"erreur": "route inconnue"})
            return
        now = time.time()
        while _calls and now - _calls[0] > 60:
            _calls.pop(0)
        if len(_calls) >= MAX_PER_MINUTE:
            self._send(429, {"erreur": "trop de requêtes"})
            return
        _calls.append(now)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            context = json.loads(self.rfile.read(length) or b"{}")
            self._send(200, call_claude(context))
        except urllib.error.HTTPError as e:
            self._send(502, {"erreur": f"API {e.code}", "detail": e.read().decode("utf-8", "ignore")[:300]})
        except Exception as e:  # réponse illisible, réseau, etc.
            self._send(502, {"erreur": str(e)[:300]})

    def log_message(self, fmt, *args):
        print("[proxy]", fmt % args)


if __name__ == "__main__":
    if not API_KEY:
        raise SystemExit("Définissez ANTHROPIC_API_KEY avant de lancer le proxy.")
    print(f"Proxy GUICHET sur http://127.0.0.1:{PORT}/dialogue (modèle {MODEL})")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
