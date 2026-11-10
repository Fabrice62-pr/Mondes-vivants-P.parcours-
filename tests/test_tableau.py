"""Tableau de bord (services/tableau.py, services/metriques.py) : un run vu en direct, même rattrapé en cours de
route, doit donner exactement les mêmes métriques que le même run lu dans l'archive."""
import copy
import json

import pytest
from fastapi.testclient import TestClient

from engine.config import charger_config, charger_modeles
from engine.evenements import PuitsJsonl
from engine.moteur import Simulation
from llm.passerelle import Passerelle
from services import metriques
from services.archiveur import archiver_run
from services.tableau import Direct, Historique, creer_app
from test_llm import Faux, repondeur_plausible

RUN = "emergence-g1-20261110-100000"


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    """Un run court avec LLM scripté et Système 1 à règle, enregistré puis archivé."""
    racine = tmp_path_factory.mktemp("runs")
    cfg = copy.deepcopy(charger_config("scenarios/emergence.yaml"))
    cfg["population"]["deposants"] = 30
    cfg["horloge"]["duree_jours"] = 4
    cfg["rag"]["actif"] = False
    cfg["systeme1"]["moteur"] = "regle"
    faux = Faux(repondeur_plausible)
    p = Passerelle(charger_modeles(), cfg["llm"], {}, fournisseurs={n: faux for n in ("groq", "mistral", "google")})
    dossier = racine / RUN
    puits = PuitsJsonl(dossier)
    (dossier / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    sim = Simulation(cfg, 1, [puits], run_id=RUN, passerelle=p)
    resume = sim.executer()
    sim.bus.fermer()
    (dossier / "resume.json").write_text(json.dumps({**resume, "run_id": RUN, "graine": 1}), encoding="utf-8")
    assert archiver_run(dossier)
    return racine


def _evenements(runs):
    return [json.loads(l) for l in (runs / RUN / "events.jsonl").read_text(encoding="utf-8").splitlines()]


def _arrondi(x):
    if isinstance(x, float):
        return round(x, 9)
    if isinstance(x, dict):
        return {k: _arrondi(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_arrondi(v) for v in x]
    return x


def _metriques(source):
    return _arrondi(source.interroger(lambda con: metriques.calculer(con, RUN)))


def test_le_run_a_des_decisions_llm_et_systeme1(runs):
    m = _metriques(Historique(runs))
    s = m["synthese"][0]
    assert s["decisions_llm"] > 0 and s["decisions_systeme1"] > 0 and s["runs"] == 1
    assert m["qui_decide"] and m["fiabilite_modeles"] and m["serie_icc"]


def test_direct_identique_a_l_archive(runs):
    direct = Direct(runs)
    evts = _evenements(runs)
    for e in evts[: len(evts) // 2]:          # deux lots : le direct reçoit le run au fil de l'eau
        direct.recevoir(e)
    direct.interroger(lambda con: None)
    for e in evts[len(evts) // 2:]:
        direct.recevoir(e)
    assert _metriques(direct) == _metriques(Historique(runs))


def test_run_rattrape_depuis_son_fichier(runs):
    """Le tableau de bord démarre en cours de run : il ne reçoit de Kafka que la fin, le début vient du fichier."""
    direct = Direct(runs)
    evts = _evenements(runs)
    for e in evts[len(evts) // 3:]:
        direct.recevoir(e)
    for e in evts[len(evts) // 3: len(evts) // 3 + 50]:   # doublons : ignorés
        direct.recevoir(e)
    assert _metriques(direct) == _metriques(Historique(runs))
    assert direct.runs_vus()[0]["evenements"] == len(evts)


def test_un_rejeu_repart_de_zero(runs):
    direct = Direct(runs)
    evts = _evenements(runs)
    for e in evts:
        direct.recevoir(e)
    direct.interroger(lambda con: None)
    for e in evts[:20]:                        # le rejeu recommence au run.started
        direct.recevoir(e)
    assert direct.runs_vus()[0]["evenements"] == 20


def test_fiche_d_une_decision_llm(runs):
    h = Historique(runs)
    fil = h.interroger(lambda con: metriques.calculer(con, RUN, ["fil_decisions"]))["fil_decisions"]
    llm = next(d for d in fil if d["type"] == "llm")
    f = h.interroger(lambda con: metriques.fiche(con, llm["event_id"]))
    assert f["decision"]["agent_id"] == llm["agent_id"]
    assert f["appels"] and f["appels"][-1]["statut"] == "ok"
    assert all(isinstance(c["payload"], dict) for c in f["causes"] + f["effets"])


def test_api(runs):
    app = creer_app(runs, kafka=False)
    with TestClient(app) as client:
        for e in _evenements(runs):
            app.state.direct.recevoir(e)
        r = client.get("/api/runs").json()
        assert r["direct"][0]["run_id"] == RUN and r["historique"][0]["run_id"] == RUN
        m = client.get("/api/metriques", params={"source": "historique", "run": RUN}).json()["metriques"]
        assert m["synthese"][0]["runs"] == 1
        eid = m["fil_decisions"][0]["event_id"]
        assert client.get(f"/api/decisions/{eid}", params={"source": "direct"}).status_code == 200
        assert client.get("/api/decisions/inconnue").status_code == 404
        assert client.get("/api/metriques", params={"source": "autre"}).status_code == 422
        assert set(client.get("/api/requetes").json()) == set(metriques.METRIQUES)
        with client.websocket_connect("/ws") as ws:
            msg = json.loads(ws.receive_text())
            assert msg["run"] == RUN and msg["metriques"]["synthese"][0]["decisions"] > 0
