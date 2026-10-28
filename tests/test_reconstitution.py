"""Runs interrompus (services/reconstitution.py) : le résumé recalculé depuis events.jsonl doit être celui
que le moteur aurait écrit, et l'archive doit les distinguer des runs allés à leur terme."""
import copy
import json
import os
import time

import pytest

from engine.config import charger_config
from engine.evenements import PuitsJsonl
from engine.moteur import Simulation
from services.reconstitution import a_reconstituer, reconstituer

COMMUNS = ("defaut", "t_defaut", "solvable", "part_retiree", "file_max", "icc_max", "emergence", "t_emergence",
           "conversations", "communiques", "retraits", "t_fin", "fin")


def _run(racine, nom="emergence-g1-20261027-100000", jours=4, etiquette="essai"):
    cfg = copy.deepcopy(charger_config("scenarios/emergence.yaml"))
    cfg["population"]["deposants"] = 40
    cfg["horloge"]["duree_jours"] = jours
    cfg["llm"]["actif"] = False
    dossier = racine / nom
    puits = PuitsJsonl(dossier)
    (dossier / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    sim = Simulation(cfg, 1, [puits], run_id=nom, etiquette=etiquette)
    resume = sim.executer()
    sim.bus.fermer()
    return dossier, resume


def test_resume_reconstitue_identique_a_celui_du_moteur(tmp_path):
    dossier, moteur = _run(tmp_path)
    r = reconstituer(dossier, ecrire=False)
    assert r["interrompu"] is True and r["etiquette"] == "essai" and r["config_nom"] == "emergence"
    assert {k: r[k] for k in COMMUNS} == {k: moteur[k] for k in COMMUNS}
    assert r["llm"] is None and r["rag"] is None
    assert not (dossier / "resume.json").exists()


def test_resume_reconstitue_avec_llm_et_systeme1(tmp_path):
    from engine.config import charger_modeles
    from llm.passerelle import Passerelle
    from test_llm import Faux, repondeur_plausible
    cfg = copy.deepcopy(charger_config("scenarios/emergence.yaml"))
    cfg["population"]["deposants"] = 30
    cfg["horloge"]["duree_jours"] = 4
    cfg["rag"]["actif"] = False
    cfg["systeme1"]["moteur"] = "regle"
    faux = Faux(repondeur_plausible)
    p = Passerelle(charger_modeles(), cfg["llm"], {}, fournisseurs={n: faux for n in ("groq", "mistral", "google")})
    dossier = tmp_path / "emergence-g1-20261027-100000"
    puits = PuitsJsonl(dossier)
    (dossier / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    sim = Simulation(cfg, 1, [puits], run_id=dossier.name, passerelle=p)
    moteur = sim.executer()
    sim.bus.fermer()
    r = reconstituer(dossier, ecrire=False)
    for k in ("appels", "ok", "echecs", "tokens_entree", "tokens_sortie", "decisions_llm", "voies", "par_modele"):
        assert r["llm"][k] == moteur["llm"][k], k
    s1_r, s1_m = r["llm"]["systeme1"], moteur["llm"]["systeme1"]
    assert s1_r["escalades"] == s1_m["escalades"] and s1_r["verdicts_llm"] == s1_m["verdicts_llm"]
    assert s1_r["decide_seul"] is None                  # non publié quand l'intention ne change pas


def test_run_coupe_en_plein_milieu(tmp_path):
    dossier, moteur = _run(tmp_path)
    ev = dossier / "events.jsonl"
    lignes = ev.read_text(encoding="utf-8").splitlines(keepends=True)
    moitie = len(lignes) // 2
    ev.write_text("".join(lignes[:moitie]) + lignes[moitie][:25], encoding="utf-8")   # dernière ligne tronquée
    r = reconstituer(dossier)
    assert r["interrompu"] and 0 < r["t_fin"] < moteur["t_fin"]
    assert json.loads((dossier / "resume.json").read_text(encoding="utf-8"))["t_fin"] == r["t_fin"]


def test_quels_runs_reconstituer(tmp_path):
    termine, _ = _run(tmp_path, "a-g1-20261027-100000")
    (termine / "resume.json").write_text("{}", encoding="utf-8")         # allé à son terme : jamais touché
    ancien, _ = _run(tmp_path, "b-g1-20261027-100500")
    vieux = time.time() - 3600
    os.utime(ancien / "events.jsonl", (vieux, vieux))
    en_cours, _ = _run(tmp_path, "c-g1-20261027-101000")                # écrit à l'instant : peut-être en cours
    assert a_reconstituer(tmp_path) == [ancien]
    lance = time.mktime((2026, 10, 27, 10, 10, 0, 0, 0, -1))
    assert a_reconstituer(tmp_path, entre=(lance, lance + 30)) == [en_cours]


def test_archive_distingue_les_runs_interrompus(tmp_path):
    pytest.importorskip("duckdb")
    from services.archiveur import archiver_run, ouvrir
    dossier, _ = _run(tmp_path)
    reconstituer(dossier)
    assert archiver_run(dossier)
    con = ouvrir(tmp_path / "archive.duckdb", tmp_path)
    ligne = con.sql("SELECT interrompu, etiquette, config, mode, systeme1 FROM runs").fetchone()
    assert ligne == (True, "essai", "emergence", "synchrone", "aucun")


def test_run_started_porte_etiquette_et_version_du_code(tmp_path):
    dossier, _ = _run(tmp_path)
    debut = json.loads((dossier / "events.jsonl").open(encoding="utf-8").readline())
    p = debut["payload"]
    assert p["etiquette"] == "essai" and p["systeme1"] == "aucun"      # sans LLM, rien à aiguiller
    assert len(p["code"]["empreinte"]) == 16
