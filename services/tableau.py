"""Tableau de bord de l'IA : décisions des agents, confiance et fiabilité, en direct et sur l'historique.

    python -m services.tableau            # http://127.0.0.1:8770 (port : GUICHET_TABLEAU_PORT)

Deux sources, les mêmes métriques (services/metriques.py) :
  - **direct** : les événements reçus de Kafka sont rangés dans une base DuckDB en mémoire (table `ev`).
    Les tables de l'archiveur (decisions, croyances...) en sont des vues, construites avec les mêmes
    projections que l'archive (services.archiveur.selection). Un run déjà commencé quand le tableau de bord
    démarre est rattrapé depuis son data/runs/<run_id>/events.jsonl ;
  - **historique** : les Parquet des runs archivés (services.archiveur.ouvrir), relus quand un run s'ajoute.

Le navigateur reçoit les métriques du direct par WebSocket (/ws), seulement quand de nouveaux événements sont
arrivés ; l'historique et la fiche d'une décision passent par l'API REST (/api, documentée sur /docs).
"""
import asyncio
import json
import os
import sys
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import duckdb
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from fastapi.staticfiles import StaticFiles

from services import metriques
from services.archiveur import COLONNES_EV, RUNS, TABLES, lire_enveloppes, ouvrir, selection

# Tout sauf guichet.state : les instantanés d'état ne sont pas de l'historique (l'archiveur les ignore aussi).
TOPICS = ["guichet.events", "guichet.indicators", "guichet.incidents", "guichet.llm.responses"]
WEB = Path(__file__).resolve().parent / "tableau_web"
INTERVALLE_S = 1.0          # fréquence maximale des envois aux navigateurs


# ================================================================ source « direct »

class Direct:
    """Les événements reçus de Kafka, dans une base DuckDB en mémoire, avec les tables de l'archiveur en vues."""

    def __init__(self, runs: Path = RUNS):
        self.runs = runs
        self.con = duckdb.connect()
        self.con.execute(f"CREATE TABLE ev ({', '.join(f'{k} {v}' for k, v in COLONNES_EV.items())})")
        for nom in [*TABLES, "agents"]:
            self.con.execute(f"CREATE VIEW {nom} AS {selection(nom)}")
        self.version = 0                       # augmente à chaque arrivée d'événements
        self._verrou = threading.Lock()        # la connexion DuckDB n'est utilisée que par un fil à la fois
        self._verrou_attente = threading.Lock()
        self._attente: list[dict] = []
        self._tmp = Path(tempfile.mkdtemp(prefix="guichet-tableau-")) / "lot.jsonl"
        self.kafka_erreur: str | None = None

    def recevoir(self, evt: dict) -> None:
        """Appelé par le fil Kafka : l'événement attend la prochaine requête (insertion par lots)."""
        with self._verrou_attente:
            self._attente.append(evt)

    def interroger(self, fn, vide=None):
        """Exécute fn(connexion) après avoir intégré les événements en attente. `vide` : même signature que
        Historique.interroger (le direct a toujours sa base, même sans événement)."""
        with self._verrou:
            self._integrer()
            return fn(self.con)

    def _integrer(self) -> None:
        with self._verrou_attente:
            lot, self._attente = self._attente, []
        if not lot:
            return
        connus = {r[0] for r in self.con.execute("SELECT DISTINCT run_id FROM ev").fetchall()}
        premiers: dict[str, int] = {}
        for e in lot:
            run = e.get("run_id")
            if e.get("type") == "run.started" and run in connus:
                # Un rejeu recommence un run déjà vu : on repart de zéro.
                self.con.execute("DELETE FROM ev WHERE run_id = ?", [run])
                connus.discard(run)
                premiers[run] = 0
            premiers[run] = min(premiers.get(run, e.get("seq", 0)), e.get("seq", 0))
        for run, seq in premiers.items():
            if run not in connus and seq > 0:
                self._rattraper(run, seq)
        self._tmp.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in lot), encoding="utf-8")
        self._inserer(lire_enveloppes(self._tmp))
        self.version += 1

    def _rattraper(self, run_id: str, seq: int) -> None:
        """Run commencé avant le tableau de bord : ses premiers événements viennent de son events.jsonl."""
        fichier = self.runs / run_id / "events.jsonl"
        if fichier.exists() and fichier.stat().st_size > 0:
            self._inserer(lire_enveloppes(fichier, f"WHERE run_id = '{run_id}' AND seq < {int(seq)}"))

    def _inserer(self, requete: str) -> None:
        # Un événement peut arriver deux fois (rattrapage, topics lus dans un ordre différent) : event_id unique.
        self.con.execute(f"INSERT INTO ev SELECT * FROM ({requete}) n "
                         "WHERE NOT EXISTS (SELECT 1 FROM ev WHERE ev.event_id = n.event_id) "
                         "QUALIFY row_number() OVER (PARTITION BY event_id ORDER BY seq) = 1")

    def runs_vus(self) -> list[dict]:
        return self.interroger(lambda con: metriques.lignes(con.execute("""
            SELECT run_id, min(t_emit) AS debut, max(t_sim) AS t_sim, count(*) AS evenements,
                   bool_or(type = 'run.ended') AS termine
            FROM ev GROUP BY run_id ORDER BY debut DESC""")))


