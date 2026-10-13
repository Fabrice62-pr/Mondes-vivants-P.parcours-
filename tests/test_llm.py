"""Passerelle LLM et agents LLM, sans réseau : un faux fournisseur remplace les API (A4, A5, E4)."""
import copy
import json
import time

from engine.config import charger_config, charger_modeles
from engine.evenements import PuitsMemoire
from engine.moteur import Simulation
from llm import prompts
from llm.fournisseurs import Reponse
from llm.limites import CompteurJour, Limiteur
from llm.passerelle import Passerelle, Requete


class Faux:
    """Fournisseur scripté. `repondre(systeme, utilisateur)` renvoie le texte ou une Reponse complète."""

    def __init__(self, repondre):
        self.repondre = repondre
        self.appels: list[tuple[str, str]] = []

    def disponible(self) -> bool:
        return True

    def appeler(self, modele, systeme, utilisateur, temperature, delai_s):
        self.appels.append((modele["id"], utilisateur))
        r = self.repondre(systeme, utilisateur)
        return r if isinstance(r, Reponse) else Reponse(200, texte=r, tokens_entree=120, tokens_sortie=40, latence_ms=5)


def repondeur_plausible(systeme, utilisateur):
    """Réponses valides selon le rôle : le déposant retire s'il a vu une file, sinon il attend."""
    if "guichet de l'Agence du Bourg" in systeme:
        return json.dumps({"action": "repondre", "reponse": "Vos dépôts sont en sécurité.",
                           "justification": "Les comptes sont sains."})
    if "Tu diriges la Caisse" in systeme:
        return json.dumps({"action": "publier_communique", "ton": "factuel", "texte": "Nos réserves sont suffisantes.",
                           "justification": "La file s'allonge."})
    inquiet = "faire la queue" in utilisateur or "presse" in utilisateur
    return "```json\n" + json.dumps({"action": "retirer" if inquiet else "attendre", "peur": 0.7 if inquiet else 0.2,
                                     "estime_autres": 0.8 if inquiet else 0.3, "justification": "J'ai vu ce qui se passe.",
                                     "phrase": "Tout le monde va retirer !" if inquiet else "Restons calmes."}) + "\n```"


def _passerelle(repondre, **cfg):
    faux = Faux(repondre)
    p = Passerelle(charger_modeles(), {"essais": 2, "budget_appels": 100, "max_simultanes": 2, **cfg},
                   {"a1": "ministral-8b"}, fournisseurs={n: faux for n in ("groq", "mistral", "google")})
    return p, faux


def _rq(modele="ministral-8b", call_id="c-1"):
    return Requete(call_id, "a1", modele, "decision", "système", "utilisateur", prompts.valider_deposant)


# ------------------------------------------------------------------ validation (A4)

def test_validation_des_reponses_du_deposant():
    ok, statut, _ = prompts.valider_deposant('Voilà : {"action": "Retirer", "peur": "0,6", "estime_autres": 0.5, '
                                             '"justification": "file", "phrase": "Vite !"} merci')
    assert statut == "ok" and ok["action"] == "retirer" and ok["peur"] == 0.6
    assert prompts.valider_deposant("pas de json")[1] == "json_invalide"
    assert prompts.valider_deposant('{"action": "retirer", "peur": 1.4, "estime_autres": 0.5, '
                                    '"justification": "x", "phrase": "y"}')[1] == "json_invalide"
    # un déposant ne publie pas de communiqué : réponse hors de son rôle
    assert prompts.valider_deposant('{"action": "publier_communique"}')[1] == "hors_role"


def test_validation_direction_exige_un_ton_si_elle_publie():
    assert prompts.valider_direction('{"action": "rien", "justification": "calme"}')[1] == "ok"
    assert prompts.valider_direction('{"action": "publier_communique", "ton": "joyeux", "texte": "x", '
                                     '"justification": "y"}')[1] == "json_invalide"


# ------------------------------------------------------------------ passerelle

def test_nouvel_essai_sur_le_meme_modele_puis_succes():
    reponses = iter(["n'importe quoi", repondeur_plausible("", "")])
    p, faux = _passerelle(lambda s, u: next(reponses))
    res = p.executer([_rq()])[0]
    assert res.ok and res.incident is None
    assert [a["statut"] for a in res.appels] == ["json_invalide", "ok"] and [a["essai"] for a in res.appels] == [0, 1]
    assert {a["modele"] for a in res.appels} == {"ministral-8b"} and len(faux.appels) == 2
    assert "ATTENTION" in faux.appels[1][1]           # le second essai rappelle le format attendu


def test_echec_persistant_donne_un_incident_jamais_un_autre_modele():
    p, faux = _passerelle(lambda s, u: Reponse(500, erreur="panne"))
    res = p.executer([_rq()])[0]
    assert not res.ok and res.incident["nature"] == "erreur_api" and res.incident["repli"] == "regles"
    assert {m for m, _ in faux.appels} == {"ministral-8b-2512"} and len(res.appels) == 2


def test_modele_non_attribue_refuse_sans_appel():
    p, faux = _passerelle(repondeur_plausible)
    res = p.executer([_rq(modele="gpt-oss-120b")])[0]
    assert res.incident["nature"] == "modele_non_attribue" and faux.appels == [] and res.appels == []


