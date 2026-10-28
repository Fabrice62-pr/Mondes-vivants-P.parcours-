"""Indicateurs de contexte commun (docs/contexte_commun.md). Fonctions pures, réutilisées plus tard
par le service d'indicateurs qui les calculera à partir du flux Kafka."""
from statistics import pstdev


def icc(estimes: list[float], seuil_attente: float = 0.5) -> dict:
    """ICC = A × C.
    A : part des déposants dont estime_autres > 0,5 ;
    C : convergence = 1 - écart-type / 0,5 (borné à [0, 1])."""
    if not estimes:
        return {"icc": 0.0, "attente": 0.0, "convergence": 0.0}
    a = sum(1 for e in estimes if e > seuil_attente) / len(estimes)
    c = min(max(1.0 - pstdev(estimes) / 0.5, 0.0), 1.0)
    return {"icc": round(a * c, 4), "attente": round(a, 4), "convergence": round(c, 4)}


def instant_emergence(serie: list[tuple[int, float]], pas_min: int, seuil: float = 0.6,
                      duree_min: int = 240) -> int | None:
    """Émergence du contexte commun : ICC >= 0,6 pendant 4 h. Renvoie l'instant où la série a commencé."""
    duree = max(1, duree_min // pas_min)
    suite = 0
    for t, v in serie:
        suite = suite + 1 if v >= seuil else 0
        if suite >= duree:
            return t - (duree - 1) * pas_min
    return None