def ecouter_kafka(direct: Direct, bootstrap: str, arret: threading.Event) -> None:
    """Fil dédié : confluent-kafka est bloquant. Seuls les nouveaux messages sont lus (offset « latest »)."""
    from confluent_kafka import Consumer
    c = Consumer({"bootstrap.servers": bootstrap, "group.id": f"tableau-{os.getpid()}-{int(time.time())}",
                  "auto.offset.reset": "latest", "enable.auto.commit": False})
    c.subscribe(TOPICS)
    try:
        while not arret.is_set():
            m = c.poll(0.2)
            if m is None:
                continue
            if m.error():
                direct.kafka_erreur = str(m.error())
                continue
            direct.kafka_erreur = None
            try:
                direct.recevoir(json.loads(m.value()))
            except ValueError:
                continue
    finally:
        c.close()


def kafka_joignable(bootstrap: str) -> bool:
    from confluent_kafka.admin import AdminClient
    try:
        AdminClient({"bootstrap.servers": bootstrap}).list_topics(timeout=2)
        return True
    except Exception:
        return False


# ================================================================ source « historique »

class Historique:
    """Les runs archivés. La base est rouverte quand un run s'ajoute à l'archive (fin d'un run)."""

    def __init__(self, runs: Path = RUNS):
        self.runs = runs
        self.con = None
        self._signature = None
        self._verrou = threading.Lock()

    def interroger(self, fn, vide=None):
        with self._verrou:
            fichiers = sorted(self.runs.glob("*/archive/runs.parquet"))
            signature = [(str(f), f.stat().st_mtime) for f in fichiers]
            if signature != self._signature:
                self.con, self._signature = (ouvrir(None, self.runs) if fichiers else None), signature
            return fn(self.con) if self.con is not None else vide


# ================================================================ application FastAPI

def _json(donnees) -> str:
    return json.dumps(jsonable_encoder(donnees), ensure_ascii=False)


