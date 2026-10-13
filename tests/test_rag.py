"""Mémoire des agents et RAG (A1, F1, C6), sans réseau ni modèle : plongements factices, Chroma en mémoire."""
import copy
import itertools

import pytest

pytest.importorskip("chromadb")

from engine.config import charger_config, charger_modeles  # noqa: E402
from engine.evenements import PuitsMemoire  # noqa: E402
from engine.moteur import Simulation  # noqa: E402
from llm.passerelle import Passerelle  # noqa: E402
from rag.memoire import Memoire  # noqa: E402
from rag.plongement import PlongementFaux  # noqa: E402
from test_llm import Faux, repondeur_plausible  # noqa: E402

_runs = itertools.count()


def _memoire(**cfg):
    return Memoire(PlongementFaux(), f"test-{next(_runs)}", None, cfg)


def test_corpus_charge_et_interrogeable():
    m = _memoire(k_documents=2)
    assert m.corpus.count() >= 20
    _, docs = m.rappeler([("a1", "une panique bancaire fait tomber une banque saine", 0)])[0]
    assert len(docs) == 2 and "banques-paniques" in {d.doc_id for d in docs}


def test_souvenirs_filtres_par_agent_et_par_date():         # C6 : rien du futur, rien des autres
    m = _memoire(k_souvenirs=10)
    m.ecrire("m1", "a1", "file devant l'agence du Marché", 100, 0.6)
    m.ecrire("m2", "a1", "file devant l'agence du Bourg", 500, 0.6)       # futur pour t = 300
    m.ecrire("m3", "a2", "file devant l'agence du Marché", 100, 0.6)      # un autre agent
    souvenirs, _ = m.rappeler([("a1", "file agence", 300)])[0]
    assert [s.memoire_id for s in souvenirs] == ["m1"]


def test_importance_et_fraicheur_comptent():
    m = _memoire(k_souvenirs=1, demi_vie_h=24)
    m.ecrire("vieux", "a1", "la caisse va bien", 0, 0.2)
    m.ecrire("frais", "a1", "la caisse va bien", 2000, 0.9)
    assert m.rappeler([("a1", "la caisse", 2000)])[0][0][0].memoire_id == "frais"


def _sim(jours=3):
    cfg = copy.deepcopy(charger_config("scenarios/emergence.yaml"))
    cfg["population"]["deposants"] = 30
    cfg["horloge"]["duree_jours"] = jours
    cfg["llm"]["actif"] = cfg["rag"]["actif"] = True
    faux = Faux(repondeur_plausible)
    p = Passerelle(charger_modeles(), cfg["llm"], {}, fournisseurs={n: faux for n in ("groq", "mistral", "google")})
    puits = PuitsMemoire()
    sim = Simulation(cfg, 1, [puits], passerelle=p, memoire=_memoire(k_souvenirs=4, k_documents=2))
    sim.executer()
    return sim, puits, faux


def test_run_avec_memoire_conforme_et_cite_ses_sources():
    sim, puits, faux = _sim()
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


def test_garde_fou_c6_detecte_un_souvenir_du_futur():
    sim, puits, _ = _sim(jours=1)
    from rag.memoire import Souvenir
    a = sim.deposants[0]
    sim._verifier_c6(a, ([Souvenir("m-x", "plus tard", sim.t + 15, 0.5, 1.0)], []), "d-1")
    v = [e for e in puits.evenements if e["type"] == "temporal.violation"]
    assert len(v) == 1 and v[0]["payload"]["raison"] == "futur" and sim.bus.rejets == 0
