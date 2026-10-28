"""Résumé d'un run interrompu, recalculé à partir de son historique (events.jsonl).

Un run allé à son terme écrit lui-même resume.json (engine/run.py). Un run arrêté depuis Godot, fermé ou
planté n'en avait pas : il restait invisible pour l'archive et l'écran de comparaison. Ce module refait
les calculs du moteur sur les événements et écrit un resume.json marqué « interrompu ».

Ce que l'historique ne permet pas de retrouver vaut None : après un défaut, les pertes des déposants qui
n'étaient pas au guichet ; pour le Système 1, les décisions prises sans changer d'intention (non publiées).

    python -m services.reconstitution                 # runs sans resume.json et inactifs depuis 10 min
    python -m services.reconstitution --run <run_id>
"""
import argparse
import json
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from engine.config import ROOT
from engine.horloge import Horloge
from engine.indicateurs import instant_emergence

RUNS = ROOT / "data" / "runs"
INACTIF_S = 600              # un events.jsonl qui n'a pas bougé depuis 10 min n'appartient plus à un run en cours


def _date(t_emit: str | None) -> float | None:
    try:
        return datetime.fromisoformat(t_emit).timestamp() if t_emit else None
    except ValueError:
        return None


def reconstituer(dossier: Path, ecrire: bool = True) -> dict | None:
    """Résumé du run de `dossier` ; None s'il n'a pas commencé (aucun run.started ou aucun pas simulé)."""
    ev_f = dossier / "events.jsonl"
    if not ev_f.exists():
        return None
    cfg_f = dossier / "config.json"
    cfg = json.loads(cfg_f.read_text(encoding="utf-8")) if cfg_f.exists() else {}
    debut = None
    par_type: Counter = Counter()
    t_fin, premier, dernier = 0, None, None
    depots_initiaux = paye = perdu = garanti = 0.0
    perdants: set[str] = set()
    files: dict[str, int] = {}
    file_max = 0
    serie_icc: list[tuple[int, float]] = []
    defaut = None
    solvable = True
    appels: dict[str, dict] = {}
    decisions_llm, replis = 0, 0
    voies: Counter = Counter()
    escalades: Counter = Counter()
    verdicts: Counter = Counter()
    with ev_f.open(encoding="utf-8") as f:
        for ligne in f:
            try:
                e = json.loads(ligne)
            except ValueError:                       # dernière ligne coupée par l'arrêt brutal du moteur
                continue
            typ, p = e.get("type"), e.get("payload") or {}
            par_type[typ] += 1
            t_fin = max(t_fin, e.get("t_sim", 0))
            d = _date(e.get("t_emit"))
            premier = premier if premier is not None else d
            dernier = d or dernier
            if typ == "run.started":
                debut = p
                depots_initiaux = sum(a.get("depot", 0.0) for a in p.get("attribution", []) if a.get("role") == "deposant")
            elif typ == "withdrawal.served":
                paye += p["paye"]
                perdu += p["perdu"]
                garanti += p["garanti"]
                if p["perdu"] > 0:
                    perdants.add(p["agent_id"])
            elif typ == "queue.changed":
                files[p["agence_id"]] = p["longueur"]
                file_max = max(file_max, sum(files.values()))
            elif typ == "indicator.computed" and p.get("nom") == "icc":
                serie_icc.append((e["t_sim"], float(p["valeur"])))
            elif typ == "caisse.defaulted":
                defaut, solvable = e["t_sim"], p["solvable"]
            elif typ == "caisse.updated" and defaut is None:
                solvable = p["fonds_propres"] >= 0
            elif typ == "llm.called":
                s = appels.setdefault(p["modele"], {"appels": 0, "ok": 0, "echecs": 0, "tokens_entree": 0,
                                                    "tokens_sortie": 0, "latence_ms_total": 0})
                s["appels"] += 1
                s["ok" if p["statut"] == "ok" else "echecs"] += 1
                s["tokens_entree"] += p["tokens_entree"]
                s["tokens_sortie"] += p["tokens_sortie"]
                s["latence_ms_total"] += p["latence_ms"]
            elif typ == "decision.made":
                avis = p.get("systeme1")
                if p["type"] == "llm":
                    decisions_llm += 1
                    voies[p.get("voie") or "dans_le_pas"] += 1
                    if avis and avis.get("escalade"):
                        escalades[avis["escalade"]] += 1
                        retire = p["action"] == "retirer"
                        verdicts[("retrait_confirme" if retire else "retrait_infirme") if avis["escalade"] == "retrait"
                                 else ("ambigu_retirer" if retire else "ambigu_non")] += 1
                elif p["type"] == "repli_regles" or (p["type"] == "systeme1" and p["justification"].startswith("repli")):
                    replis += 1
    if debut is None or t_fin == 0:
        return None

    horloge = Horloge(cfg["horloge"], cfg["routine"]) if "horloge" in cfg and "routine" in cfg else None
    libelle = (lambda t: horloge.libelle(t)) if horloge else (lambda t: f"t = {t} min")
    pas = debut.get("horloge", {}).get("pas_min", 15)
    t_emergence = instant_emergence(serie_icc, pas)
    dlq = dossier / "dlq.jsonl"
    llm_actif = bool(appels) or bool(cfg.get("llm", {}).get("actif") and
                                     any(a.get("type") == "llm" for a in debut.get("attribution", [])))
    tot = {k: sum(s[k] for s in appels.values()) for k in ("appels", "ok", "echecs", "tokens_entree", "tokens_sortie")}
    moteur_s1 = debut.get("systeme1", "aucun")
    r = {
        "run_id": dossier.name, "graine": debut.get("graine"), "etiquette": debut.get("etiquette"),
        "config_nom": debut.get("config_nom"), "mode": debut.get("mode"),
        "interrompu": True, "fin": libelle(t_fin), "t_fin": t_fin,
        "defaut": defaut is not None, "t_defaut": defaut,
        "defaut_libelle": libelle(defaut) if defaut is not None else None,
        "solvable": solvable, "part_retiree": round(paye / max(depots_initiaux, 1.0), 4),
        "retraits": par_type["withdrawal.served"],
        "deposants_perdants": None if defaut is not None else len(perdants),
        "perte_totale": None if defaut is not None else round(perdu, 2),
        "garanti_total": None if defaut is not None else round(garanti, 2),
        "file_max": file_max, "icc_max": max((v for _, v in serie_icc), default=0.0),
        "emergence": t_emergence is not None, "t_emergence": t_emergence,
        "conversations": par_type["conversation.held"], "communiques": par_type["communique.published"],
        "evenements": sum(par_type.values()), "par_type": dict(sorted(par_type.items())),
        "rejets_dlq": sum(1 for _ in dlq.open(encoding="utf-8")) if dlq.exists() else 0,
        "llm": None if not llm_actif else {
            **tot,
            "par_modele": {m: {**{k: v for k, v in s.items() if k != "latence_ms_total"},
                               "latence_ms_moyenne": round(s["latence_ms_total"] / max(s["appels"], 1))}
                           for m, s in sorted(appels.items())},
            "decisions_llm": decisions_llm, "voies": {v: voies[v] for v in ("dans_le_pas", "en_retard")},
            "replis": replis, "incidents": par_type["incident.raised"],
            "systeme1": None if moteur_s1 == "aucun" else {
                "moteur": moteur_s1, "confiance_min": cfg.get("systeme1", {}).get("confiance_min"),
                "evaluations": None, "decide_seul": None, "reportees": None,
                "escalades": {k: escalades[k] for k in ("retrait", "ambigu")},
                "verdicts_llm": {k: verdicts[k] for k in ("retrait_confirme", "retrait_infirme",
                                                          "ambigu_retirer", "ambigu_non")}}},
        "rag": None if not cfg.get("rag", {}).get("actif") or not llm_actif else {
            "souvenirs": par_type["memory.written"], "rappels": None,
            "violations_c6": par_type["temporal.violation"]},
        "duree_reelle_s": round(dernier - premier, 2) if premier is not None and dernier is not None else None,
    }
    if ecrire:
        (dossier / "resume.json").write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    return r


