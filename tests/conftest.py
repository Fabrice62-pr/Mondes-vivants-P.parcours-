import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def _sans_modele_nli(monkeypatch):
    """principal.yaml active le Système 1 nli (un modèle de ~1 Go sur le GPU). Dans les tests, une simulation
    qui ne choisit pas son Système 1 n'en a pas ; test_systeme1.py choisit le sien explicitement."""
    import engine.moteur
    from engine.systeme1 import creer
    monkeypatch.setattr(engine.moteur, "creer_systeme1",
                        lambda cfg: None if (cfg or {}).get("moteur") == "nli" else creer(cfg))
