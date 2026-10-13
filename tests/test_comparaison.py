"""Écran de comparaison des runs : résumés lus dans data/runs/, courbe de l'ICC échantillonnée."""
import json

from services.comparaison import courbe_icc, resumes_runs


def _run(racine, nom, icc, termine=True, deposants=40):
    d = racine / nom
    d.mkdir()
    with (d / "events.jsonl").open("w", encoding="utf-8") as f:
        for t, v in icc:
            f.write(json.dumps({"type": "indicator.computed", "t_sim": t,
                                "payload": {"nom": "icc", "valeur": v}}) + "\n")
            f.write(json.dumps({"type": "indicator.computed", "t_sim": t,
                                "payload": {"nom": "basculement", "valeur": 2}}) + "\n")
    (d / "config.json").write_text(json.dumps({"nom": nom.split("-g")[0], "population": {"deposants": deposants},
                                               "horloge": {"duree_jours": 7}}), encoding="utf-8")
    if termine:
        (d / "resume.json").write_text(json.dumps({"graine": 1, "icc_max": max(v for _, v in icc), "emergence": False,
                                                   "defaut": False, "part_retiree": 0.1, "file_max": 3}),
                                       encoding="utf-8")
    return d


def test_courbe_un_point_par_heure(tmp_path):
    d = _run(tmp_path, "a-g1-1", [(0, 0.1), (15, 0.2), (45, 0.3), (60, 0.5), (75, 0.4)])
    # dernière valeur de chaque heure ; le basculement est ignoré
    assert courbe_icc(d / "events.jsonl") == [[0, 0.3], [60, 0.4]]


def test_runs_termines_seulement(tmp_path):
    _run(tmp_path, "emergence-g1-1", [(0, 0.2)])
    _run(tmp_path, "absence-g1-2", [(0, 0.1)], termine=False)   # arrêté avant la fin : pas de resume.json
    runs = resumes_runs(tmp_path)
    assert [r["run_id"] for r in runs] == ["emergence-g1-1"]
    assert runs[0]["config"] == "emergence" and runs[0]["deposants"] == 40 and runs[0]["icc"] == [[0, 0.2]]


def test_limite_et_dossier_absent(tmp_path):
    for i in range(5):
        _run(tmp_path, f"principal-g{i}-{i}", [(0, 0.1 * i)])
    assert len(resumes_runs(tmp_path, limite=3)) == 3
    assert resumes_runs(tmp_path / "rien") == []
