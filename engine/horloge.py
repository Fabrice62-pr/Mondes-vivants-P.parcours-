"""Horloge simulée explicite (C3). t_sim = minutes simulées écoulées depuis le début du run."""

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


class Horloge:
    def __init__(self, cfg_horloge: dict, cfg_routine: dict):
        self.pas = cfg_horloge["pas_min"]
        self._jour0 = JOURS.index(cfg_horloge["debut"]["jour"])
        self._min0 = minutes(cfg_horloge["debut"]["heure"])
        self.fin = cfg_horloge["duree_jours"] * 1440 - self._min0
        ag = cfg_horloge["agences"]
        self._jours_ouverts = set(ag["jours"])
        self._ouverture = minutes(ag["ouverture"])
        self._fermeture = minutes(ag["fermeture"])
        self._lever = minutes(cfg_routine["lever"])
        self._coucher = minutes(cfg_routine["coucher"])

    # --- conversions
    def t_de(self, jour: int, heure: str) -> int:
        """Instant simulé d'un rendez-vous (jour 1 = premier jour du run)."""
        return (jour - 1) * 1440 + minutes(heure) - self._min0

    def jour(self, t: int) -> int:
        return (self._min0 + t) // 1440 + 1

    def minute_du_jour(self, t: int) -> int:
        return (self._min0 + t) % 1440

    def jour_semaine(self, t: int) -> str:
        return JOURS[(self._jour0 + self.jour(t) - 1) % 7]

    def libelle(self, t: int) -> str:
        m = self.minute_du_jour(t)
        return f"J{self.jour(t)} {self.jour_semaine(t)} {m // 60:02d}:{m % 60:02d}"

    # --- état du monde selon l'heure
    def agences_ouvertes(self, t: int) -> bool:
        m = self.minute_du_jour(t)
        return self.jour_semaine(t) in self._jours_ouverts and self._ouverture <= m < self._fermeture

    def eveille(self, t: int) -> bool:
        m = self.minute_du_jour(t)
        return self._lever <= m < self._coucher

    def pas_eveilles_par_jour(self) -> int:
        return (self._coucher - self._lever) // self.pas

    def decrire(self, t: int) -> dict:
        m = self.minute_du_jour(t)
        return {"jour": self.jour(t), "jour_semaine": self.jour_semaine(t), "heure": m // 60,
                "minute": m % 60, "agences_ouvertes": self.agences_ouvertes(t)}
