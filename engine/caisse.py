"""Comptabilité stricte de la Caisse Coopérative de Brévanne (portée depuis guichet/scripts/caisse.gd).

Actif : réserves (liquides) + prêts (illiquides). Passif : dépôts + fonds propres.
La Caisse fait défaut dès qu'un retrait dépasse ses réserves, même si elle reste solvable.
"""


class Caisse:
    def __init__(self, depots: float, ratio_reserves: float, ratio_fonds_propres: float, pertes_reelles: float):
        self.depots_initiaux = depots
        self.depots = depots
        self.fonds_propres_initiaux = depots * ratio_fonds_propres
        self.reserves = depots * ratio_reserves
        self.prets = depots + self.fonds_propres_initiaux - self.reserves
        self.pertes_reelles = pertes_reelles   # part des prêts perdue si l'emprunteur fait défaut
        self.perte_realisee = False
        self.retire_total = 0.0
        self.defaut = False

    def realiser_perte(self) -> None:
        """La difficulté de l'emprunteur devient réelle (panique « justifiée »)."""
        self.perte_realisee = True

    def valeur_prets(self) -> float:
        return self.prets * (1.0 - self.pertes_reelles) if self.perte_realisee else self.prets

    def marge(self) -> float:
        """Actif - dépôts. Négatif : la Caisse est insolvable."""
        return self.reserves + self.valeur_prets() - self.depots

    def solvable(self) -> bool:
        return self.marge() >= 0.0

    def retirer(self, montant: float) -> float:
        """Paie au plus ce qu'il y a en caisse. Si la demande dépasse les réserves : défaut."""
        if self.defaut or montant <= 0.0:
            return 0.0
        paye = min(montant, self.reserves)
        self.reserves -= paye
        self.depots -= paye
        self.retire_total += paye
        if paye < montant:
            self.defaut = True
        return paye

    def etat(self) -> dict:
        """Charge utile de caisse.updated."""
        return {"depots": round(max(self.depots, 0.0), 2), "reserves": round(self.reserves, 2),
                "valeur_prets": round(self.valeur_prets(), 2), "fonds_propres": round(self.marge(), 2),
                "marge_solvabilite": round(self.marge() / self.depots, 4) if self.depots > 0 else 0.0,
                "defaut": self.defaut}
