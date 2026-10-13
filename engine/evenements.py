"""Événements : création de l'enveloppe, validation contre les schémas (F4), routage vers les puits.

Un événement non conforme n'arrête jamais la simulation : il part dans la DLQ avec ses erreurs.
"""
import json
import random
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator

from .config import ROOT

SCHEMAS = ROOT / "schemas"

TOPIC_EVENEMENTS = "guichet.events"
TOPIC_COMMANDES = "guichet.commands"
TOPIC_ETAT = "guichet.state"
TOPIC_DLQ = "guichet.dlq"
_TOPICS = {"llm.called": "guichet.llm.responses", "incident.raised": "guichet.incidents",
           "indicator.computed": "guichet.indicators", "state.snapshot": TOPIC_ETAT}


def topic_pour(type_: str) -> str:
    return _TOPICS.get(type_, TOPIC_EVENEMENTS)


def cle_pour(evt: dict) -> str:
    """Clé de partition : l'agent concerné, sinon l'agence, sinon le monde (ordre garanti par clé)."""
    p = evt.get("payload", {})
    for k in ("agent_id", "client_id", "locuteur_id", "agence_id"):
        if isinstance(p.get(k), str):
            return p[k]
    return "monde"


class Validateur:
    def __init__(self):
        env = json.loads((SCHEMAS / "envelope" / "v1.json").read_text(encoding="utf-8"))
        self._enveloppe = Draft202012Validator(env)
        self._charges = {}
        for d in SCHEMAS.iterdir():
            if d.is_dir() and d.name != "envelope":
                for f in d.glob("v*.json"):
                    sch = json.loads(f.read_text(encoding="utf-8"))
                    self._charges[(d.name, int(f.stem[1:]))] = Draft202012Validator(sch)

    def erreurs(self, evt: dict) -> list[str]:
        errs = [f"enveloppe: {e.message}" for e in self._enveloppe.iter_errors(evt)]
        if errs:
            return errs
        v = self._charges.get((evt["type"], evt["version"]))
        if v is None:
            return [f"aucun schéma pour {evt['type']} v{evt['version']}"]
        return [f"payload: {e.message}" for e in v.iter_errors(evt["payload"])]


class Fabrique:
    """Crée les enveloppes. Les event_id sont tirés d'un générateur à graine : deux runs de même graine
    produisent les mêmes identifiants (comparaison et rejeu facilités ; run_id les distingue)."""

    def __init__(self, run_id: str, producteur: str, graine: int):
        self.run_id = run_id
        self.producteur = producteur
        self.seq = 0
        self._rng = random.Random(f"ids-{graine}-{producteur}")

    def creer(self, type_: str, t_sim: int, payload: dict, causes=()) -> dict:
        evt = {"event_id": str(uuid.UUID(int=self._rng.getrandbits(128), version=4)),
               "type": type_, "version": 1, "run_id": self.run_id, "seq": self.seq, "t_sim": int(t_sim),
               "t_emit": datetime.now(timezone.utc).isoformat(), "t_proc": None,
               "producer": self.producteur, "causes": list(causes), "payload": payload}
        self.seq += 1
        return evt


# ------------------------------------------------------------------ puits

class PuitsMemoire:
    """Garde les événements en mémoire (tests, analyse rapide). Les instantanés d'état sont seulement comptés."""

    def __init__(self):
        self.evenements: list[dict] = []
        self.dlq: list[dict] = []
        self.instantanes = 0

    def envoyer(self, topic: str, cle: str, evt: dict) -> None:
        if topic == TOPIC_ETAT:
            self.instantanes += 1
        else:
            self.evenements.append(evt)

    def rejeter(self, brut: dict | str, erreurs: list[str]) -> None:
        self.dlq.append({"brut": brut, "erreurs": erreurs})

    def fermer(self) -> None:
        pass


