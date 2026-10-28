"""Bridge entre Godot et Kafka, servi par FastAPI.

    python -m services.bridge          # http://127.0.0.1:8765 ; documentation Swagger sur /docs

WebSocket /ws (Godot)
  Kafka -> Godot : chaque événement des topics events, state, indicators, incidents et llm.responses est
  transmis tel quel, avec `t_proc` (heure réelle de traitement, C3). À la connexion, Godot reçoit le dernier
  run.started, le dernier instantané d'état et les derniers indicateurs : il peut se brancher en cours de run.

  Godot -> bridge : messages JSON, vérifiés par les modèles Pydantic ci-dessous (le champ `action` choisit
  le modèle ; un champ inconnu ou une valeur hors bornes est refusé avec un bridge.statut « erreur ») :
    {"action": "lister"}                                   liste des configurations disponibles
    {"action": "lancer", "config": "principal.yaml", "deposants": 140, "graine": 1, "rythme": 0.5,
     "llm": true, "mode": "hybride", "echeance": 3}       mode : synchrone | asynchrone | hybride
    {"action": "arreter"}                                  arrête le run lancé par le bridge
    {"action": "comparer"}                                 résumés et courbes de l'ICC des derniers runs
    {"action": "rejouer", "run_id": "...", "rythme": 0.5}  rejoue un run enregistré (E3), sans appel LLM
    {"action": "commande", "commande": "garantie", "parametres": {...}}   -> guichet.commands (D3)

  Le lancement, le rejeu, l'arrêt et la fin d'un run sont annoncés à tous les clients (bridge.statut) ;
  une erreur n'est renvoyée qu'au client qui l'a provoquée.

Routes REST (les mêmes actions, pour Swagger, les tests et les scripts)
  GET /sante, GET /configurations, POST /runs, DELETE /runs/actif, POST /runs/{run_id}/rejeu,
  GET /runs/comparaison, POST /commandes

Le bridge ne calcule rien et ne détient aucune clé API : il relaie.
"""
import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated, Callable, Literal

from confluent_kafka import Consumer, KafkaException, Producer
from fastapi import APIRouter, Depends, FastAPI, Path, WebSocket, WebSocketDisconnect
from fastapi.requests import HTTPConnection
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from engine.config import CONFIGS, ROOT
from engine.evenements import TOPIC_COMMANDES, TOPIC_DLQ, Fabrique, Validateur
from engine.run import charger_env
from services.comparaison import resumes_runs

TOPICS_VERS_GODOT = ["guichet.events", "guichet.state", "guichet.indicators", "guichet.incidents",
                     "guichet.llm.responses"]
RUN_ID = r"^[\w-][\w.-]*$"                         # pas de « / », « \ » ni « .. » : dossier de data/runs


def configurations() -> list[str]:
    out = []
    for p in sorted(CONFIGS.rglob("*.yaml")):
        rel = p.relative_to(CONFIGS).as_posix()
        if rel != "models.yaml":
            out.append(rel)
    return out


def maintenant() -> str:
    return datetime.now(timezone.utc).isoformat()


def cloturer_runs(debut: float, fin: float) -> None:
    """Après l'arrêt du moteur (bouton « Arrêter », fermeture, plantage) : le run lancé pendant [debut, fin]
    n'a pas écrit son résumé ; on le reconstitue depuis son historique, puis on l'archive."""
    from services.archiveur import archiver_run
    from services.reconstitution import RUNS, a_reconstituer, reconstituer
    try:
        for d in a_reconstituer(RUNS, entre=(debut, fin)):
            if reconstituer(d) is not None:
                archiver_run(d)
                print(f"[bridge] run interrompu résumé et archivé : {d.name}", flush=True)
    except Exception as e:                                # n'empêche jamais le bridge de continuer
        print(f"[bridge] résumé du run interrompu impossible : {e}", flush=True)


def statut(etat: str, detail: str = "") -> dict:
    return {"type": "bridge.statut", "etat": etat, "detail": detail}


def _texte(msg: dict) -> str:
    return json.dumps(msg, ensure_ascii=False)


# ================================================================ messages reçus (Pydantic)

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Lancement(_Strict):
    """Paramètres d'un run ; un paramètre absent prend la valeur de la configuration."""
    config: str = "principal.yaml"
    deposants: int | None = Field(None, ge=1, le=2000)
    graine: int | None = Field(None, ge=0)
    rythme: float = Field(0.5, ge=0, le=60, description="secondes réelles par pas de 15 min simulées")
    llm: bool = True
    mode: Literal["synchrone", "asynchrone", "hybride"] | None = None
    echeance: float | None = Field(None, ge=0, le=600, description="mode hybride : attente maximale par pas (s)")
    systeme1: Literal["aucun", "regle", "nli", "appris"] | None = Field(None, description="Système 1 des déposants LLM")
    etiquette: str | None = Field(None, max_length=60, pattern=r"^[\w.-]+$",
                                  description="série de runs à comparer (ex. reference, s1-regle)")


