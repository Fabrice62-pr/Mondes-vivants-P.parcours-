"""Chargement des configurations de run (YAML avec héritage) et des données partagées."""
import hashlib
import json
import subprocess
from functools import lru_cache
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


def echeance(cfg: dict) -> float | None:
    """Attente maximale des réponses LLM dans un pas, en secondes réelles (C4) : None = sans limite
    (synchrone), 0 = aucune attente (asynchrone), hybride.echeance_s entre les deux."""
    mode = cfg["mode"]
    if mode == "synchrone":
        return None
    if mode == "asynchrone":
        return 0.0
    if mode == "hybride":
        e = float(cfg["hybride"]["echeance_s"])
        if e < 0:
            raise ValueError(f"hybride.echeance_s négative : {e}")
        return e
    raise ValueError(f"mode inconnu : {mode}")


def empreinte(cfg: dict) -> str:
    """Empreinte stable d'une configuration, publiée dans run.started (rejouabilité, E3)."""
    brut = json.dumps(cfg, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(brut.encode("utf-8")).hexdigest()[:16]


@lru_cache(maxsize=1)                                # le code ne change pas pendant un processus
def version_code() -> dict:
    """Version du code qui a produit un run (run.started.code) : le commit git, s'il reste des
    modifications non enregistrées, et une empreinte du code des agents (engine, llm, rag). Deux runs de même
    empreinte ont tourné avec les mêmes prompts et les mêmes règles, même entre deux commits."""
    h = hashlib.sha256()
    for f in sorted(p for d in ("engine", "llm", "rag") for p in (ROOT / d).glob("*.py")):
        h.update(f.relative_to(ROOT).as_posix().encode("utf-8"))
        h.update(f.read_bytes().replace(b"\r\n", b"\n"))
    commit = modifie = None
    try:
        sortie = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                                timeout=5)
        if sortie.returncode == 0:
            commit = sortie.stdout.strip()
            etat = subprocess.run(["git", "status", "--porcelain", "--", "engine", "llm", "rag", "configs"],
                                  cwd=ROOT, capture_output=True, text=True, timeout=5)
            modifie = bool(etat.stdout.strip()) if etat.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        pass
    return {"commit": commit, "modifie": modifie, "empreinte": h.hexdigest()[:16]}


def charger_modeles() -> dict:
    return yaml.safe_load((CONFIGS / "models.yaml").read_text(encoding="utf-8"))


def charger_monde() -> dict:
    return json.loads((CONFIGS / "monde.json").read_text(encoding="utf-8"))
