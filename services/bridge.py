"""Bridge entre Godot (WebSocket) et Kafka.

    python -m services.bridge          # écoute sur ws://127.0.0.1:8765

Kafka -> Godot : chaque événement des topics events, state, indicators et incidents est transmis tel quel,
avec `t_proc` (heure réelle de traitement, C3). À la connexion, Godot reçoit le dernier run.started, le
dernier instantané d'état et les derniers indicateurs : il peut se brancher en cours de run.

Godot -> bridge (messages JSON) :
  {"action": "lister"}                                   liste des configurations disponibles
  {"action": "lancer", "config": "principal.yaml", "deposants": 140, "graine": 1, "rythme": 0.5, "llm": true}
  {"action": "arreter"}                                  arrête le run lancé par le bridge
  {"action": "comparer"}                                 résumés et courbes de l'ICC des derniers runs
  {"action": "rejouer", "run_id": "...", "rythme": 0.5}  rejoue un run enregistré (E3), sans appel LLM
  {"action": "commande", "commande": "garantie", "parametres": {...}}   -> guichet.commands (D3)

Le bridge ne calcule rien et ne détient aucune clé API : il relaie.
"""
import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

from confluent_kafka import Consumer, Producer
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from engine.config import CONFIGS, ROOT
from engine.evenements import TOPIC_COMMANDES, TOPIC_DLQ, Fabrique, Validateur
from engine.run import charger_env
from services.comparaison import resumes_runs

TOPICS_VERS_GODOT = ["guichet.events", "guichet.state", "guichet.indicators", "guichet.incidents",
                     "guichet.llm.responses"]


def configurations() -> list[str]:
    out = []
    for p in sorted(CONFIGS.rglob("*.yaml")):
        rel = p.relative_to(CONFIGS).as_posix()
        if rel != "models.yaml":
            out.append(rel)
    return out


def maintenant() -> str:
    return datetime.now(timezone.utc).isoformat()


