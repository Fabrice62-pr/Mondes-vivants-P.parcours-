"""Construction de la population et attribution FIGÉE des modèles (A5, A6).

- Personnages principaux : grand modèle, attribué nominativement dans la configuration.
- Déposants ordinaires : les `part_regles` les moins sociables sont des agents à règles (A6) ;
  les autres reçoivent un petit modèle, réparti entre fournisseurs de façon équilibrée À L'INTÉRIEUR
  de chaque profil (choix de charge, pas choix cognitif).
L'attribution dépend uniquement de la configuration et de la graine : elle est identique à chaque relance.
"""
import random

from .agents import Deposant, Institutionnel
from .monde import Monde

ORDRE_PROFILS = ["connecte", "fidele", "commercant", "prudent"]


def _tirer(rng: random.Random, v):
    return rng.uniform(v[0], v[1]) if isinstance(v, list) else v


def _quotas(n: int, repartition: dict) -> list[str]:
    """Répartit n places selon des proportions (méthode du plus fort reste), dans un ordre stable."""
    noms = sorted(repartition)
    exact = {m: n * repartition[m] for m in noms}
    base = {m: int(exact[m]) for m in noms}
    reste = n - sum(base.values())
    for m in sorted(noms, key=lambda m: (-(exact[m] - base[m]), m))[:reste]:
        base[m] += 1
    return [m for m in noms for _ in range(base[m])]


def construire(cfg: dict, monde: Monde, modeles: dict, rng: random.Random):
    """Renvoie (déposants, employé, direction)."""
    pop = cfg["population"]
    llm_actif = cfg["llm"].get("actif", False)
    principaux_en_regles = cfg.get("personnages_principaux_en_regles", False) or not llm_actif
    catalogue = modeles["models"]

    def attribuer(agent, modele: str | None, tier: str | None):
        if modele is None:
            agent.type, agent.tier, agent.modele, agent.fournisseur = "regles", None, None, None
        else:
            agent.type, agent.tier, agent.modele = "llm", tier, modele
            agent.fournisseur = catalogue[modele]["provider"]

    deposants: list[Deposant] = []
    employe = direction = None
    for p in cfg["personnages_principaux"]:
        modele = None if principaux_en_regles else p["modele"]
        if p["role"] == "deposant":
            t = p["traits"]
            x, z = monde.tirer_domicile(rng, p["quartier"])
            a = Deposant(p["id"], p["nom"], "principal", float(p["depot"]), t["seuil"], t["loyaute"],
                         t["confiance"], t["sociabilite"], t["connecte"], p["quartier"], x, z,
                         principal=True, lieu_travail=p.get("lieu_travail"),
                         persona=p.get("persona") or p.get("justification"))
            attribuer(a, modele, "grand")
            deposants.append(a)
        else:
            inst = Institutionnel(p["id"], p["nom"], p["role"], lieu="bourg",
                                  persona=p.get("persona") or p.get("justification", ""))
            attribuer(inst, modele, "grand")
            if p["role"] == "employe":
                employe = inst
            else:
                direction = inst

    # Déposants ordinaires : profil tiré selon les parts (prudent complète)
    profils = cfg["profils"]
    n = pop["deposants"]
    parts = {"connecte": pop["part_connectes"], "fidele": profils["fidele"]["part"],
             "commercant": profils["commercant"]["part"]}
    ordinaires: list[Deposant] = []
    for i in range(n):
        r, cumul, profil = rng.random(), 0.0, "prudent"
        for nom in ("connecte", "fidele", "commercant"):
            cumul += parts[nom]
            if r < cumul:
                profil = nom
                break
        pr = profils[profil]
        q = monde.tirer_quartier(rng)
        x, z = monde.tirer_domicile(rng, q)
        depot = round(_tirer(rng, pr["depot"]) / 100.0) * 100.0
        ordinaires.append(Deposant(f"dep_{i:03d}", f"Déposant {i}", profil, depot, _tirer(rng, pr["seuil"]),
                                   pr["loyaute"], pr["confiance"], _tirer(rng, pr["sociabilite"]),
                                   profil == "connecte", q, x, z))

    # A6 : les moins exposés à l'information (sociabilité la plus faible) sont des agents à règles
    n_regles = round(n * pop["part_regles"])
    par_sociabilite = sorted(ordinaires, key=lambda a: (a.sociabilite, a.id))
    a_regles = {a.id for a in par_sociabilite[:n_regles]}
    for profil in ORDRE_PROFILS:
        groupe = [a for a in ordinaires if a.profil == profil and a.id not in a_regles]
        if llm_actif:
            places = _quotas(len(groupe), pop["repartition_petits"])
            rng.shuffle(places)
        else:
            places = [None] * len(groupe)
        for a, m in zip(groupe, places):
            attribuer(a, m, "petit")
    deposants.extend(ordinaires)
    return deposants, employe, direction


def attribution(deposants: list[Deposant], employe: Institutionnel, direction: Institutionnel,
                monde: Monde) -> list[dict]:
    """Table publiée dans run.started : elle fige le modèle de chaque agent pour tout le run."""
    lignes = [{"agent_id": a.id, "nom": a.nom, "role": "deposant", "type": a.type, "tier": a.tier,
               "modele": a.modele, "fournisseur": a.fournisseur, "profil": a.profil,
               "depot": a.depot, "connecte": a.connecte, "quartier": a.quartier,
               "domicile": {"x": round(a.x, 2), "z": round(a.z, 2)}} for a in deposants]
    for inst in (employe, direction):
        x, z = monde.position(inst.lieu)
        lignes.append({"agent_id": inst.id, "nom": inst.nom, "role": inst.role, "type": inst.type,
                       "tier": inst.tier, "modele": inst.modele, "fournisseur": inst.fournisseur,
                       "profil": inst.role, "depot": 0.0, "connecte": False, "quartier": None,
                       "domicile": {"x": round(x, 2), "z": round(z, 2)}})
    return lignes
