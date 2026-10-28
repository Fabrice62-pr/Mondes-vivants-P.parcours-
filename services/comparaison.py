"""Résumés des runs passés, pour l'écran de comparaison de Godot.

Chaque run terminé a un dossier data/runs/<run_id>/ avec resume.json, config.json et events.jsonl.
Un run arrêté avant la fin n'a pas de resume.json tant que services/reconstitution.py ne l'a pas résumé
(fait par le bridge à l'arrêt du moteur) ; il apparaît alors avec interrompu = true.
"""
import json
from pathlib import Path

PAS_COURBE_MIN = 60          # un point de la courbe de l'ICC par heure simulée

_cache: dict[str, tuple[float, dict]] = {}   # run_id -> (date de modification de resume.json, résumé)


def courbe_icc(events: Path, pas_min: int = PAS_COURBE_MIN) -> list[list[float]]:
    """Série [t_sim, icc] échantillonnée : la dernière valeur de chaque tranche de `pas_min` minutes."""
    points: dict[int, float] = {}
    with events.open(encoding="utf-8") as f:
        for ligne in f:
            if '"indicator.computed"' not in ligne or '"icc"' not in ligne:
                continue
            e = json.loads(ligne)
            if e["payload"]["nom"] == "icc":
                points[e["t_sim"] // pas_min] = float(e["payload"]["valeur"])
    return [[k * pas_min, round(v, 4)] for k, v in sorted(points.items())]


def resume_run(dossier: Path) -> dict | None:
    fichier = dossier / "resume.json"
    if not fichier.exists():
        return None
    date = fichier.stat().st_mtime
    deja = _cache.get(dossier.name)
    if deja and deja[0] == date:
        return deja[1]
    r = json.loads(fichier.read_text(encoding="utf-8"))
    cfg_f = dossier / "config.json"
    cfg = json.loads(cfg_f.read_text(encoding="utf-8")) if cfg_f.exists() else {}
    ev = dossier / "events.jsonl"
    out = {
        "run_id": dossier.name,
        "config": cfg.get("nom") or r.get("config_nom") or dossier.name.split("-g")[0],
        "graine": r.get("graine"),
        "deposants": cfg.get("population", {}).get("deposants"),
        "duree_jours": cfg.get("horloge", {}).get("duree_jours", 7),
        "icc_max": r.get("icc_max"),
        "emergence": r.get("emergence"),
        "t_emergence": r.get("t_emergence"),
        "defaut": r.get("defaut"),
        "t_defaut": r.get("t_defaut"),
        "defaut_libelle": r.get("defaut_libelle"),
        "solvable": r.get("solvable"),
        "part_retiree": r.get("part_retiree"),
        "file_max": r.get("file_max"),
        "perte_totale": r.get("perte_totale"),
        "communiques": r.get("communiques"),
        "fin": r.get("fin"),
        "interrompu": bool(r.get("interrompu")),
        "etiquette": r.get("etiquette"),
        "mode": cfg.get("mode") or r.get("mode") or "synchrone",
        "llm": bool(cfg.get("llm", {}).get("actif")),
        "systeme1": ((r.get("llm") or {}).get("systeme1") or {}).get("moteur", "aucun"),
        "appels_llm": (r.get("llm") or {}).get("appels", 0),
        "rejouable": (dossier / "etats.jsonl").exists(),
        "icc": courbe_icc(ev) if ev.exists() else [],
    }
    _cache[dossier.name] = (date, out)
    return out


def resumes_runs(racine: Path, limite: int = 12) -> list[dict]:
    """Les `limite` derniers runs terminés, du plus récent au plus ancien."""
    if not racine.exists():
        return []
    dossiers = sorted((d for d in racine.iterdir() if (d / "resume.json").exists()),
                      key=lambda d: (d / "resume.json").stat().st_mtime, reverse=True)
    return [r for r in (resume_run(d) for d in dossiers[:limite]) if r is not None]
