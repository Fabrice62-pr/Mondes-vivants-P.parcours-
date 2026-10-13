"""La carte du moteur (configs/monde.json) et celle de Godot (guichet/scripts/world_data.gd) doivent
rester identiques : sinon les agents s'afficheraient ailleurs que là où le moteur les situe."""
import re

from engine.config import ROOT, charger_monde


def _sites_godot() -> dict:
    gd = (ROOT / "guichet" / "scripts" / "world_data.gd").read_text(encoding="utf-8")
    motif = r'\{"id": "(\w+)", "name": "([^"]+)", "x": (-?[\d.]+), "z": (-?[\d.]+), "kind": "(\w+)"\}'
    return {m[0]: (m[1], float(m[2]), float(m[3]), m[4]) for m in re.findall(motif, gd)}


def test_memes_lieux_et_coordonnees():
    godot = _sites_godot()
    moteur = {l["id"]: l for l in charger_monde()["lieux"]}
    assert set(godot) == set(moteur)
    for i, (nom, x, z, kind) in godot.items():
        l = moteur[i]
        assert (l["nom"], l["x"], l["z"]) == (nom, x, z), i
        assert (l["type"] == "agence") == (kind == "agency"), i


def test_meme_echelle():
    gd = (ROOT / "guichet" / "scripts" / "world_data.gd").read_text(encoding="utf-8")
    assert float(re.search(r"const S := ([\d.]+)", gd)[1]) == charger_monde()["echelle"]
