"""Traitement hybride (C4, H2), seul mode d'exécution : un pas attend les réponses LLM jusqu'à l'échéance
(voie synchrone) ; les retardataires s'appliquent plus tard (voie asynchrone)."""
import copy
import time

import pytest

from engine.config import charger_config, charger_modeles, echeance
from engine.evenements import PuitsMemoire
from engine.moteur import Simulation
from llm.passerelle import Passerelle
from test_llm import Faux, repondeur_plausible

LENTS = ("openai/gpt-oss-120b", "gemma-4-31b-it")     # personnages principaux, employé, direction


class Latences(Faux):
    """Les petits modèles répondent tout de suite, les grands après `latence` secondes réelles."""

    def __init__(self, repondre, latence: float):
        super().__init__(repondre)
        self.latence = latence

    def appeler(self, modele, *args):
        if modele["id"] in LENTS:
            time.sleep(self.latence)
        return super().appeler(modele, *args)


def _run(echeance_s, latence=0.1, jours=4, rythme_s=0.01):
    cfg = copy.deepcopy(charger_config("scenarios/emergence.yaml"))
    cfg["population"]["deposants"] = 30
    cfg["horloge"]["duree_jours"] = jours
    cfg["llm"]["actif"] = True
    cfg["rag"]["actif"] = False
    cfg["hybride"]["echeance_s"] = echeance_s
    faux = Latences(repondeur_plausible, latence)
    p = Passerelle(charger_modeles(), cfg["llm"], {}, fournisseurs={n: faux for n in ("groq", "mistral", "google")})
    puits = PuitsMemoire()
    sim = Simulation(cfg, 1, [puits], passerelle=p)
    while not sim.fini:          # comme engine/run.py : sans rythme réel, une réponse en retard arriverait après la fin
        sim.pas()
        time.sleep(rythme_s)
    ev = puits.evenements
    assert sim.bus.rejets == 0 and puits.dlq == [] and not any(e["type"] == "temporal.violation" for e in ev)
    lents = {r["agent_id"] for r in ev[0]["payload"]["attribution"]
             if r["type"] == "llm" and charger_modeles()["models"][r["modele"]]["id"] in LENTS}
    decisions = [e for e in ev if e["type"] == "decision.made" and e["payload"]["type"] == "llm"]
    assert decisions
    return sim, ev, decisions, lents


def test_hybride_les_deux_voies_dans_le_meme_run():
    sim, ev, decisions, lents = _run(0.03)
    assert ev[0]["payload"]["mode"] == "hybride" and ev[0]["payload"]["echeance_s"] == 0.03
    voies = {d["payload"]["voie"] for d in decisions}
    assert voies == {"dans_le_pas", "en_retard"}
    for d in decisions:
        p = d["payload"]
        if p["voie"] == "dans_le_pas":
            assert d["t_sim"] == p["t_sim_contexte"]
        else:                                        # appliquée plus tard, datée de l'instant où il a réfléchi
            assert d["t_sim"] > p["t_sim_contexte"]
        if p["agent_id"] in lents:                   # 0,1 s de latence > 0,03 s d'échéance : toujours en retard
            assert p["voie"] == "en_retard"
    assert any(d["payload"]["voie"] == "dans_le_pas" and d["payload"]["agent_id"] not in lents for d in decisions)
    assert sim.resume()["llm"]["voies"] == {v: sum(1 for d in decisions if d["payload"]["voie"] == v)
                                            for v in ("dans_le_pas", "en_retard")}


def test_echeance_longue_toutes_dans_le_pas():
    _, ev, decisions, _ = _run(2.0, latence=0.02)
    assert ev[0]["payload"]["mode"] == "hybride" and ev[0]["payload"]["echeance_s"] == 2.0
    assert all(d["payload"]["voie"] == "dans_le_pas" and d["t_sim"] == d["payload"]["t_sim_contexte"]
               for d in decisions)


def test_echeance_et_anciens_modes_refuses():
    assert echeance({"hybride": {"echeance_s": 2.5}}) == 2.5
    assert echeance({"mode": "hybride", "hybride": {"echeance_s": 3}}) == 3
    for e in (0, -1):
        with pytest.raises(ValueError):
            echeance({"hybride": {"echeance_s": e}})
    for mode in ("synchrone", "asynchrone", "parallele"):
        with pytest.raises(ValueError, match="supprimé"):
            echeance({"mode": mode, "hybride": {"echeance_s": 3}})
