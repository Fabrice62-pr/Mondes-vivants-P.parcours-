"""Semaine 7 : mode asynchrone (C4), rejeu (E3), archive interrogeable (F5). Sans réseau."""
import copy
import json
import time

import pytest

from engine.config import charger_config, charger_modeles
from engine.evenements import PuitsJsonl, PuitsMemoire
from engine.moteur import Simulation
from llm.passerelle import Passerelle
from services.rejeu import Rejeu, verifier
from test_llm import Faux, repondeur_plausible


def _cfg(mode="synchrone", llm=False, deposants=30, jours=3):
    cfg = copy.deepcopy(charger_config("scenarios/emergence.yaml"))
    cfg["population"]["deposants"] = deposants
    cfg["horloge"]["duree_jours"] = jours
    cfg["mode"] = mode
    cfg["llm"]["actif"] = llm
    cfg["rag"]["actif"] = False
    return cfg


# ------------------------------------------------------------------ mode asynchrone (C4, C3)

def test_mode_asynchrone_decisions_appliquees_plus_tard():
    def lent(s, u):
        time.sleep(0.05)                       # un LLM qui met 50 ms à répondre
        return repondeur_plausible(s, u)
    cfg = _cfg("asynchrone", llm=True)
    faux = Faux(lent)
    p = Passerelle(charger_modeles(), cfg["llm"], {}, fournisseurs={n: faux for n in ("groq", "mistral", "google")})
    puits = PuitsMemoire()
    sim = Simulation(cfg, 1, [puits], passerelle=p)
    while not sim.fini:                        # l'horloge avance en temps réel : 10 ms par pas
        sim.pas()
        time.sleep(0.01)
    ev = puits.evenements
    assert sim.bus.rejets == 0 and ev[0]["payload"]["mode"] == "asynchrone"
    dec = [e for e in ev if e["type"] == "decision.made" and e["payload"]["type"] == "llm"
           and e["payload"]["agent_id"] not in ("employe",)]
    assert dec and all(e["payload"]["t_sim_contexte"] <= e["t_sim"] for e in dec)
    # la réponse arrive après plusieurs pas : la décision s'applique plus tard que son contexte
    assert any(e["payload"]["t_sim_contexte"] < e["t_sim"] for e in dec)
    assert not any(e["type"] == "temporal.violation" for e in ev)


def test_mode_synchrone_decision_au_meme_pas():
    cfg = _cfg("synchrone", llm=True)
    faux = Faux(repondeur_plausible)
    p = Passerelle(charger_modeles(), cfg["llm"], {}, fournisseurs={n: faux for n in ("groq", "mistral", "google")})
    puits = PuitsMemoire()
    Simulation(cfg, 1, [puits], passerelle=p).executer()
    dec = [e for e in puits.evenements if e["type"] == "decision.made" and e["payload"]["type"] == "llm"
           and e["payload"]["agent_id"] != "direction"]       # la direction délibère dans les deux modes
    assert dec and all(e["payload"]["t_sim_contexte"] == e["t_sim"] for e in dec)


# ------------------------------------------------------------------ rejeu (E3)

def _run_enregistre(tmp_path, **kw):
    cfg = _cfg(**kw)
    run_id = "emergence-g1-test"
    dossier = tmp_path / run_id
    sim = Simulation(cfg, 1, [PuitsJsonl(dossier)], run_id)
    r = sim.executer()
    sim.bus.fermer()
    (dossier / "resume.json").write_text(json.dumps({**r, "run_id": run_id, "graine": 1}), encoding="utf-8")
    (dossier / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    return run_id, dossier


def test_rejeu_identique_sans_aucun_calcul(tmp_path):
    run_id, dossier = _run_enregistre(tmp_path)
    v = verifier(run_id, tmp_path)
    assert v["identique"] and v["instantanes"] > 0
    # le rejeu publie aussi les instantanés (même visualisation) et les indicateurs enregistrés
    puits = PuitsMemoire()
    Rejeu(run_id, puits, 0.0, tmp_path).executer()
    types = {e["type"] for e in puits.evenements}
    assert {"run.started", "indicator.computed", "run.ended"} <= types


def test_rejeu_pause_et_vitesse(tmp_path):
    run_id, _ = _run_enregistre(tmp_path, jours=1)

    class PuitsCommandes(PuitsMemoire):
        def __init__(self):
            super().__init__()
            self.a_envoyer = [json.dumps({"payload": {"commande": "vitesse", "parametres": {"rythme_s": 0.0}}})]

        def commandes(self):
            out, self.a_envoyer = self.a_envoyer, []
            return out
    r = Rejeu(run_id, PuitsCommandes(), 5.0, tmp_path)
    t0 = time.monotonic()
    r.executer()
    assert r.rythme == 0.0 and time.monotonic() - t0 < 5    # la commande « vitesse » a été appliquée


# ------------------------------------------------------------------ archive (F5)

def test_archive_parquet_interrogeable(tmp_path):
    pytest.importorskip("duckdb")
    from services.archiveur import archiver_run, executer_sql, ouvrir
    run_id, dossier = _run_enregistre(tmp_path)
    assert archiver_run(dossier)
    con = ouvrir(tmp_path / "archive.duckdb", tmp_path)
    n_events = sum(1 for _ in (dossier / "events.jsonl").open(encoding="utf-8"))
    assert con.sql("SELECT count(*) FROM evenements").fetchone()[0] == n_events
    assert con.sql("SELECT count(*) FROM agents").fetchone()[0] == 30 + 5
    r = con.sql("SELECT config, deposants, defaut FROM runs").fetchone()
    assert r[0] == "emergence" and r[1] == 30
    # une requête du dossier requetes/ s'exécute telle quelle
    from engine.config import ROOT
    rels = executer_sql(con, (ROOT / "requetes" / "scenarios.sql").read_text(encoding="utf-8"))
    assert rels[0].fetchall()


# ------------------------------------------------------------------ service d'indicateurs (B2, F6, C7)

def test_service_indicateurs_recalcule_l_icc_du_moteur(tmp_path):
    from services.indicateurs import ServiceIndicateurs, verifier as verifier_icc
    run_id, _ = _run_enregistre(tmp_path)
    v = verifier_icc(run_id, tmp_path)
    assert v["identique"] and v["pas_compares"] > 50
    # les indicateurs publiés sont conformes au contrat, portent leurs sources et leur retard
    from engine.evenements import Validateur
    from services.rejeu import charger
    service, val, sorties = ServiceIndicateurs(), Validateur(), []
    for e in charger(run_id, tmp_path):
        sorties.extend(service.traiter(e))
    assert sorties and all(val.erreurs(s) == [] for s in sorties)
    assert {s["payload"]["nom"] for s in sorties} == {"icc_flux", "icc_2h"}
    assert all(s["producer"] == "indicateurs" and s["payload"]["retard_ms"] >= 0 for s in sorties)
    assert any(s["payload"]["sources"] for s in sorties)
