"""Agents de la vallée : déposants, employé d'agence, direction.

Croyances d'un déposant (reprises de guichet/scripts/depositor.gd) :
  peur          : croyance personnelle que la Caisse est en danger (0..1)
  estime_autres : estimation de la part des AUTRES qui vont retirer (0..1)
                  -> le raisonnement de second ordre du bank run.
"""
from dataclasses import dataclass, field

# États visibles dans Godot et dans state.snapshot
DOMICILE, EN_ROUTE, SUR_PLACE, VERS_AGENCE, EN_FILE, ATTENTE_OUVERTURE = (
    "domicile", "en_route", "sur_place", "vers_agence", "en_file", "attente_ouverture")


@dataclass
class Deposant:
    id: str
    nom: str
    profil: str
    depot: float
    seuil: float
    loyaute: float
    confiance: float
    sociabilite: float
    connecte: bool
    quartier: str
    x: float                      # domicile (mètres)
    z: float
    principal: bool = False
    lieu_travail: str | None = None
    persona: str | None = None    # personnages principaux : qui ils sont (prompt LLM)
    # attribution (figée pour tout le run)
    type: str = "regles"          # llm | regles
    tier: str | None = None
    modele: str | None = None
    fournisseur: str | None = None
    # croyances
    peur: float = 0.0
    estime_autres: float = 0.0
    informe: bool = False
    # situation
    etat: str = DOMICILE
    lieu: str = "domicile"
    cible: str | None = None
    t_arrivee: int = 0
    t_fin_visite: int = 0
    veut_retirer: bool = False
    agence: str | None = None
    retire: bool = False
    paye: float = 0.0             # total effectivement retiré au guichet
    garanti: float = 0.0          # total remboursé par la garantie des dépôts
    perdu: float = 0.0
    dernier_echange: int = -10 ** 9
    # dernière décision (inspection D2)
    intention: str = "rien"
    regle: str | None = None
    justification: str = ""
    # suivi des croyances publiées (granularité : on ne publie que les variations significatives)
    peur_publiee: float = 0.0
    estime_publiee: float = 0.0
    cause: str = "oubli"
    touche: bool = False          # une cause autre que l'oubli a modifié les croyances pendant ce pas
    causes_ids: list = field(default_factory=list)
    depot_initial: float = 0.0
    # agents LLM : carnet de ce que l'agent a appris (mémoire courte, daté), suivi des réflexions
    carnet: list = field(default_factory=list)      # [(t_sim, texte)], les 10 derniers
    n_notes: int = 0
    n_notes_vues: int = 0         # nombre de notes au moment de la dernière réflexion
    t_llm: int = -10 ** 9         # instant de la dernière réflexion
    peur_llm: float = 0.0         # croyances déclarées lors de la dernière réflexion
    estime_llm: float = 0.0
    phrase_llm: str | None = None # ce que l'agent dit à ses voisins, tiré de sa dernière réflexion
    call_phrase: str | None = None
    vu_files: dict = field(default_factory=dict)    # agence -> longueur de file notée dans le carnet
    reflexion: object = None      # mode asynchrone : (future, t_contexte, rappel) d'une réflexion en cours

    def __post_init__(self):
        self.depot_initial = self.depot

    def pression(self, couverture: float) -> float:
        """Pression à retirer, entre 0 et 1 (règle R-seuil, reprise de depositor.gd)."""
        exposition = 1.0 - 0.85 * couverture
        base = 0.4 * self.peur + 0.6 * self.estime_autres
        if self.estime_autres > 0.25:
            base += 0.15 * self.estime_autres   # peur d'être le dernier servi
        return min(max(base * exposition * (1.1 - 0.35 * self.loyaute), 0.0), 1.0)

    def modifier(self, d_peur: float = 0.0, d_estime: float = 0.0, cause: str = "", source: str | None = None):
        self.peur = min(max(self.peur + d_peur, 0.0), 1.0)
        self.estime_autres = min(max(self.estime_autres + d_estime, 0.0), 1.0)
        if cause:
            self.cause = cause
            self.touche = True
        if source:
            self.causes_ids = (self.causes_ids + [source])[-5:]

    def noter(self, t: int, texte: str) -> None:
        self.carnet = (self.carnet + [(t, texte)])[-10:]
        self.n_notes += 1

    def informer(self, force: float, cause: str, source: str | None) -> None:
        self.informe = True
        # Second ordre : « si moi je l'ai lu, les autres aussi ».
        self.modifier(force, force * 0.4, cause, source)


@dataclass
class Institutionnel:
    """Employé d'agence ou direction : ils ne déposent pas, ils agissent sur l'information."""
    id: str
    nom: str
    role: str                     # employe | direction
    lieu: str
    persona: str = ""
    type: str = "regles"
    tier: str | None = None
    modele: str | None = None
    fournisseur: str | None = None
    intention: str = "rien"
    regle: str | None = None
    justification: str = ""
