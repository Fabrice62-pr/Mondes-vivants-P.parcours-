import copy
import random
from collections import Counter

from engine.config import charger_config, charger_modeles, charger_monde
from engine.monde import Monde
from engine.population import construire, repartition_effective


def _pop(cfg, graine=1):
    return construire(cfg, Monde(charger_monde()), charger_modeles(), random.Random(f"population-{graine}"))


def _cfg_llm():
    cfg = charger_config("principal.yaml")
    cfg["llm"]["actif"] = True
    return cfg


def test_taille_parametrable_sans_code():          # C2
    cfg = charger_config("principal.yaml")
    cfg["population"]["deposants"] = 40
    deposants, employe, direction = _pop(cfg)
    assert len(deposants) == 40 + 3 and employe.role == "employe" and direction.role == "direction"


def test_part_regles_et_moins_exposes():           # A6
    deposants, _, _ = _pop(_cfg_llm())
    ordinaires = [a for a in deposants if not a.principal]
    regles = [a for a in ordinaires if a.type == "regles"]
    assert len(regles) == round(len(ordinaires) * 0.25)
    assert max(a.sociabilite for a in regles) <= min(a.sociabilite for a in ordinaires if a.type == "llm")


def test_attribution_des_modeles():                # A5
    deposants, employe, direction = _pop(_cfg_llm())
    tiers = {m: v["tier"] for m, v in charger_modeles()["models"].items()}
    for a in deposants:
        if a.principal:
            assert a.type == "llm" and a.tier == "grand" and tiers[a.modele] == "grand"
        elif a.type == "llm":
            assert a.tier == "petit" and tiers[a.modele] == "petit"
    assert employe.modele == "gpt-oss-120b" and direction.modele == "gemma-31b"
    petits = Counter(a.modele for a in deposants if a.tier == "petit")
    total = sum(petits.values())
    attendu = cfg_llm_repartition()["ministral-8b"]
    assert abs(petits["ministral-8b"] / total - attendu) < 0.05


def cfg_llm_repartition():
    return _cfg_llm()["population"]["repartition_petits"]


def test_modele_local_absent_redistribue():
    # Ollama injoignable : sa part est répartie entre les autres petits modèles, avant l'attribution.
    cfg = _cfg_llm()
    exclus = {m for m in cfg["population"]["repartition_petits"] if m.endswith("-local")}
    deposants = construire(cfg, Monde(charger_monde()), charger_modeles(), random.Random("population-1"),
                           exclus)[0]
    petits = {a.modele for a in deposants if a.tier == "petit"}
    assert petits and not petits & exclus
    r = repartition_effective({"a": 0.6, "b": 0.15, "c": 0.25}, {"c"})
    assert set(r) == {"a", "b"} and abs(r["a"] - 0.8) < 1e-9 and abs(r["b"] - 0.2) < 1e-9


def test_attribution_figee_par_la_graine():
    a1 = [(a.id, a.modele, a.type) for a in _pop(_cfg_llm(), 7)[0]]
    a2 = [(a.id, a.modele, a.type) for a in _pop(_cfg_llm(), 7)[0]]
    a3 = [(a.id, a.modele, a.type) for a in _pop(_cfg_llm(), 8)[0]]
    assert a1 == a2 and a1 != a3


def test_sans_llm_tout_le_monde_est_a_regles():
    cfg = copy.deepcopy(charger_config("principal.yaml"))
    cfg["llm"]["actif"] = False
    deposants, employe, direction = _pop(cfg)
    assert all(a.type == "regles" and a.modele is None for a in deposants)
    assert employe.type == direction.type == "regles"
