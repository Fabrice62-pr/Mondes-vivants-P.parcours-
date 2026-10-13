"""Vérifie les contrats du projet, sans réseau :
  1. chaque schéma d'événement est un JSON Schema valide (draft 2020-12) ;
  2. les exemples déclarés dans les schémas sont conformes, une fois placés dans l'enveloppe ;
  3. un événement volontairement faux est bien rejeté ;
  4. les configurations de run sont cohérentes avec le catalogue de modèles.

    .venv/Scripts/python tools/validate_contracts.py
"""
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine.config import charger_config as load_config  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = ROOT / "schemas"
errors: list[str] = []


def load_schemas() -> tuple[dict, dict]:
    envelope = json.loads((SCHEMAS / "envelope" / "v1.json").read_text(encoding="utf-8"))
    payloads = {}
    for d in sorted(SCHEMAS.iterdir()):
        if d.is_dir() and d.name != "envelope":
            for f in sorted(d.glob("v*.json")):
                payloads[(d.name, int(f.stem[1:]))] = json.loads(f.read_text(encoding="utf-8"))
    return envelope, payloads


def validate_event(event: dict, envelope: dict, payloads: dict) -> list[str]:
    """Même logique que celle qu'appliqueront les consommateurs (non conforme -> guichet.dlq)."""
    errs = [e.message for e in Draft202012Validator(envelope).iter_errors(event)]
    if errs:
        return errs
    key = (event["type"], event["version"])
    if key not in payloads:
        return [f"aucun schéma pour {key[0]} v{key[1]}"]
    return [e.message for e in Draft202012Validator(payloads[key]).iter_errors(event["payload"])]


def wrap(t: str, payload: dict) -> dict:
    return {
        "event_id": str(uuid.uuid4()), "type": t, "version": 1, "run_id": "test", "seq": 0,
        "t_sim": 0, "t_emit": datetime.now(timezone.utc).isoformat(), "producer": "moteur",
        "causes": [], "payload": payload,
    }


def check_schemas() -> None:
    envelope, payloads = load_schemas()
    Draft202012Validator.check_schema(envelope)
    n_examples = 0
    for (t, v), sch in payloads.items():
        try:
            Draft202012Validator.check_schema(sch)
        except Exception as exc:  # schéma lui-même invalide
            errors.append(f"{t} v{v} : schéma invalide : {exc}")
            continue
        for ex in sch.get("examples", []):
            n_examples += 1
            for msg in validate_event(wrap(t, ex), envelope, payloads):
                errors.append(f"{t} v{v} : exemple non conforme : {msg}")
    bad = wrap("decision.made", {"agent_id": "x", "action": "paniquer"})
    if not validate_event(bad, envelope, payloads):
        errors.append("un événement non conforme n'a pas été rejeté")
    print(f"schémas : {len(payloads)} types, {n_examples} exemples vérifiés, rejet d'un événement faux : ok")


def check_configs() -> None:
    models = yaml.safe_load((ROOT / "configs" / "models.yaml").read_text(encoding="utf-8"))["models"]
    for name in ("principal.yaml", "baseline_regles.yaml", "scenarios/emergence.yaml", "scenarios/absence.yaml"):
        cfg = load_config(name)
        pop = cfg["population"]
        if not 0.0 <= pop["part_regles"] <= 1.0:
            errors.append(f"{name} : part_regles hors de [0, 1]")
        rep = pop["repartition_petits"]
        if abs(sum(rep.values()) - 1.0) > 1e-6:
            errors.append(f"{name} : repartition_petits ne somme pas à 1")
        for m in rep:
            if models.get(m, {}).get("tier") != "petit":
                errors.append(f"{name} : {m} n'est pas un modèle de catégorie « petit »")
        for p in cfg["personnages_principaux"]:
            if models.get(p["modele"], {}).get("tier") != "grand":
                errors.append(f"{name} : {p['id']} doit avoir un modèle de catégorie « grand »")
            if not p.get("justification"):
                errors.append(f"{name} : {p['id']} sans justification d'attribution (A5)")
        if cfg["mode"] not in ("synchrone", "asynchrone"):
            errors.append(f"{name} : mode inconnu {cfg['mode']}")
        print(f"config {name} : {pop['deposants']} déposants, {pop['part_regles']:.0%} à règles, "
              f"{len(cfg['personnages_principaux'])} personnages principaux")


if __name__ == "__main__":
    check_schemas()
    check_configs()
    if errors:
        print("\nERREURS :")
        for e in errors:
            print(" -", e)
        sys.exit(1)
    print("\nTous les contrats sont valides.")
