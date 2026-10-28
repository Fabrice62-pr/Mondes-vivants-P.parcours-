"""Système 1 des déposants LLM (engine/systeme1.py) : aiguillage vers le LLM, retraits toujours vérifiés,
repli en cas de panne. Sans réseau ni modèle NLI : un faux fournisseur et un faux Système 1."""
import copy
import json

import pytest

from engine.agents import Deposant
from engine.config import charger_config, charger_modeles
from engine.evenements import PuitsMemoire
from engine.moteur import Simulation
from engine.systeme1 import Avis, Systeme1Nli, Systeme1Regle, creer, texte_s1
from llm.fournisseurs import Reponse
from llm.passerelle import Passerelle
from test_llm import Faux, repondeur_plausible


def _sim(repondre=repondeur_plausible, moteur="regle", systeme1=None, deposants=30, jours=4):
    cfg = copy.deepcopy(charger_config("scenarios/emergence.yaml"))
    cfg["population"]["deposants"] = deposants
    cfg["horloge"]["duree_jours"] = jours
    cfg["llm"]["actif"] = True
    cfg["rag"]["actif"] = False
    cfg["systeme1"]["moteur"] = moteur
    faux = Faux(repondre)
    p = Passerelle(charger_modeles(), cfg["llm"], {}, fournisseurs={n: faux for n in ("groq", "mistral", "google")})
    puits = PuitsMemoire()
    sim = Simulation(cfg, 1, [puits], passerelle=p, systeme1=systeme1)
    sim.executer()
    assert sim.bus.rejets == 0 and puits.dlq == []
    return sim, puits.evenements, faux


def _deposants_llm(ev):
    return {r["agent_id"] for r in ev[0]["payload"]["attribution"] if r["type"] == "llm" and r["role"] == "deposant"}


def _agent(**k):
    base = dict(id="d", nom="d", profil="prudent", depot=10000, seuil=0.5, loyaute=0.5, confiance=0.6,
                sociabilite=0.5, connecte=False, quartier="q", x=0, z=0)
    return Deposant(**{**base, **k})


class _Leviers:
    leviers = {"garantie": False, "plafond_garantie": 20000}

    @staticmethod
    def couverture(_):
        return 0.0


def test_regle_probabilite_et_confiance():
    s1 = Systeme1Regle({"marge": 0.10})
    calme, panique, limite = _agent(), _agent(peur=0.9, estime_autres=0.9), _agent(peur=0.5, estime_autres=0.5)
    a_calme, a_panique, a_limite = s1.evaluer(_Leviers, [calme, panique, limite])
    assert a_calme.action == "rien" and a_calme.p_retirer == 0.0 and a_calme.confiance == 1.0
    assert a_panique.action == "retirer" and a_panique.p_retirer == 1.0
    pression = limite.pression(0.0)                     # 0,53 : juste au-dessus du seuil de 0,50
    assert a_limite.action == "retirer" and abs(a_limite.p_retirer - (0.5 + (pression - 0.5) / 0.2)) < 1e-9
    assert a_limite.confiance < 0.85


def test_creer_selon_le_moteur():
    assert creer({"moteur": "aucun"}) is None and creer(None) is None
    assert isinstance(creer({"moteur": "regle"}), Systeme1Regle)


def test_retrait_toujours_verifie_par_le_llm():
    sim, ev, _ = _sim()
    llm = _deposants_llm(ev)
    decisions = [e["payload"] for e in ev if e["type"] == "decision.made" and e["payload"]["agent_id"] in llm]
    # le Système 1 ne décide jamais seul un retrait (hors panne du LLM, absente ici)
    assert not any(d["type"] == "systeme1" and d["action"] == "retirer" for d in decisions)
    # chaque décision LLM d'un déposant vient d'une escalade du Système 1, qui en donne la raison
    par_llm = [d for d in decisions if d["type"] == "llm"]
    assert par_llm and all(d["systeme1"] and d["systeme1"]["escalade"] in ("retrait", "ambigu") for d in par_llm)
    # un déposant LLM ne va au guichet qu'après une décision « retirer » du LLM
    for e in ev:
        if e["type"] == "withdrawal.served" and e["payload"]["agent_id"] in llm:
            aid = e["payload"]["agent_id"]
            assert any(d["agent_id"] == aid and d["action"] == "retirer" and d["type"] == "llm" for d in decisions)
    s = sim.resume()["llm"]["systeme1"]
    assert s["moteur"] == "regle" and s["evaluations"] >= s["decide_seul"] + sum(s["escalades"].values())
    assert sum(s["verdicts_llm"].values()) == len(par_llm)


def test_moins_d_appels_qu_sans_systeme1():
    _, _, sans = _sim(moteur="aucun")
    sim, _, avec = _sim(moteur="regle")
    assert sim.resume()["llm"]["systeme1"]["decide_seul"] > 0
    assert len(avec.appels) < len(sans.appels)


class _Fixe:
    """Faux Système 1 : la même probabilité de retrait pour tout le monde."""
    nom, regle_id = "regle", "S1-test"

    def __init__(self, p):
        self.p = p

    def evaluer(self, sim, agents):
        return [Avis(self.p, "retirer" if self.p > 0.5 else "attendre", f"p fixe {self.p}") for _ in agents]


