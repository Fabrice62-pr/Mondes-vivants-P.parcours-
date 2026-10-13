"""Lancement d'un ou plusieurs runs, sans Godot.

    python -m engine.run --config principal.yaml
    python -m engine.run --config scenarios/emergence.yaml --graines 1-5
    python -m engine.run --config principal.yaml --deposants 40 --puits kafka,jsonl --rythme 0.5

--puits    : où publier (kafka, jsonl, memoire), séparés par des virgules. Défaut : jsonl.
--rythme   : secondes réelles par pas simulé (0 = aussi vite que possible ; utile pour regarder dans Godot).
--deposants, --jours, --mode : remplacent la configuration au lancement (C2), sans modifier de code.
--sans-llm : tous les agents décident par règles (aucun appel, aucun quota consommé).
"""
import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime

from .config import ROOT, charger_config
from .evenements import PuitsJsonl, PuitsKafka, PuitsMemoire
from .moteur import Simulation


def charger_env() -> None:
    f = ROOT / ".env"
    if f.exists():
        for ligne in f.read_text(encoding="utf-8").splitlines():
            ligne = ligne.strip()
            if ligne and not ligne.startswith("#") and "=" in ligne:
                k, v = ligne.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def graines(texte: str) -> list[int]:
    out = []
    for part in texte.split(","):
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


def executer(cfg: dict, graine: int, puits_noms: list[str], rythme: float, valider: bool = True) -> dict:
    run_id = f"{cfg['nom']}-g{graine}-{datetime.now():%Y%m%d-%H%M%S}"
    puits, dossier = [], None
    if "kafka" in puits_noms:
        puits.append(PuitsKafka(os.environ.get("KAFKA_BOOTSTRAP", "localhost:9092"), run_id))
    if "jsonl" in puits_noms:
        dossier = ROOT / "data" / "runs" / run_id
        puits.append(PuitsJsonl(dossier))
    if "memoire" in puits_noms or not puits:
        puits.append(PuitsMemoire())
    sim = Simulation(cfg, graine, puits, run_id, valider)
    if sim.asynchrone and rythme <= 0:
        # En asynchrone, l'horloge simulée avance en temps réel : sans rythme, les réponses des LLM
        # arriveraient des jours simulés plus tard.
        rythme = float(cfg.get("asynchrone", {}).get("rythme_s", 0.5))
    t0 = time.perf_counter()
    while not sim.fini:
        while sim.pause and not sim.fini:          # pause demandée depuis Godot
            time.sleep(0.2)
            sim.traiter_commandes_externes()
        sim.pas()
        attente = sim.rythme_s if sim.rythme_s is not None else rythme
        if attente > 0:
            time.sleep(attente)
    sim.bus.fermer()
    r = sim.resume()
    r.update({"run_id": run_id, "graine": graine, "duree_reelle_s": round(time.perf_counter() - t0, 2)})
    if dossier is not None:
        (dossier / "resume.json").write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
        (dossier / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        try:                                       # archive Parquet du run (F5) ; un échec n'invalide pas le run
            from services.archiveur import archiver_run
            archiver_run(dossier)
        except Exception as e:
            print(f"(archivage impossible : {e})")
    return r


def ligne(r: dict) -> str:
    d = f"défaut {r['defaut_libelle']} ({'solvable' if r['solvable'] else 'insolvable'})" if r["defaut"] else "pas de défaut"
    e = "oui" if r["emergence"] else "non"
    return (f"graine {r['graine']:>3} | {d:<44} | retiré {r['part_retiree']:6.1%} | file max {r['file_max']:>3} | "
            f"ICC max {r['icc_max']:.2f} | émergence {e:<3} | {r['evenements']:>6} évts | DLQ {r['rejets_dlq']} | "
            f"{r['duree_reelle_s']:.1f} s")


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Moteur GUICHET (sans Godot)")
    ap.add_argument("--config", default="principal.yaml")
    ap.add_argument("--graines", help="ex. 1-5 ou 1,3,7 (défaut : la graine de la configuration)")
    ap.add_argument("--deposants", type=int)
    ap.add_argument("--jours", type=int)
    ap.add_argument("--mode", choices=["synchrone", "asynchrone"])
    ap.add_argument("--puits", default="jsonl")
    ap.add_argument("--rythme", type=float, default=0.0)
    ap.add_argument("--sans-validation", action="store_true", help="déconseillé : désactive le contrôle F4")
    ap.add_argument("--sans-llm", action="store_true", help="tous les agents décident par règles")
    a = ap.parse_args(argv)

    charger_env()
    cfg = charger_config(a.config)
    if a.deposants is not None:
        cfg["population"]["deposants"] = a.deposants
    if a.jours is not None:
        cfg["horloge"]["duree_jours"] = a.jours
    if a.mode:
        cfg["mode"] = a.mode
    if a.sans_llm:
        cfg["llm"]["actif"] = False
    liste = graines(a.graines) if a.graines else [cfg["graine"]]
    puits = [p.strip() for p in a.puits.split(",") if p.strip()]

    print(f"Configuration « {cfg['nom']} » : {cfg['population']['deposants']} déposants + personnages principaux, "
          f"{cfg['horloge']['duree_jours']} jours, mode {cfg['mode']}, LLM {'actifs' if cfg['llm']['actif'] else 'inactifs'}, "
          f"puits {','.join(puits)}")
    resultats = []
    for g in liste:
        r = executer(cfg, g, puits, a.rythme, not a.sans_validation)
        resultats.append(r)
        print(ligne(r))
    if len(resultats) > 1:
        def stat(cle):
            v = [float(r[cle]) for r in resultats]
            return f"{statistics.mean(v):.3f} ± {statistics.stdev(v):.3f} (min {min(v):.3f}, max {max(v):.3f})"
        print(f"\nSur {len(resultats)} runs : défaut dans {sum(r['defaut'] for r in resultats)} run(s), "
              f"émergence dans {sum(r['emergence'] for r in resultats)} run(s)")
        print(f"  part retirée : {stat('part_retiree')}")
        print(f"  ICC max      : {stat('icc_max')}")
        print(f"  file max     : {stat('file_max')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