class PuitsJsonl:
    """Écrit le run dans data/runs/<run_id>/ : events.jsonl (l'historique), etats.jsonl (les instantanés
    d'état, nécessaires au rejeu pour reproduire la même visualisation, E3) et dlq.jsonl."""

    def __init__(self, dossier: Path):
        dossier.mkdir(parents=True, exist_ok=True)
        self.dossier = dossier
        self._f = (dossier / "events.jsonl").open("w", encoding="utf-8")
        self._etats = (dossier / "etats.jsonl").open("w", encoding="utf-8")
        self._dlq = (dossier / "dlq.jsonl").open("w", encoding="utf-8")

    def envoyer(self, topic: str, cle: str, evt: dict) -> None:
        # L'état courant n'est pas de l'historique (il se reconstruit) : fichier à part, ignoré par l'archiveur.
        (self._etats if topic == TOPIC_ETAT else self._f).write(json.dumps(evt, ensure_ascii=False) + "\n")

    def rejeter(self, brut, erreurs: list[str]) -> None:
        self._dlq.write(json.dumps({"brut": brut, "erreurs": erreurs}, ensure_ascii=False) + "\n")

    def fermer(self) -> None:
        self._f.close()
        self._etats.close()
        self._dlq.close()


class PuitsKafka:
    """Publie dans Kafka et lit les commandes envoyées depuis Godot (topic guichet.commands)."""

    def __init__(self, bootstrap: str, run_id: str, lire_commandes: bool = True):
        from confluent_kafka import Consumer, Producer
        self._p = Producer({"bootstrap.servers": bootstrap, "linger.ms": 20, "client.id": "moteur"})
        self._c = None
        if lire_commandes:
            self._c = Consumer({"bootstrap.servers": bootstrap, "group.id": f"moteur-{run_id}",
                                "auto.offset.reset": "latest", "enable.auto.commit": True})
            self._c.subscribe([TOPIC_COMMANDES])

    def envoyer(self, topic: str, cle: str, evt: dict) -> None:
        self._p.produce(topic, json.dumps(evt, ensure_ascii=False).encode("utf-8"), key=cle.encode("utf-8"))
        self._p.poll(0)

    def rejeter(self, brut, erreurs: list[str]) -> None:
        msg = {"brut": brut, "erreurs": erreurs, "t_rejet": datetime.now(timezone.utc).isoformat()}
        self._p.produce(TOPIC_DLQ, json.dumps(msg, ensure_ascii=False).encode("utf-8"))
        self._p.poll(0)

    def commandes(self) -> list[str]:
        """Messages bruts reçus depuis le dernier appel (non bloquant)."""
        if self._c is None:
            return []
        out = []
        while True:
            m = self._c.poll(0)
            if m is None:
                break
            if not m.error():
                out.append(m.value().decode("utf-8", "replace"))
        return out

    def fermer(self) -> None:
        self._p.flush(10)
        if self._c is not None:
            self._c.close()


class Bus:
    """Valide puis distribue chaque événement à tous les puits. Compte ce qui passe et ce qui est rejeté."""

    def __init__(self, puits: list, valider: bool = True):
        self.puits = puits
        self.validateur = Validateur() if valider else None
        self.compteur: Counter = Counter()
        self.rejets = 0

    def publier(self, evt: dict) -> bool:
        if self.validateur is not None:
            errs = self.validateur.erreurs(evt)
            if errs:
                self.rejeter(evt, errs)
                return False
        topic = topic_pour(evt["type"])
        cle = cle_pour(evt)
        for p in self.puits:
            p.envoyer(topic, cle, evt)
        self.compteur[evt["type"]] += 1
        return True

    def rejeter(self, brut, erreurs: list[str]) -> None:
        self.rejets += 1
        for p in self.puits:
            p.rejeter(brut, erreurs)

    def commandes_recues(self) -> list[str]:
        out = []
        for p in self.puits:
            if hasattr(p, "commandes"):
                out.extend(p.commandes())
        return out

    def fermer(self) -> None:
        for p in self.puits:
            p.fermer()