def test_aiguillage_selon_la_confiance():
    sim, _, faux = _sim(systeme1=_Fixe(0.05))          # confiance 0,95 : tout est décidé sans LLM
    s = sim.resume()["llm"]["systeme1"]
    assert s["decide_seul"] == s["evaluations"] > 0 and sum(s["escalades"].values()) == 0
    assert not any("Décide maintenant ce que tu fais" in u for _, u in faux.appels)   # aucun prompt de déposant
    sim, _, _ = _sim(systeme1=_Fixe(0.3))               # confiance 0,7 < 0,85 : ambigu, le LLM tranche
    s = sim.resume()["llm"]["systeme1"]
    assert s["decide_seul"] == 0 and s["escalades"]["ambigu"] > 0 and s["escalades"]["retrait"] == 0
    sim, _, _ = _sim(systeme1=_Fixe(0.9))               # retrait proposé : toujours vérifié
    s = sim.resume()["llm"]["systeme1"]
    assert s["decide_seul"] == 0 and s["escalades"]["retrait"] > 0 and s["escalades"]["ambigu"] == 0


def _attend_toujours(systeme, utilisateur):
    """Le déposant ne retire jamais : il reste dans la vallée et accumule du nouveau."""
    if "Décide maintenant ce que tu fais" in utilisateur:
        return json.dumps({"action": "attendre", "peur": 0.3, "estime_autres": 0.3, "justification": "Je reste.",
                           "phrase": "On verra."})
    return repondeur_plausible(systeme, utilisateur)


def test_escalade_reportee_pendant_l_intervalle():
    sim, ev, _ = _sim(_attend_toujours, systeme1=_Fixe(0.3))
    s = sim.resume()["llm"]["systeme1"]
    assert s["reportees"] > 0
    intervalle = sim.L["intervalle_decision_min"]
    llm = _deposants_llm(ev)
    contextes: dict[str, list[int]] = {}
    for e in ev:
        p = e["payload"]
        if e["type"] == "decision.made" and p["type"] == "llm" and p["agent_id"] in llm:
            contextes.setdefault(p["agent_id"], []).append(p["t_sim_contexte"])
    for ts in contextes.values():                       # jamais deux appels au LLM à moins d'un intervalle
        assert all(b - a >= intervalle for a, b in zip(ts, ts[1:]))


def test_panne_du_llm_repli_sur_le_systeme1():
    sim, ev, _ = _sim(lambda s, u: Reponse(503, erreur="indisponible"), jours=3)
    llm = _deposants_llm(ev)
    replis = [e["payload"] for e in ev if e["type"] == "decision.made" and e["payload"]["agent_id"] in llm
              and e["payload"]["type"] == "systeme1" and e["payload"]["justification"].startswith("repli")]
    assert replis and all(r["systeme1"]["moteur"] == "regle" and r["regle_id"] == "S1-regle" for r in replis)
    assert not any(e["type"] == "decision.made" and e["payload"]["agent_id"] in llm
                   and e["payload"]["type"] == "repli_regles" for e in ev)


def test_texte_appris_notes_d_abord_situation_a_la_fin():
    t = texte_s1("ministral-8b", "Tu es prudent.", "J3 vendredi 10:00", 12000, None, 1.0,
                 [("J3 vendredi 07:30", "tu as lu la presse"), ("J3 vendredi 09:00", "tu as vu une file")], 0.62, 0.3)
    assert t.startswith("Ce que tu as appris : J3 vendredi 07:30, tu as lu la presse.")
    assert t.index("tu as vu une file") < t.index("Modèle ministral-8b.")   # coupé à gauche : les notes partent d'abord
    assert t.endswith("Part des autres qui vont retirer, selon toi : moyenne (0.30).")
    assert "très inquiet (0.62)" in t and "Aucune garantie publique." in t and "Retraits limités" not in t


def test_moteur_appris_sans_modele_entraine(tmp_path):
    with pytest.raises(FileNotFoundError, match="systeme1_apprentissage"):
        creer({"moteur": "appris", "modele_appris": str(tmp_path / "absent")})


def test_seuil_propre_au_systeme1():
    s1 = _Fixe(0.05)                                    # confiance 0,95
    s1.confiance_min = 0.99                             # comme le moteur appris, réglé à l'entraînement
    sim, _, _ = _sim(systeme1=s1)
    s = sim.resume()["llm"]["systeme1"]
    assert s["confiance_min"] == 0.99 and s["decide_seul"] == 0 and s["escalades"]["ambigu"] > 0


def test_premisse_nli_en_francais_sans_nombres():
    s1 = object.__new__(Systeme1Nli)                    # sans charger le modèle
    s1.notes = 2

    class Horloge:
        @staticmethod
        def libelle(t):
            return f"vendredi {t}"

    class Sim(_Leviers):
        horloge = Horloge

    a = _agent(peur=0.8, estime_autres=0.6)
    for t, txt in [(1, "rien"), (2, "tu as vu 9 personnes faire la queue"), (3, "un voisin t'a parlé")]:
        a.noter(t, txt)
    texte = s1.premisse(Sim, a)
    assert "paniqué" in texte and "forte" in texte and "0.8" not in texte
    assert "faire la queue" in texte and "rien." not in texte          # seulement les 2 dernières notes
    assert "Aucune garantie publique" in texte and "10 000 unités" in texte