def test_budget_d_appels():
    p, _ = _passerelle(repondeur_plausible, budget_appels=1)
    r1, r2 = p.executer([_rq(call_id="c-1"), _rq(call_id="c-2")])
    assert [r1.ok, r2.ok].count(True) == 1
    assert next(r for r in (r1, r2) if not r.ok).incident["nature"] == "budget_epuise"


def test_quota_429_bloque_le_modele():
    p, _ = _passerelle(lambda s, u: Reponse(429, erreur="trop de requêtes", attente_s=120))
    res = p.executer([_rq()])[0]
    assert res.incident["nature"] == "quota"
    assert p.limiteurs["ministral-8b"].reserver(10, time.monotonic() + 0.2) is None


def test_limiteur_requetes_par_minute(tmp_path):
    lim = Limiteur("m", {"rpm": 2, "rpd": 5}, CompteurJour(tmp_path / "q.json"))
    assert lim.reserver(10, time.monotonic() + 1) and lim.reserver(10, time.monotonic() + 1)
    assert lim.reserver(10, time.monotonic() + 0.3) is None            # 3e requête dans la minute


# ------------------------------------------------------------------ simulation avec agents LLM

def _sim(repondre, deposants=30, jours=4):
    cfg = copy.deepcopy(charger_config("scenarios/emergence.yaml"))
    cfg["population"]["deposants"] = deposants
    cfg["horloge"]["duree_jours"] = jours
    cfg["llm"]["actif"] = True
    cfg["rag"]["actif"] = False          # la mémoire est testée dans test_rag.py
    faux = Faux(repondre)
    passerelle = Passerelle(charger_modeles(), cfg["llm"], {}, fournisseurs={n: faux for n in ("groq", "mistral", "google")})
    puits = PuitsMemoire()
    sim = Simulation(cfg, 1, [puits], passerelle=passerelle)
    sim.executer()
    return sim, puits, faux


def test_run_avec_agents_llm_conforme_et_trace():
    sim, puits, faux = _sim(repondeur_plausible)
    ev = puits.evenements
    assert sim.bus.rejets == 0 and puits.dlq == []
    attribue = {r["agent_id"]: r["modele"] for r in ev[0]["payload"]["attribution"] if r["type"] == "llm"}
    appels = [e["payload"] for e in ev if e["type"] == "llm.called"]
    assert appels and all(a["modele"] == attribue[a["agent_id"]] for a in appels)   # jamais un autre modèle
    decisions = [e["payload"] for e in ev if e["type"] == "decision.made" and e["payload"]["type"] == "llm"]
    assert decisions and all(d["call_id"] and d["regle_id"] is None for d in decisions)
    assert {d["agent_id"] for d in decisions} >= {"employe", "direction"} or any(
        d["agent_id"] in ("boulangere", "retraite", "entrepreneur") for d in decisions)
    assert any(e["type"] == "conversation.held" and e["payload"]["mode"] == "llm" for e in ev)
    r = sim.resume()["llm"]
    assert r["appels"] == len(appels) and r["decisions_llm"] == len(decisions) and r["replis"] == 0


def test_carnet_ne_contient_que_le_passe():                         # C6 : rien du futur dans un prompt
    sim, puits, faux = _sim(repondeur_plausible, jours=3)
    for a in sim.deposants:
        assert all(t <= sim.t for t, _ in a.carnet)
    # avant l'article, aucun déposant n'a de raison de réfléchir : aucun appel avant J3 07:30
    premier = min(e["t_sim"] for e in puits.evenements if e["type"] == "llm.called")
    assert premier >= sim.t_article


def test_panne_totale_repli_sur_les_regles():
    sim, puits, _ = _sim(lambda s, u: Reponse(503, erreur="indisponible"), jours=3)
    ev = puits.evenements
    assert sim.bus.rejets == 0
    replis = [e for e in ev if e["type"] == "decision.made" and e["payload"]["type"] == "repli_regles"]
    incidents = [e for e in ev if e["type"] == "incident.raised"]
    assert replis and incidents and not any(e["type"] == "decision.made" and e["payload"]["type"] == "llm" for e in ev)
    assert sim.resume()["llm"]["replis"] == sim.replis > 0


def test_direction_delibere_sans_bloquer_le_run():                  # C3 : t_sim_contexte <= t_sim
    sim, puits, _ = _sim(repondeur_plausible)
    ev = puits.evenements
    dec = [e for e in ev if e["type"] == "decision.made" and e["payload"]["agent_id"] == "direction"
           and e["payload"]["type"] == "llm"]
    assert dec and all(e["payload"]["t_sim_contexte"] <= e["t_sim"] for e in dec)
    assert any(e["type"] == "communique.published" and e["payload"]["auteur"] == "direction"
               and e["payload"]["texte"] == "Nos réserves sont suffisantes." for e in ev)


def test_serveur_sature_attente_puis_succes():                      # 429 sans délai indiqué (Mistral 3505)
    reponses = iter([Reponse(429, erreur="backend_out_of_capacity"), repondeur_plausible("", "")])
    p, _ = _passerelle(lambda s, u: next(reponses))
    t0 = time.monotonic()
    res = p.executer([_rq()])[0]
    assert res.ok and [a["statut"] for a in res.appels] == ["quota", "ok"]
    assert time.monotonic() - t0 >= 1.9                                  # attente de 2 s avant le nouvel essai
