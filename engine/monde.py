"""Géographie de la vallée (configs/monde.json, partagé avec Godot). Unités : mètres, nord = -z."""
import math
import random
from dataclasses import dataclass


@dataclass(frozen=True)
class Lieu:
    id: str
    nom: str
    type: str          # agence | rencontre
    x: float
    z: float
    poids: float = 0.0


class Monde:
    def __init__(self, data: dict):
        e = data["echelle"]
        self.echelle = e
        self.lieux = {d["id"]: Lieu(d["id"], d["nom"], d["type"], d["x"] * e, d["z"] * e, d.get("poids", 0.0))
                      for d in data["lieux"]}
        self.agences = [l.id for l in self.lieux.values() if l.type == "agence"]
        self.rencontres = [l.id for l in self.lieux.values() if l.type == "rencontre"]
        self.quartiers = {q["id"]: q for q in data["quartiers"]}

    @staticmethod
    def distance(a: tuple[float, float], b: tuple[float, float]) -> float:
        return math.hypot(a[0] - b[0], a[1] - b[1])

    def position(self, lieu_id: str) -> tuple[float, float]:
        l = self.lieux[lieu_id]
        return l.x, l.z

    def tirer_quartier(self, rng: random.Random) -> str:
        ids = sorted(self.quartiers)
        return rng.choices(ids, weights=[self.quartiers[i]["maisons"] for i in ids])[0]

    def tirer_domicile(self, rng: random.Random, quartier: str) -> tuple[float, float]:
        """Un point dans l'anneau du quartier. Godot place ensuite l'agent à la maison la plus proche."""
        q = self.quartiers[quartier]
        a = rng.random() * math.tau
        r = q["r_min"] + rng.random() * (q["r_max"] - q["r_min"])
        return (q["x"] + math.cos(a) * r) * self.echelle, (q["z"] + math.sin(a) * r) * self.echelle
