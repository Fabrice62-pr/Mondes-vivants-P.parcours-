"""Chargement des configurations de run (YAML avec héritage) et des données partagées."""
import hashlib
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIGS = ROOT / "configs"


def _fusion(base: dict, dessus: dict) -> dict:
    out = dict(base)
    for k, v in dessus.items():
        out[k] = _fusion(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def charger_config(chemin: str | Path) -> dict:
    """Charge une configuration. La clé `herite` désigne un fichier parent, relatif au fichier courant ;
    les dictionnaires sont fusionnés récursivement, les listes et valeurs sont remplacées."""
    p = Path(chemin)
    if not p.exists():
        p = CONFIGS / p
    cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    parent = cfg.pop("herite", None)
    if parent:
        cfg = _fusion(charger_config((p.parent / parent).resolve()), cfg)
    return cfg


def empreinte(cfg: dict) -> str:
    """Empreinte stable d'une configuration, publiée dans run.started (rejouabilité, E3)."""
    brut = json.dumps(cfg, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(brut.encode("utf-8")).hexdigest()[:16]


def charger_modeles() -> dict:
    return yaml.safe_load((CONFIGS / "models.yaml").read_text(encoding="utf-8"))


def charger_monde() -> dict:
    return json.loads((CONFIGS / "monde.json").read_text(encoding="utf-8"))