def date_lancement(run_id: str) -> float | None:
    """Horodatage contenu dans le run_id (« principal-g1-20261027-094323 », engine/run.py)."""
    try:
        return datetime.strptime(run_id[-15:], "%Y%m%d-%H%M%S").timestamp()
    except ValueError:
        return None


def a_reconstituer(racine: Path = RUNS, entre: tuple[float, float] | None = None) -> list[Path]:
    """Runs sans resume.json. entre (debut, fin) : ceux lancés dans cet intervalle (le moteur que le bridge
    vient de voir s'arrêter) ; sinon ceux dont l'historique n'a pas bougé depuis INACTIF_S (pas en cours)."""
    if not racine.exists():
        return []
    out = []
    for d in sorted(p for p in racine.iterdir() if p.is_dir()):
        ev = d / "events.jsonl"
        if (d / "resume.json").exists() or not ev.exists() or ev.stat().st_size == 0:
            continue
        if entre is not None:
            lance = date_lancement(d.name)
            # run_id est à la seconde près : une seconde de marge avant le lancement du processus
            if lance is not None and entre[0] - 1 <= lance <= entre[1]:
                out.append(d)
        elif time.time() - ev.stat().st_mtime >= INACTIF_S:
            out.append(d)
    return out


def reconstituer_tout(racine: Path = RUNS) -> list[str]:
    return [d.name for d in a_reconstituer(racine) if reconstituer(d) is not None]


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Résumés des runs interrompus")
    ap.add_argument("--run", help="ce run seulement (un résumé déjà reconstitué est recalculé)")
    a = ap.parse_args(argv)
    if a.run:
        resume = RUNS / a.run / "resume.json"
        if resume.exists() and not json.loads(resume.read_text(encoding="utf-8")).get("interrompu"):
            print("ce run est allé à son terme : son résumé, écrit par le moteur, est conservé")
            return 1
        r = reconstituer(RUNS / a.run)
        print("reconstitué" if r else "run inconnu ou vide")
    else:
        faits = reconstituer_tout()
        print(f"{len(faits)} run(s) interrompu(s) reconstitué(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