class Rejeu(_Strict):
    rythme: float = Field(0.5, ge=0, le=60)


class Commande(_Strict):
    """Commande vers le moteur ; son contenu est vérifié par le schéma de command.issued (D3)."""
    commande: str
    parametres: dict = Field(default_factory=dict)


class Lister(_Strict):
    action: Literal["lister"]


class Lancer(Lancement):
    action: Literal["lancer"]


class Arreter(_Strict):
    action: Literal["arreter"]


class Comparer(_Strict):
    action: Literal["comparer"]
    limite: int = Field(12, ge=1, le=100)


class Rejouer(Rejeu):
    action: Literal["rejouer"]
    run_id: str = Field(pattern=RUN_ID)


class EnvoyerCommande(Commande):
    action: Literal["commande"]


ACTIONS = TypeAdapter(Annotated[Lister | Lancer | Arreter | Comparer | Rejouer | EnvoyerCommande,
                                Field(discriminator="action")])


def _erreurs(e: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(x) for x in err['loc']) or 'message'} : {err['msg']}" for err in e.errors())


class ErreurBridge(Exception):
    def __init__(self, detail: str, code: int = 400):
        super().__init__(detail)
        self.detail, self.code = detail, code


# ================================================================ le bridge

class Bridge:
    def __init__(self, bootstrap: str, producteur=None):
        self.bootstrap = bootstrap
        self.clients: dict[WebSocket, asyncio.Queue] = {}     # websocket -> file d'envoi
        self.loop: asyncio.AbstractEventLoop | None = None
        self.run_started: dict | None = None
        self.instantane: dict | None = None
        self.indicateurs: dict[str, dict] = {}
        self.run_id: str | None = None
        self.t_sim = 0
        self.proc: subprocess.Popen | None = None
        self.validateur = Validateur()
        self.fab = Fabrique("aucun", "bridge", int(time.time()))
        self.producteur = producteur or Producer({"bootstrap.servers": bootstrap, "client.id": "bridge"})

    # ------------------------------------------------------------ Kafka -> Godot

    def ecouter_kafka(self, arret: threading.Event) -> None:
        """Thread dédié : confluent-kafka est bloquant, on repasse chaque message à la boucle asyncio."""
        c = Consumer({"bootstrap.servers": self.bootstrap, "group.id": f"bridge-{os.getpid()}-{int(time.time())}",
                      "auto.offset.reset": "latest", "enable.auto.commit": False})
        c.subscribe(TOPICS_VERS_GODOT)
        try:
            while not arret.is_set():
                m = c.poll(0.2)
                if m is None or m.error():
                    continue
                try:
                    evt = json.loads(m.value())
                except ValueError:
                    continue
                evt["t_proc"] = maintenant()
                self.loop.call_soon_threadsafe(self.recevoir, evt)
        finally:
            c.close()

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
        texte = _texte(msg)
        for file in self.clients.values():
            file.put_nowait(texte)

    def memorises(self) -> list[dict]:
        """Ce qu'un client qui se connecte en cours de run doit recevoir pour rattraper l'état."""
        return [e for e in [self.run_started, self.instantane, *self.indicateurs.values()] if e is not None]

    # ------------------------------------------------------------ Godot -> bridge (WebSocket)

    async def traiter(self, brut: str) -> dict | None:
        """Message de Godot -> réponse pour lui seul (None : la réponse a été diffusée à tous)."""
        try:
            match ACTIONS.validate_json(brut):
                case Lister():
                    return self.hello()
                case Lancer() as a:
                    self.lancer(a)
                case Arreter():
                    self.arreter()
                case Comparer() as a:
                    return await self.comparer(a.limite)
                case Rejouer() as a:
                    self.rejouer(a.run_id, a)
                case EnvoyerCommande() as a:
                    return self.commande(a)
        except ValidationError as e:
            return statut("erreur", f"message refusé : {_erreurs(e)}")
        except ErreurBridge as e:
            return statut("erreur", e.detail)
        return None

    # ------------------------------------------------------------ actions (WebSocket et REST)

    def hello(self) -> dict:
        return {"type": "bridge.hello", "configs": configurations(), "run_actif": self.run_id,
                "moteur_lance": self.moteur_lance()}

    def moteur_lance(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def kafka_joignable(self) -> bool:
        try:
            self.producteur.list_topics(timeout=2)
            return True
        except KafkaException:
            return False

    def _annoncer(self, etat: str, detail: str = "") -> dict:
        msg = statut(etat, detail)
        self.diffuser(msg)
        return msg

    def lancer(self, p: Lancement) -> dict:
        if p.config not in configurations():
            raise ErreurBridge(f"configuration inconnue : {p.config}", 404)
        self._stopper()
        args = [sys.executable, "-m", "engine.run", "--config", p.config, "--puits", "kafka,jsonl",
                "--rythme", str(p.rythme)]
        if p.deposants is not None:
            args += ["--deposants", str(p.deposants)]
        if p.graine is not None:
            args += ["--graines", str(p.graine)]
        if not p.llm:
            args.append("--sans-llm")
        if p.mode is not None:
            args += ["--mode", p.mode]
        if p.echeance is not None:
            args += ["--echeance", str(p.echeance)]
        if p.systeme1 is not None:
            args += ["--systeme1", p.systeme1]
        if p.etiquette is not None:
            args += ["--etiquette", p.etiquette]
        self._demarrer(args, "moteur")
        return self._annoncer("lance", p.config)

    def rejouer(self, run_id: str, p: Rejeu) -> dict:
        if not (ROOT / "data" / "runs" / run_id / "events.jsonl").exists():
            raise ErreurBridge(f"run inconnu : {run_id}", 404)
        self._stopper()
        self._demarrer([sys.executable, "-m", "services.rejeu", "--run", run_id, "--rythme", str(p.rythme)], "rejeu")
        return self._annoncer("rejeu", run_id)

    def arreter(self) -> dict:
        if not self._stopper():
            raise ErreurBridge("aucun run en cours", 404)
        return self._annoncer("arrete")

    async def comparer(self, limite: int) -> dict:
        runs = await asyncio.to_thread(resumes_runs, ROOT / "data" / "runs", limite)
        return {"type": "bridge.runs", "runs": runs}

    def commande(self, c: Commande) -> dict:
        self.fab.run_id = self.run_id or "aucun"
        evt = self.fab.creer("command.issued", self.t_sim, {"commande": c.commande, "parametres": c.parametres})
        errs = self.validateur.erreurs(evt)
        if errs:
            self.producteur.produce(TOPIC_DLQ, _texte({"brut": evt, "erreurs": errs, "t_rejet": maintenant()})
                                    .encode("utf-8"))
            self.producteur.poll(0)
            raise ErreurBridge("; ".join(errs), 422)
        self.producteur.produce(TOPIC_COMMANDES, _texte(evt).encode("utf-8"), key=b"monde")
        self.producteur.poll(0)
        return statut("commande_envoyee", c.commande)

    # ------------------------------------------------------------ processus du moteur ou du rejeu

    def _demarrer(self, args: list[str], nom: str) -> None:
        journaux = ROOT / "data" / "logs"
        journaux.mkdir(parents=True, exist_ok=True)
        sortie = (journaux / f"{nom}-{datetime.now():%Y%m%d-%H%M%S}.log").open("w", encoding="utf-8")
        drapeaux = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.proc = subprocess.Popen(args, cwd=ROOT, stdout=sortie, stderr=subprocess.STDOUT,
                                     env={**os.environ, "PYTHONIOENCODING": "utf-8"}, creationflags=drapeaux)
        print(f"[bridge] {nom} lancé : {' '.join(args[2:])}", flush=True)
        asyncio.create_task(self._surveiller(self.proc, nom, time.time()))

    async def _surveiller(self, proc: subprocess.Popen, nom: str, debut: float) -> None:
        while proc.poll() is None:
            await asyncio.sleep(0.5)
        if nom == "moteur":
            await asyncio.to_thread(cloturer_runs, debut, time.time())
        if proc is self.proc:
            self._annoncer("termine", f"code {proc.returncode}")

    def _stopper(self) -> bool:
        """Arrête le moteur ou le rejeu en cours ; False s'il n'y en avait pas."""
        if not self.moteur_lance():
            return False
        self.proc.terminate()
        print("[bridge] moteur arrêté", flush=True)
        return True


# ================================================================ application FastAPI

routes = APIRouter()


def le_bridge(conn: HTTPConnection) -> Bridge:
    return conn.app.state.bridge


BridgeDep = Annotated[Bridge, Depends(le_bridge)]


@routes.websocket("/ws")
async def flux(ws: WebSocket, b: BridgeDep) -> None:
    """Flux Kafka vers Godot et actions de Godot (voir l'en-tête du module)."""
    await ws.accept()
    file: asyncio.Queue = asyncio.Queue()
    b.clients[ws] = file
    ecrivain = asyncio.create_task(_ecrire(ws, file))
    print(f"[bridge] Godot connecté ({len(b.clients)} client(s))", flush=True)
    for msg in [b.hello(), *b.memorises()]:
        file.put_nowait(_texte(msg))
    try:
        async for brut in ws.iter_text():          # s'arrête quand Godot ferme, même brutalement
            reponse = await b.traiter(brut)
            if reponse is not None:
                file.put_nowait(_texte(reponse))
    finally:
        del b.clients[ws]
        ecrivain.cancel()
        print(f"[bridge] Godot déconnecté ({len(b.clients)} client(s))", flush=True)


async def _ecrire(ws: WebSocket, file: asyncio.Queue) -> None:
    try:
        while True:
            await ws.send_text(await file.get())
    except (WebSocketDisconnect, RuntimeError):     # RuntimeError : envoi après la fermeture
        pass


@routes.get("/", include_in_schema=False)
async def accueil() -> RedirectResponse:
    return RedirectResponse("/docs")


@routes.get("/sante", tags=["bridge"])
async def sante(b: BridgeDep) -> dict:
    """État du bridge et de sa connexion à Kafka."""
    return {"bridge": "ok", "kafka": await asyncio.to_thread(b.kafka_joignable), "clients": len(b.clients),
            "run_actif": b.run_id, "moteur_lance": b.moteur_lance(), "t_sim": b.t_sim}


@routes.get("/configurations", tags=["runs"])
async def lister_configurations() -> list[str]:
    return configurations()


@routes.post("/runs", tags=["runs"])
async def lancer_run(p: Lancement, b: BridgeDep) -> dict:
    """Lance le moteur (le run en cours est arrêté)."""
    return b.lancer(p)


@routes.delete("/runs/actif", tags=["runs"])
async def arreter_run(b: BridgeDep) -> dict:
    return b.arreter()


@routes.post("/runs/{run_id}/rejeu", tags=["runs"])
async def rejouer_run(run_id: Annotated[str, Path(pattern=RUN_ID)], b: BridgeDep, p: Rejeu | None = None) -> dict:
    """Rejoue un run enregistré (E3), sans appel LLM."""
    return b.rejouer(run_id, p or Rejeu())


@routes.get("/runs/comparaison", tags=["runs"])
async def comparer_runs(b: BridgeDep, limite: int = 12) -> dict:
    """Résumés et courbes de l'ICC des derniers runs terminés."""
    return await b.comparer(max(1, min(limite, 100)))


@routes.post("/commandes", tags=["moteur"])
async def envoyer_commande(c: Commande, b: BridgeDep) -> dict:
    """Commande vers le moteur (topic guichet.commands) ; non conforme : DLQ et erreur 422."""
    return b.commande(c)


async def _erreur_bridge(_, e: ErreurBridge) -> JSONResponse:
    return JSONResponse(statut("erreur", e.detail), status_code=e.code)


def creer_app(fabrique: Callable[[], Bridge] | None = None, kafka: bool = True) -> FastAPI:
    """`fabrique` et `kafka=False` servent aux tests : un bridge sans Kafka réel."""
    @asynccontextmanager
    async def cycle(app: FastAPI):
        charger_env()
        b = fabrique() if fabrique else Bridge(os.environ.get("KAFKA_BOOTSTRAP", "localhost:9092"))
        b.loop = asyncio.get_running_loop()
        app.state.bridge = b
        arret = threading.Event()
        if kafka:
            threading.Thread(target=b.ecouter_kafka, args=(arret,), daemon=True).start()
            print(f"[bridge] prêt (Kafka {b.bootstrap}) ; Godot sur /ws, Swagger sur /docs", flush=True)
        yield
        arret.set()
        b.producteur.flush(2)

    app = FastAPI(title="Bridge GUICHET", version="2.0",
                  description="Relais entre Godot et Kafka : flux d'événements (WebSocket /ws), lancement des "
                              "runs et des rejeux, commandes vers le moteur.", lifespan=cycle)
    app.include_router(routes)
    app.add_exception_handler(ErreurBridge, _erreur_bridge)
    return app


app = creer_app()


if __name__ == "__main__":
    import uvicorn
    sys.stdout.reconfigure(encoding="utf-8")
    charger_env()
    port = int(os.environ.get("GUICHET_BRIDGE_PORT", "8765"))
    uvicorn.run(app, host="127.0.0.1", port=port, ws="websockets-sansio")
