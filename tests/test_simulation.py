import copy

import pytest

from engine.config import charger_config
from engine.evenements import PuitsMemoire
from engine.moteur import Simulation


def _sim(nom="principal.yaml", graine=1, **modifs):
    cfg = copy.deepcopy(charger_config(nom))
    cfg["population"]["deposants"] = modifs.get("deposants", 40)
    cfg["horloge"]["duree_jours"] = modifs.get("jours", 4)
    cfg["llm"]["actif"] = False          # moteur à règles ; les agents LLM sont testés dans test_llm.py
    puits = PuitsMemoire()
    return Simulation(cfg, graine, [puits]), puits


def test_tous_les_evenements_sont_conformes():     # F4
    sim, puits = _sim()
    sim.executer()
    assert sim.bus.rejets == 0 and puits.dlq == []
    assert puits.evenements[0]["type"] == "run.started" and puits.evenements[-1]["type"] == "run.ended"


def test_determinisme_meme_graine():               # base du rejeu (E3)
    def trace(g):
        sim, puits = _sim(graine=g)
        sim.executer()
        return [(e["type"], e["t_sim"], e["event_id"], e["payload"]) for e in puits.evenements
                if e["type"] != "run.ended"]
    assert trace(3) == trace(3)
    assert trace(3) != trace(4)


def test_t_sim_croissant_et_seq_unique():          # C3
    sim, puits = _sim()
    sim.executer()
    t = [e["t_sim"] for e in puits.evenements]
    assert t == sorted(t)
    assert len({e["seq"] for e in puits.evenements}) == len(puits.evenements)


@pytest.mark.parametrize("graine", [1, 2, 3])
def test_conservation_de_l_argent(graine):          # aucun euro créé ni perdu deux fois
    sim, _ = _sim("scenarios/emergence.yaml", graine, deposants=60)
    sim.executer()
    for a in sim.deposants:
        assert a.depot_initial == pytest.approx(a.paye + a.garanti + a.perdu + a.depot, abs=0.01), a.id
    assert sim.caisse.retire_total == pytest.approx(sum(a.paye for a in sim.deposants), abs=0.01)


def test_defaut_sans_double_perte():
    sim, _ = _sim("scenarios/emergence.yaml", 1, deposants=60)
    r = sim.executer()
    assert r["defaut"]
    pertes = sum(a.perdu for a in sim.deposants)
    restants = sum(a.depot_initial - a.paye - a.garanti for a in sim.deposants)
    assert pertes == pytest.approx(restants, abs=0.01)


def test_emergence_contre_absence():               # B3 (échelle réduite, une graine)
    r_oui = _sim("scenarios/emergence.yaml", 2, deposants=140, jours=7)[0].executer()
    r_non = _sim("scenarios/absence.yaml", 2, deposants=140, jours=7)[0].executer()
    assert r_oui["emergence"] and r_oui["icc_max"] > 0.6
    assert not r_non["emergence"] and r_non["icc_max"] < 0.3


def test_commande_invalide_isolee_sans_arret():    # F4 : DLQ, la simulation continue
    sim, puits = _sim(jours=2)
    assert not sim.appliquer_commande("fermer_agence", {})            # paramètre manquant
    assert not sim.appliquer_commande("detruire_la_caisse", {})       # commande inconnue
    assert len(puits.dlq) == 2
    sim.executer()
    assert sim.fini


def test_commande_garantie_reduit_la_pression():   # D3
    sim, _ = _sim(jours=2)
    a = next(d for d in sim.deposants if d.depot > 20000)
    a.peur, a.estime_autres = 0.8, 0.8
    avant = a.pression(sim.couverture(a.depot))
    sim.appliquer_commande("garantie", {"active": True, "plafond": 100000})
    assert a.pression(sim.couverture(a.depot)) < avant


def test_mode_inconnu_refuse():                     # C4 : synchrone ou asynchrone (test_semaine7.py)
    cfg = copy.deepcopy(charger_config("principal.yaml"))
    cfg["mode"] = "quantique"
    with pytest.raises(ValueError):
        Simulation(cfg, 1, [PuitsMemoire()])
