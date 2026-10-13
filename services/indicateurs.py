"""Service d'indicateurs : l'ICC recalculé à partir du flux Kafka, indépendamment du moteur (B2, F6, C7).

    python -m services.indicateurs                    # consomme Kafka en continu (lancé par lancer.bat)
    python -m services.indicateurs --verifier <run>   # recalcule l'ICC d'un run enregistré et le compare

Il lit les instantanés d'état (guichet.state) et les variations de croyance (guichet.events), et publie
dans guichet.indicators, avec le producteur « indicateurs » :
- `icc_flux` : l'ICC du pas, recalculé à partir de l'instantané (doit être égal à celui du moteur) ;
- `icc_2h`   : sa moyenne sur une fenêtre glissante de 2 h simulées (docs/contexte_commun.md).

Chaque valeur porte ses `sources` (les belief.updated du pas, F6) et son `retard_ms` : le temps réel
entre l'émission de l'instantané par le moteur et la publication de l'indicateur (C7). Pendant un
rejeu, les événements ont leur date d'émission d'origine : le retard est alors compté à partir de
l'arrivée du run.started rejoué.
"""
import argparse
import json
import os
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from engine.config import ROOT
from engine.evenements import Fabrique, Validateur
from engine.indicateurs import icc

RUNS = ROOT / "data" / "runs"
TOPIC_SORTIE = "guichet.indicators"


def _instant(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


class ServiceIndicateurs:
    def __init__(self, fenetre_min: int = 120, horloge=time.time):
        self.fenetre_min = fenetre_min
        self.horloge = horloge
        self.run_id: str | None = None
        self.fab: Fabrique | None = None
        self.pas_min = 15
        self._fenetre: deque = deque()
        self._croyances: list[str] = []
        self._decalage = 0.0            # rejeu : écart entre la date d'émission d'origine et maintenant
        self.retards: list[int] = []

    def traiter(self, evt: dict) -> list[dict]:
        """Un événement entrant -> les indicator.computed à publier (enveloppes complètes)."""
        typ = evt.get("type")
        if typ == "run.started":
            self.run_id = evt["run_id"]
            self.fab = Fabrique(self.run_id, "indicateurs", int(self.horloge()))
            self.pas_min = int(evt["payload"]["horloge"]["pas_min"])
            self._fenetre = deque(maxlen=max(1, self.fenetre_min // self.pas_min))
            self._croyances, self.retards = [], []
            ecart = self.horloge() - _instant(evt["t_emit"])
            self._decalage = ecart if ecart > 60 else 0.0     # au-delà d'une minute : c'est un rejeu
            return []
        if self.fab is None or evt.get("run_id") != self.run_id:
            return []
        if typ == "belief.updated":
            self._croyances.append(evt["event_id"])
            return []
        if typ != "state.snapshot":
            return []
        v = icc([a["estime_autres"] for a in evt["payload"]["agents"]])
        self._fenetre.append(v["icc"])
        retard = max(0, int((self.horloge() - _instant(evt["t_emit"]) - self._decalage) * 1000))
        self.retards.append(retard)
        sources, self._croyances = self._croyances, []
        t = evt["t_sim"]
        return [
            self.fab.creer("indicator.computed", t, {
                "nom": "icc_flux", "valeur": v["icc"], "fenetre_min": 0,
                "composantes": {"attente": v["attente"], "convergence": v["convergence"]},
                "sources": sources, "retard_ms": retard}, [evt["event_id"]]),
            self.fab.creer("indicator.computed", t, {
                "nom": "icc_2h", "valeur": round(sum(self._fenetre) / len(self._fenetre), 4),
                "fenetre_min": self.fenetre_min, "composantes": {"pas": len(self._fenetre)},
                "sources": sources, "retard_ms": retard}, [evt["event_id"]]),
        ]


def verifier(run_id: str, runs: Path = RUNS) -> dict:
    """E3, F6 : l'ICC recalculé à partir des seuls instantanés enregistrés est-il celui du moteur ?"""
    from services.rejeu import charger
    service = ServiceIndicateurs()
    recalcule, moteur = {}, {}
    for e in charger(run_id, runs):
        if e["type"] == "indicator.computed" and e["payload"]["nom"] == "icc":
            moteur[e["t_sim"]] = e["payload"]["valeur"]
        for s in service.traiter(e):
            if s["payload"]["nom"] == "icc_flux":
                recalcule[s["t_sim"]] = s["payload"]["valeur"]
    communs = sorted(set(recalcule) & set(moteur))
    ecart = max((abs(recalcule[t] - moteur[t]) for t in communs), default=None)
    # Les instantanés arrondissent les croyances à 3 décimales : un écart d'arrondi (< 0,001) est attendu.
    return {"pas_compares": len(communs), "ecart_max": ecart, "identique": bool(communs) and ecart < 1e-3}


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Service d'indicateurs (Kafka)")
    ap.add_argument("--verifier", metavar="RUN_ID", help="recalcule l'ICC d'un run enregistré, sans Kafka")
    a = ap.parse_args(argv)
    if a.verifier:
        print(json.dumps(verifier(a.verifier), ensure_ascii=False))
        return 0
    from confluent_kafka import Consumer, Producer
    from engine.run import charger_env
    charger_env()
    bootstrap = os.environ.get("KAFKA_BOOTSTRAP", "localhost:9092")
    c = Consumer({"bootstrap.servers": bootstrap, "group.id": f"indicateurs-{os.getpid()}",
                  "auto.offset.reset": "latest", "enable.auto.commit": False})
    c.subscribe(["guichet.events", "guichet.state"])
    p = Producer({"bootstrap.servers": bootstrap, "client.id": "indicateurs", "linger.ms": 5})
    validateur, service = Validateur(), ServiceIndicateurs()
    print(f"[indicateurs] à l'écoute de Kafka ({bootstrap})", flush=True)
    while True:
        m = c.poll(0.2)
        if m is None or m.error():
            continue
        try:
            evt = json.loads(m.value())
        except ValueError:
            continue
        for sortie in service.traiter(evt):
            errs = validateur.erreurs(sortie)
            if errs:
                p.produce("guichet.dlq", json.dumps({"brut": sortie, "erreurs": errs}, ensure_ascii=False).encode("utf-8"))
            else:
                p.produce(TOPIC_SORTIE, json.dumps(sortie, ensure_ascii=False).encode("utf-8"), key=b"monde")
        p.poll(0)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        pass