class Bridge:
    def __init__(self, bootstrap: str):
        self.bootstrap = bootstrap
        self.clients: dict = {}                     # websocket -> file d'envoi
        self.loop: asyncio.AbstractEventLoop | None = None
        self.run_started: dict | None = None
        self.instantane: dict | None = None
        self.indicateurs: dict[str, dict] = {}
        self.run_id: str | None = None
        self.t_sim = 0
        self.proc: subprocess.Popen | None = None
        self.validateur = Validateur()
        self.fab = Fabrique("aucun", "bridge", int(time.time()))
        self.producteur = Producer({"bootstrap.servers": bootstrap, "client.id": "bridge"})

    # ------------------------------------------------------------ Kafka -> Godot

    def ecouter_kafka(self) -> None:
        """Thread dédié : confluent-kafka est bloquant, on repasse chaque message à la boucle asyncio."""
        c = Consumer({"bootstrap.servers": self.bootstrap, "group.id": f"bridge-{os.getpid()}-{int(time.time())}",
                      "auto.offset.reset": "latest", "enable.auto.commit": False})
        c.subscribe(TOPICS_VERS_GODOT)
        while True:
            m = c.poll(0.2)
            if m is None or m.error():
                continue
            try:
                evt = json.loads(m.value())
            except ValueError:
                continue
            evt["t_proc"] = maintenant()
            self.loop.call_soon_threadsafe(self.recevoir, evt)

    def recevoir(self, evt: dict) -> None:
        typ = evt.get("type")
        if typ == "run.started":
            self.run_started, self.run_id, self.instantane, self.indicateurs = evt, evt["run_id"], None, {}
        elif typ == "state.snapshot":
            self.instantane = evt
        elif typ == "indicator.computed":
            self.indicateurs[evt["payload"]["nom"]] = evt
        self.t_sim = max(self.t_sim, evt.get("t_sim", 0)) if evt.get("run_id") == self.run_id else self.t_sim
        self.diffuser(evt)

    def diffuser(self, msg: dict) -> None:
        texte = json.dumps(msg, ensure_ascii=False)
        for file in self.clients.values():
            file.put_nowait(texte)

    # ------------------------------------------------------------ Godot -> bridge

    async def connexion(self, ws) -> None:
        file: asyncio.Queue = asyncio.Queue()
        self.clients[ws] = file
        ecrivain = asyncio.create_task(self._ecrire(ws, file))
        print(f"[bridge] Godot connecté ({len(self.clients)} client(s))", flush=True)
        file.put_nowait(json.dumps(self._hello(), ensure_ascii=False))
        for evt in [self.run_started, self.instantane, *self.indicateurs.values()]:
            if evt is not None:
                file.put_nowait(json.dumps(evt, ensure_ascii=False))
        try:
            async for brut in ws:
                await self.traiter(brut, file)
        except ConnectionClosed:
            pass                                    # Godot fermé brutalement : rien à faire
        finally:
            del self.clients[ws]
            ecrivain.cancel()
            print(f"[bridge] Godot déconnecté ({len(self.clients)} client(s))", flush=True)

    async def _ecrire(self, ws, file: asyncio.Queue) -> None:
        try:
            while True:
                await ws.send(await file.get())
        except ConnectionClosed:
            pass

    def _hello(self) -> dict:
        return {"type": "bridge.hello", "configs": configurations(), "run_actif": self.run_id,
                "moteur_lance": self.proc is not None and self.proc.poll() is None}

    def _statut(self, file: asyncio.Queue, etat: str, detail: str = "") -> None:
        file.put_nowait(json.dumps({"type": "bridge.statut", "etat": etat, "detail": detail}, ensure_ascii=False))

    async def traiter(self, brut: str, file: asyncio.Queue) -> None:
        try:
            msg = json.loads(brut)
            action = msg["action"]
        except (ValueError, KeyError, TypeError):
            self._statut(file, "erreur", "message illisible")
            return
        if action == "lister":
            file.put_nowait(json.dumps(self._hello(), ensure_ascii=False))
        elif action == "lancer":
            self._lancer(msg, file)
        elif action == "arreter":
            self._arreter(file)
        elif action == "commande":
            self._commande(msg, file)
        elif action == "rejouer":
            self._rejouer(msg, file)
        elif action == "comparer":
            runs = await asyncio.to_thread(resumes_runs, ROOT / "data" / "runs", int(msg.get("limite", 12)))
            file.put_nowait(json.dumps({"type": "bridge.runs", "runs": runs}, ensure_ascii=False))
        else:
            self._statut(file, "erreur", f"action inconnue : {action}")

    def _lancer(self, msg: dict, file: asyncio.Queue) -> None:
        config = msg.get("config", "principal.yaml")
        if config not in configurations():
            self._statut(file, "erreur", f"configuration inconnue : {config}")
            return
        self._arreter(None)
        args = [sys.executable, "-m", "engine.run", "--config", config, "--puits", "kafka,jsonl",
                "--rythme", str(float(msg.get("rythme", 0.5)))]
        if msg.get("deposants"):
            args += ["--deposants", str(int(msg["deposants"]))]
        if msg.get("graine"):
            args += ["--graines", str(int(msg["graine"]))]
        if msg.get("llm") is False:
            args.append("--sans-llm")
        if msg.get("mode") in ("synchrone", "asynchrone"):
            args += ["--mode", msg["mode"]]
        self._demarrer(args, "moteur")
        self._statut(file, "lance", config)

    def _demarrer(self, args: list[str], nom: str) -> None:
        journaux = ROOT / "data" / "logs"
        journaux.mkdir(parents=True, exist_ok=True)
        sortie = (journaux / f"{nom}-{datetime.now():%Y%m%d-%H%M%S}.log").open("w", encoding="utf-8")
        drapeaux = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.proc = subprocess.Popen(args, cwd=ROOT, stdout=sortie, stderr=subprocess.STDOUT,
                                     env={**os.environ, "PYTHONIOENCODING": "utf-8"}, creationflags=drapeaux)
        print(f"[bridge] {nom} lancé : {' '.join(args[2:])}", flush=True)
        asyncio.create_task(self._surveiller(self.proc))

    def _rejouer(self, msg: dict, file: asyncio.Queue) -> None:
        run_id = str(msg.get("run_id", ""))
        dossier = ROOT / "data" / "runs" / run_id
        if not run_id or "/" in run_id or "\\" in run_id or not (dossier / "events.jsonl").exists():
            self._statut(file, "erreur", f"run inconnu : {run_id}")
            return
        self._arreter(None)
        self._demarrer([sys.executable, "-m", "services.rejeu", "--run", run_id,
                        "--rythme", str(float(msg.get("rythme", 0.5)))], "rejeu")
        self._statut(file, "rejeu", run_id)

    async def _surveiller(self, proc: subprocess.Popen) -> None:
        while proc.poll() is None:
            await asyncio.sleep(0.5)
        if proc is self.proc:
            self.diffuser({"type": "bridge.statut", "etat": "termine", "detail": f"code {proc.returncode}"})

    def _arreter(self, file: asyncio.Queue | None) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            print("[bridge] moteur arrêté", flush=True)
            if file is not None:
                self._statut(file, "arrete")

    def _commande(self, msg: dict, file: asyncio.Queue) -> None:
        self.fab.run_id = self.run_id or "aucun"
        evt = self.fab.creer("command.issued", self.t_sim, {"commande": msg.get("commande"),
                                                            "parametres": msg.get("parametres") or {}})
        errs = self.validateur.erreurs(evt)
        if errs:
            self.producteur.produce(TOPIC_DLQ, json.dumps({"brut": evt, "erreurs": errs, "t_rejet": maintenant()},
                                                          ensure_ascii=False).encode("utf-8"))
            self._statut(file, "erreur", "; ".join(errs))
        else:
            self.producteur.produce(TOPIC_COMMANDES, json.dumps(evt, ensure_ascii=False).encode("utf-8"), key=b"monde")
            self._statut(file, "commande_envoyee", msg.get("commande", ""))
        self.producteur.poll(0)


async def principal() -> None:
    charger_env()
    bootstrap = os.environ.get("KAFKA_BOOTSTRAP", "localhost:9092")
    port = int(os.environ.get("GUICHET_BRIDGE_PORT", "8765"))
    b = Bridge(bootstrap)
    b.loop = asyncio.get_running_loop()
    threading.Thread(target=b.ecouter_kafka, daemon=True).start()
    async with serve(b.connexion, "127.0.0.1", port, max_size=2 ** 22):
        print(f"[bridge] prêt sur ws://127.0.0.1:{port} (Kafka {bootstrap})", flush=True)
        await asyncio.Future()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        asyncio.run(principal())
    except KeyboardInterrupt:
        pass