def creer_app(runs: Path = RUNS, kafka: bool = True) -> FastAPI:
    """`kafka=False` sert aux tests : le direct est alimenté à la main (app.state.direct.recevoir)."""
    direct, historique = Direct(runs), Historique(runs)
    bootstrap = os.environ.get("KAFKA_BOOTSTRAP", "localhost:9092")

    @asynccontextmanager
    async def cycle(app: FastAPI):
        arret = threading.Event()
        if kafka:
            threading.Thread(target=ecouter_kafka, args=(direct, bootstrap, arret), daemon=True).start()
            print(f"[tableau] à l'écoute de Kafka ({bootstrap})", flush=True)
        yield
        arret.set()

    app = FastAPI(title="Tableau de bord GUICHET", version="1.0", lifespan=cycle,
                  description="Décisions des agents, confiance et fiabilité de l'IA, en direct (Kafka) et sur "
                              "l'historique des runs archivés. Métriques : services/metriques.py.")
    app.state.direct, app.state.historique = direct, historique

    def source(nom: str):
        if nom not in ("direct", "historique"):
            raise HTTPException(422, "source : direct ou historique")
        return direct if nom == "direct" else historique

    @app.get("/api/sante", tags=["tableau"])
    async def sante() -> dict:
        """État de la connexion à Kafka et des runs reçus en direct."""
        joignable = await asyncio.to_thread(kafka_joignable, bootstrap) if kafka else False
        return {"kafka": joignable, "kafka_erreur": direct.kafka_erreur, "version": direct.version,
                "runs_direct": await asyncio.to_thread(direct.runs_vus)}

    @app.get("/api/runs", tags=["tableau"])
    async def lister_runs() -> dict:
        """Runs reçus en direct et runs archivés (résumé de fin de run)."""
        return {"direct": await asyncio.to_thread(direct.runs_vus),
                "historique": await asyncio.to_thread(historique.interroger, metriques.comparaison, [])}

    @app.get("/api/metriques", tags=["tableau"])
    async def lire_metriques(source_: str = Query("historique", alias="source"), run: str | None = None) -> dict:
        """Toutes les métriques d'un run (ou de tous les runs de la source si `run` est absent)."""
        s = source(source_)
        m = await asyncio.to_thread(s.interroger, lambda con: metriques.calculer(con, run or None), {})
        return {"source": source_, "run": run or None, "metriques": m}

    @app.get("/api/requetes", tags=["tableau"])
    async def lire_requetes() -> dict[str, str]:
        """Le SQL de chaque métrique, affiché sur la page (« Le calcul »)."""
        return {nom: "\n".join(l.strip() for l in sql.strip().splitlines()) for nom, sql in metriques.METRIQUES.items()}

    @app.get("/api/decisions/{event_id}", tags=["tableau"])
    async def lire_fiche(event_id: str, source_: str = Query("historique", alias="source")) -> dict:
        """Fiche d'une décision : agent, appel LLM, événements qui l'ont causée et ce qu'elle a changé."""
        s = source(source_)
        f = await asyncio.to_thread(s.interroger, lambda con: metriques.fiche(con, event_id), None)
        if not f or f["decision"] is None:
            raise HTTPException(404, "décision inconnue dans cette source")
        return f

    @app.websocket("/ws")
    async def flux(ws: WebSocket) -> None:
        """Métriques du direct, envoyées quand de nouveaux événements arrivent. Le navigateur choisit le run en
        envoyant {"run": "<run_id>"} ; sans run choisi, c'est le dernier run commencé."""
        await ws.accept()
        etat = {"run": None, "envoye": None}

        async def lire():
            async for brut in ws.iter_text():
                try:
                    etat["run"] = json.loads(brut).get("run") or None
                except (ValueError, AttributeError):
                    continue
                etat["envoye"] = None                     # renvoyer tout de suite pour le nouveau run

        lecteur = asyncio.create_task(lire())
        try:
            while not lecteur.done():
                await asyncio.to_thread(direct.interroger, lambda con: None)    # intègre les événements reçus
                cle = (direct.version, etat["run"])
                if cle != etat["envoye"]:
                    runs_vus = await asyncio.to_thread(direct.runs_vus)
                    run = etat["run"] or (runs_vus[0]["run_id"] if runs_vus else None)
                    m = (await asyncio.to_thread(direct.interroger, lambda con: metriques.calculer(con, run))
                         if run else {})
                    await ws.send_text(_json({"source": "direct", "run": run, "runs": runs_vus, "metriques": m,
                                              "kafka_erreur": direct.kafka_erreur}))
                    etat["envoye"] = cle
                await asyncio.sleep(INTERVALLE_S)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            lecteur.cancel()

    app.mount("/", StaticFiles(directory=WEB, html=True), name="page")
    return app


if __name__ == "__main__":
    import uvicorn
    from engine.run import charger_env
    sys.stdout.reconfigure(encoding="utf-8")
    charger_env()
    port = int(os.environ.get("GUICHET_TABLEAU_PORT", "8770"))
    print(f"[tableau] http://127.0.0.1:{port}", flush=True)
    uvicorn.run(creer_app(), host="127.0.0.1", port=port, ws="websockets-sansio")
