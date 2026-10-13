"""Rejeu d'un run passé, à partir des seules données enregistrées (E3).

    python -m services.rejeu --run emergence-g1-20261013-155951 --rythme 0.5
    python -m services.rejeu --run <run_id> --verifier        # contrôle sans Kafka : rejeu identique ?

Le rejeu relit data/runs/<run_id>/events.jsonl et etats.jsonl et les republie **tels quels**, dans leur
ordre d'origine (`seq`), pas après pas. Aucun calcul, aucun appel LLM : Godot reçoit exactement ce qu'il
a reçu pendant le run, et affiche donc la même scène et les mêmes indicateurs (ceux-ci sont des
événements enregistrés). Seul `t_proc` change : il est posé par le bridge au moment du rejeu (C3).

Depuis Godot, pause, reprise et vitesse agissent sur le rejeu (commandes lues dans guichet.commands) ;
les autres leviers sont ignorés, puisqu'on ne réécrit pas l'histoire.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

from engine.config import ROOT
from engine.evenements import TOPIC_ETAT, cle_pour, topic_pour

RUNS = ROOT / "data" / "runs"


def charger(run_id: str, runs: Path = RUNS) -> list[dict]:
    """Événements et instantanés du run, fusionnés dans l'ordre d'émission d'origine."""
    d = runs / run_id
    if not (d / "events.jsonl").exists():
        raise FileNotFoundError(f"run inconnu : {run_id}")
    out = []
    for nom in ("events.jsonl", "etats.jsonl"):
        f = d / nom
        if f.exists():
            out.extend(json.loads(l) for l in f.open(encoding="utf-8") if l.strip())
    out.sort(key=lambda e: e["seq"])
    return out


def pas(evenements: list[dict]):
    """Regroupe les événements par pas : un nouveau pas commence à chaque clock.tick."""
    courant: list[dict] = []
    for e in evenements:
        if e["type"] == "clock.tick" and courant:
            yield courant
            courant = []
        courant.append(e)
    if courant:
        yield courant


class Rejeu:
    def __init__(self, run_id: str, puits, rythme: float = 0.5, runs: Path = RUNS):
        self.run_id = run_id
        self.evenements = charger(run_id, runs)
        self.puits = puits
        self.rythme = rythme
        self.pause = False
        self.publies = 0

    def _commandes(self) -> None:
        if not hasattr(self.puits, "commandes"):
            return
        for brut in self.puits.commandes():
            try:
                p = json.loads(brut)["payload"]
            except (ValueError, KeyError, TypeError):
                continue
            if p.get("commande") == "pause":
                self.pause = True
            elif p.get("commande") == "reprendre":
                self.pause = False
            elif p.get("commande") == "vitesse":
                self.rythme = float((p.get("parametres") or {}).get("rythme_s", self.rythme))

    def executer(self) -> int:
        for groupe in pas(self.evenements):
            self._commandes()
            while self.pause:
                time.sleep(0.2)
                self._commandes()
            for e in groupe:
                self.puits.envoyer(topic_pour(e["type"]), cle_pour(e), e)
                self.publies += 1
            if self.rythme > 0:
                time.sleep(self.rythme)
        self.puits.fermer()
        return self.publies


def verifier(run_id: str, runs: Path = RUNS) -> dict:
    """E3 : le rejeu publie exactement les événements enregistrés, dans le même ordre, sur les mêmes topics."""
    from engine.evenements import PuitsMemoire
    puits = PuitsMemoire()
    Rejeu(run_id, puits, 0.0, runs).executer()
    source = charger(run_id, runs)
    attendus = [e for e in source if topic_pour(e["type"]) != TOPIC_ETAT]
    return {"evenements": len(attendus), "instantanes": puits.instantanes,
            "identique": puits.evenements == attendus and puits.instantanes == len(source) - len(attendus)}


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Rejeu d'un run passé (E3)")
    ap.add_argument("--run", required=True)
    ap.add_argument("--rythme", type=float, default=0.5, help="secondes réelles par pas")
    ap.add_argument("--verifier", action="store_true", help="contrôle du rejeu, sans Kafka")
    a = ap.parse_args(argv)
    if a.verifier:
        print(json.dumps(verifier(a.run), ensure_ascii=False))
        return 0
    from engine.evenements import PuitsKafka
    from engine.run import charger_env
    charger_env()
    puits = PuitsKafka(os.environ.get("KAFKA_BOOTSTRAP", "localhost:9092"), f"rejeu-{a.run}-{int(time.time())}")
    n = Rejeu(a.run, puits, a.rythme).executer()
    print(f"rejeu de {a.run} : {n} messages publiés")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
