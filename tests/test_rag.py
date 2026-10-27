"""Mémoire des agents et RAG (A1, F1, C6), sans réseau ni modèle : plongements factices, PostgreSQL.

Les tests utilisent une base à part, `guichet_test`, créée et vidée au début de la session ; ils sont
sautés si PostgreSQL ne répond pas (docker compose up -d postgres).
"""
import copy
import itertools

import pytest

psycopg = pytest.importorskip("psycopg")

from engine.config import charger_config, charger_modeles  # noqa: E402
from engine.evenements import PuitsMemoire  # noqa: E402
from engine.moteur import Simulation  # noqa: E402
from engine.run import charger_env  # noqa: E402
from llm.passerelle import Passerelle  # noqa: E402
from rag.memoire import Memoire, url_postgres  # noqa: E402
from rag.plongement import PlongementFaux  # noqa: E402
from test_llm import Faux, repondeur_plausible  # noqa: E402

_runs = itertools.count()


@pytest.fixture(scope="module")
def url():
    charger_env()
    try:
        with psycopg.connect(url_postgres(), autocommit=True, connect_timeout=3) as cx:
            if not cx.execute("SELECT 1 FROM pg_database WHERE datname = 'guichet_test'").fetchone():
                cx.execute("CREATE DATABASE guichet_test")
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL injoignable : docker compose up -d postgres")
    url = psycopg.conninfo.make_conninfo(url_postgres(), dbname="guichet_test")
    with psycopg.connect(url, autocommit=True) as cx:          # base de test vide à chaque session
        cx.execute("DROP TABLE IF EXISTS souvenirs, memoire_runs, corpus")
    return url


@pytest.fixture
def memoire(url):
    ouvertes = []

    def fabrique(run_id=None, **cfg):
        m = Memoire(PlongementFaux(), run_id or f"test-{next(_runs)}", url, cfg)
        ouvertes.append(m)
        return m
    yield fabrique
    for m in ouvertes:
        m.fermer()


def test_corpus_charge_et_interrogeable(memoire):
    m = memoire(k_documents=2)
    assert m.n_documents >= 20
    _, docs = m.rappeler([("a1", "une panique bancaire fait tomber une banque saine", 0)])[0]
    assert len(docs) == 2 and "banques-paniques" in {d.doc_id for d in docs}


def test_souvenirs_filtres_par_agent_et_par_date(memoire):  # C6 : rien du futur, rien des autres
    m = memoire(k_souvenirs=10)
    m.ecrire("m1", "a1", "file devant l'agence du Marché", 100, 0.6)
    m.ecrire("m2", "a1", "file devant l'agence du Bourg", 500, 0.6)       # futur pour t = 300
    m.ecrire("m3", "a2", "file devant l'agence du Marché", 100, 0.6)      # un autre agent
    souvenirs, _ = m.rappeler([("a1", "file agence", 300)])[0]
    assert [s.memoire_id for s in souvenirs] == ["m1"]


def test_importance_et_fraicheur_comptent(memoire):
    m = memoire(k_souvenirs=1, demi_vie_h=24)
    m.ecrire("vieux", "a1", "la caisse va bien", 0, 0.2)
    m.ecrire("frais", "a1", "la caisse va bien", 2000, 0.9)
    assert m.rappeler([("a1", "la caisse", 2000)])[0][0][0].memoire_id == "frais"


def test_souvenirs_conserves_apres_le_run_et_isoles_par_run(memoire, url):
    m = memoire("run-a")
    m.ecrire("m1", "a1", "file devant l'agence du Marché", 100, 0.6)
    m.fermer()                                                 # fin du run : écrit ce qui était en attente
    with psycopg.connect(url) as cx:
        assert cx.execute("SELECT count(*) FROM souvenirs WHERE run_id = 'run-a'").fetchone()[0] == 1
        assert cx.execute("SELECT fin FROM memoire_runs WHERE run_id = 'run-a'").fetchone()[0] is not None
    autre = memoire("run-b", k_souvenirs=10)                   # un autre run ne voit pas ces souvenirs
    assert autre.rappeler([("a1", "file agence", 300)])[0][0] == []
    memoire("run-a")                                           # même nom relancé : repart de zéro
    with psycopg.connect(url) as cx:
        assert cx.execute("SELECT count(*) FROM souvenirs WHERE run_id = 'run-a'").fetchone()[0] == 0


def _sim(memoire, jours=3):
    cfg = copy.deepcopy(charger_config("scenarios/emergence.yaml"))
    cfg["population"]["deposants"] = 30
    cfg["horloge"]["duree_jours"] = jours
    cfg["llm"]["actif"] = cfg["rag"]["actif"] = True
    faux = Faux(repondeur_plausible)
    p = Passerelle(charger_modeles(), cfg["llm"], {}, fournisseurs={n: faux for n in ("groq", "mistral", "google")})
    puits = PuitsMemoire()
    sim = Simulation(cfg, 1, [puits], passerelle=p, memoire=memoire(k_souvenirs=4, k_documents=2))
    sim.executer()
    return sim, puits, faux


def test_run_avec_memoire_conforme_et_cite_ses_sources(memoire):
    sim, puits, faux = _sim(memoire)
    ev = puits.evenements
    assert sim.bus.rejets == 0 and puits.dlq == []
    memoires = {e["payload"]["memoire_id"]: e for e in ev if e["type"] == "memory.written"}
    assert memoires and {e["payload"]["nature"] for e in memoires.values()} >= {"observation", "reflexion"}
    llm = {r["agent_id"] for r in ev[0]["payload"]["attribution"] if r["type"] == "llm"}
    assert {e["payload"]["agent_id"] for e in memoires.values()} <= llm            # seuls les agents LLM se souviennent
    decisions = [e for e in ev if e["type"] == "decision.made" and e["payload"]["type"] == "llm"]
    assert any(d["payload"]["souvenirs"] for d in decisions) and all(d["payload"]["documents"] for d in decisions
                                                                      if d["payload"]["agent_id"] != "direction")
    # C6 : chaque souvenir cité appartient à l'agent et date d'avant le contexte de la décision
    for d in decisions:
        for mid in d["payload"]["souvenirs"]:
            m = memoires[mid]
            assert m["payload"]["agent_id"] == d["payload"]["agent_id"] and m["t_sim"] <= d["payload"]["t_sim_contexte"]
    assert not any(e["type"] == "temporal.violation" for e in ev) and sim.resume()["rag"]["violations_c6"] == 0
    # le prompt contient bien le corpus de la vallée
    assert any("Ce que l'on sait à Brévanne" in u for _, u in faux.appels)


def test_garde_fou_c6_detecte_un_souvenir_du_futur(memoire):
    sim, puits, _ = _sim(memoire, jours=1)
    from rag.memoire import Souvenir
    a = sim.deposants[0]
    sim._verifier_c6(a, ([Souvenir("m-x", "plus tard", sim.t + 15, 0.5, 1.0)], []), "d-1")
    v = [e for e in puits.evenements if e["type"] == "temporal.violation"]
    assert len(v) == 1 and v[0]["payload"]["raison"] == "futur" and sim.bus.rejets == 0
