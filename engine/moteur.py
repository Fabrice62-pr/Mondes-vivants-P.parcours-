"""Simulation GUICHET (C4).

Traitement hybride : un seul mécanisme pour tous les appels LLM (déposants, employé, direction). Les
requêtes d'un pas partent en parallèle, puis le pas attend leurs réponses **au plus `echeance_s` secondes
réelles** :
  - une réponse arrivée avant l'échéance s'applique dans ce pas (decision.made.voie = dans_le_pas) ;
  - une réponse en retard s'applique au pas où elle arrive, avec t_sim_contexte = l'instant où l'agent a
    commencé à réfléchir (voie = en_retard, C3). Un modèle rapide agit donc plus tôt qu'un modèle lent (H2).
Les deux voies coexistent dans un même run ; c'est la latence réelle qui décide. Les deux autres modes sont
des cas particuliers du même mécanisme :
  - synchrone  : échéance infinie, le pas attend toutes les réponses (barrière) ;
  - asynchrone : échéance nulle, le pas n'attend jamais ; chaque réponse s'applique à son arrivée.
En hybride et en asynchrone, l'horloge avance au rythme réel choisi (engine/run.py).

Ordre d'un pas (15 minutes simulées) :
  commandes -> article -> tic d'horloge -> arrivées -> routine, employé, conversations
  -> perception -> décisions -> guichets -> direction -> publication des variations et de l'état.

Chaque règle porte un identifiant (R-seuil, R-file, ...) repris dans decision.made.regle_id (D2).

Agents LLM (llm.actif) : un déposant LLM réfléchit quand quelque chose a changé pour lui (voir
_doit_reflechir) ; les requêtes d'un pas partent en un lot. L'employé d'agence répond au guichet du Bourg,
la direction décide des communiqués. En cas d'échec, l'agent décide avec sa règle
(decision.made.type = repli_regles) et un incident est publié.

Mémoire (rag.actif) : chaque chose qu'un agent LLM apprend devient un souvenir (memory.written). Avant de
décider, il se rappelle ses souvenirs les plus utiles, datés d'avant la décision, et les documents du
corpus de la vallée les plus proches ; decision.made les cite (A1, F1). Un élément du futur ou jamais
reçu produirait un temporal.violation (C6).
"""
import math
import random
import time
from concurrent.futures import Future, wait
from dataclasses import dataclass
from datetime import datetime

from . import textes
from .agents import (ATTENTE_OUVERTURE, DOMICILE, EN_FILE, EN_ROUTE, SUR_PLACE, VERS_AGENCE, Deposant,
                     Institutionnel)
from .caisse import Caisse
from llm import prompts
from llm.passerelle import Passerelle, Requete

from .config import ROOT, charger_modeles, charger_monde, echeance, empreinte
from .evenements import Bus, Fabrique, PuitsMemoire, Validateur
from .horloge import Horloge, minutes
from .indicateurs import icc
from .monde import Monde
from .population import attribution, construire

PARAMETRES_REQUIS = {"garantie": [], "plafond_retrait": ["valeur"], "fermer_agence": ["agence_id"],
                     "rouvrir_agence": ["agence_id"], "consigne_employe": ["consigne"],
                     "publier_communique": [], "injecter_rumeur": [], "pertes_reelles": ["valeur"],
                     "pause": [], "reprendre": [], "vitesse": ["rythme_s"]}


# « j'ai décidé ... » : texte des souvenirs de réflexion
DECIDE = {"retirer": "de retirer mon argent", "attendre": "d'attendre", "rien": "de ne rien changer",
          "publier_communique": "de publier un communiqué", "repondre": "de répondre", "eluder": "d'éluder"}


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


@dataclass
class Echange:
    """Question d'un client au guichet du Bourg, dont l'employé LLM n'a pas encore répondu."""
    future: Future
    t0: int
    client: Deposant
    agence: str
    eid: str                    # withdrawal.served qui précède la question
    question: str
    consigne: str
    rappel: tuple | None


class Simulation:
    def __init__(self, cfg: dict, graine: int | None = None, puits: list | None = None,
                 run_id: str | None = None, valider: bool = True, passerelle: Passerelle | None = None,
                 memoire=None):
        self.echeance = echeance(cfg)
        self._limite_pas = 0.0                       # instant réel (time.monotonic) où l'attente du pas s'arrête
        self.cfg = cfg
        self.graine = cfg["graine"] if graine is None else graine
        self.rng = random.Random(f"sim-{self.graine}")
        self.monde = Monde(charger_monde())
        self.horloge = Horloge(cfg["horloge"], cfg["routine"])
        self.R = cfg["regles"]
        self.routine = cfg["routine"]
        self.leviers = dict(cfg["leviers"])
        self.run_id = run_id or f"{cfg['nom']}-g{self.graine}-{datetime.now():%Y%m%d-%H%M%S}"
        self.bus = Bus(puits if puits is not None else [PuitsMemoire()], valider)
        self._validateur_entrant = self.bus.validateur or Validateur()
        self.fab = Fabrique(self.run_id, "moteur", self.graine)

        self.deposants, self.employe, self.direction = construire(
            cfg, self.monde, charger_modeles(), random.Random(f"population-{self.graine}"))
        self.par_id = {a.id: a for a in self.deposants}
        self.L = cfg["llm"]
        llm_agents = {a.id: a.modele for a in [*self.deposants, self.employe, self.direction] if a.type == "llm"}
        self.llm: Passerelle | None = None
        if llm_agents:
            if passerelle is None:
                passerelle = Passerelle(charger_modeles(), self.L, llm_agents,
                                        fichier_quota=ROOT / "data" / "logs" / "llm_quota.json")
            passerelle.attribution = dict(llm_agents)     # la passerelle n'accepte que ces modèles
            self.llm = passerelle
        self.memoire = None
        if self.llm is not None and cfg.get("rag", {}).get("actif", False):
            if memoire is None:
                from rag.memoire import Memoire          # import tardif : le modèle de plongement est lourd
                from rag.plongement import Plongement
                memoire = Memoire(Plongement(cfg["rag"].get("modele_plongement", "intfloat/multilingual-e5-small")),
                                  self.run_id, cfg=cfg["rag"])   # PostgreSQL : POSTGRES_URL (.env)
            self.memoire = memoire
        self._docs_publies: dict[str, int] = {}      # doc_id (article, communiqué) -> t_sim de publication
        self._doc_article: str | None = None
        self.n_rappels = 0
        self.violations = 0
        self._n_appels = 0
        self.replis = 0
        self.incidents = 0
        self.decisions_llm = 0
        self.voies = {"dans_le_pas": 0, "en_retard": 0}   # décisions LLM appliquées dans le pas ou en retard
        c = cfg["caisse"]
        self.caisse = Caisse(sum(a.depot for a in self.deposants), c["ratio_reserves"],
                             c["ratio_fonds_propres"], c["pertes_reelles"])
        self.files: dict[str, list[str]] = {ag: [] for ag in self.monde.agences}
        self.fermees: set[str] = set()

        self.t = 0
        art = cfg["horloge"]["article"]
        self.t_article = self.horloge.t_de(art["jour"], art["heure"])
        self.rumeur_publiee = False
        self.force_rumeur = cfg["rumeur"]["force"]
        self._evt_article: str | None = None
        self.n_articles = 0
        self.n_communiques = 0
        self.t_dernier_communique: int | None = None
        self.t_defaut: int | None = None
        self.fini = False
        self.pause = False
        self.rythme_s: float | None = None
        self.scenario = sorted((self.horloge.t_de(s["jour"], s["heure"]), i, s)
                               for i, s in enumerate(cfg.get("scenario") or []))
        self._ouvert_prec: bool | None = None
        self._files_publiees = {ag: (0, True) for ag in self.monde.agences}
        self._fuite_jour = -1
        self.serie_icc: list[tuple[int, dict]] = []
        self.file_max = 0
        self._basculement: str | None = None
        self.reserves_depart = self.caisse.reserves
        self._dernier_communique: str | None = None
        self._deliberation = None                      # (future, t_contexte, rappel) : la direction réfléchit
        self._echanges: list[Echange] = []             # questions au guichet en attente de la réponse de l'employé
        self._demarrer()

    # ================================================================ outils

    def _emettre(self, type_: str, payload: dict, causes=()) -> str:
        evt = self.fab.creer(type_, self.t, payload, causes)
        self.bus.publier(evt)
        return evt["event_id"]

    def _position(self, a: Deposant) -> tuple[float, float]:
        return (a.x, a.z) if a.lieu == "domicile" else self.monde.position(a.lieu)

    def couverture(self, depot: float) -> float:
        """Part du dépôt couverte par la garantie (réduit la pression à retirer)."""
        if not self.leviers["garantie"] or depot <= 0:
            return 0.0
        return min(max(self.leviers["plafond_garantie"] / depot, 0.0), 1.0)

    def agence_ouverte(self, ag: str, ouvert: bool) -> bool:
        return ouvert and ag not in self.fermees and not self.caisse.defaut

    def part_retiree(self) -> float:
        return self.caisse.retire_total / max(self.caisse.depots_initiaux, 1.0)

    def _deplacer(self, a: Deposant, vers: str, etat: str) -> None:
        dep = self._position(a)
        arr = (a.x, a.z) if vers == "domicile" else self.monde.position(vers)
        duree = max(1, math.ceil(Monde.distance(dep, arr) / self.routine["vitesse_m_par_min"]))
        de = a.lieu
        a.etat, a.cible, a.t_arrivee = etat, vers, self.t + duree
        self._emettre("agent.moved", {"agent_id": a.id, "de": de, "vers": vers, "t_sim_arrivee": a.t_arrivee})

    def _rentrer(self, a: Deposant) -> None:
        if a.lieu == "domicile" and a.etat in (DOMICILE, ATTENTE_OUVERTURE):
            return
        self._deplacer(a, "domicile", EN_ROUTE)

    def _decision(self, agent, action: str, regle: str | None, justification: str, causes=(),
                  call_id: str | None = None, t_contexte: int | None = None, rappel=None,
                  voie: str | None = None) -> str:
        """regle None = décision du LLM (call_id et voie renseignés) ; sinon une règle, qui pour un agent LLM
        est un repli."""
        agent.intention, agent.regle, agent.justification = action, regle, justification
        if regle is None:
            type_ = "llm"
            self.decisions_llm += 1
            self.voies[voie] += 1
        else:
            type_ = "regles" if agent.type == "regles" else "repli_regles"
        return self._emettre("decision.made", {
            "agent_id": agent.id, "type": type_, "action": action, "justification": justification,
            "regle_id": regle, "t_sim_contexte": self.t if t_contexte is None else t_contexte,
            "documents": self._documents_cites(rappel), "souvenirs": [s.memoire_id for s in rappel[0]] if rappel else [],
            "call_id": call_id, "voie": voie if regle is None else None}, causes)

    # ---------------------------------------------------------------- LLM

    def _requete(self, agent, usage: str, systeme: str, utilisateur: str, valider) -> Requete:
        self._n_appels += 1
        return Requete(f"c-{self._n_appels:06d}", agent.id, agent.modele, usage, systeme, utilisateur, valider,
                       float(self.L.get("temperature", 0.7)))

    def _publier_appels(self, res) -> None:
        """llm.called pour chaque essai, puis l'incident s'il y a repli (A4, E4)."""
        for appel in res.appels:
            self._emettre("llm.called", appel)
        if res.incident is not None:
            self.incidents += 1
            if res.incident["repli"] != "ignore":    # incident déjà signalé (clé absente, budget) : compté seulement
                self._emettre("incident.raised", res.incident)

    def _attendre(self, futures: list[Future]) -> bool:
        """Attente bornée par l'échéance du pas. True : les réponses arrivées s'appliquent dans ce pas ;
        False (asynchrone) : aucune ne s'applique dans le pas où elle est partie, même si elle est déjà là."""
        if self.echeance == 0:
            return False
        reste = None if self.echeance is None else max(0.0, self._limite_pas - time.monotonic())
        wait(futures, timeout=reste)
        return True

    # ---------------------------------------------------------------- mémoire (A1, F1, C6)

    def _noter(self, a: Deposant, texte: str, nature: str, importance: float, sources=(),
               doc_id: str | None = None) -> None:
        """L'agent apprend quelque chose : son carnet (mémoire courte) et, s'il est LLM, un souvenir."""
        a.noter(self.t, texte)
        if self.memoire is not None and a.type == "llm":
            self._souvenir(a, texte, nature, importance, sources, doc_id)

    def _souvenir(self, agent, texte: str, nature: str, importance: float, sources=(), doc_id=None) -> str:
        mid = f"m-{agent.id}-{self.memoire.n_souvenirs + len(self.memoire._en_attente):05d}"
        self.memoire.ecrire(mid, agent.id, texte, self.t, importance, doc_id, nature)
        self._emettre("memory.written", {"agent_id": agent.id, "memoire_id": mid, "nature": nature, "texte": texte,
                                         "importance": round(importance, 2), "sources": [s for s in sources if s]})
        return mid

    def _rappeler(self, agents: list, situations: list[str]) -> list:
        """(souvenirs, documents) de chaque agent, vérifiés contre C6 ; (None) sans mémoire."""
        if self.memoire is None:
            return [None] * len(agents)
        rappels = self.memoire.rappeler([(a.id, s, self.t) for a, s in zip(agents, situations)])
        self.n_rappels += len(agents)
        return rappels

    def _verifier_c6(self, agent, rappel, did: str, t_contexte: int | None = None) -> None:
        """Garde-fou : aucun souvenir postérieur au contexte de la décision, aucun document publié pendant le
        run que l'agent n'a pas reçu."""
        if not rappel:
            return
        t_ctx = self.t if t_contexte is None else t_contexte
        recus = {s.doc_id for s in rappel[0] if s.doc_id}
        for s in rappel[0]:
            if s.t_sim > t_ctx:
                self._violation(agent, did, s.memoire_id, s.t_sim, "futur", t_ctx)
        for d in rappel[1]:
            t_doc = self._docs_publies.get(d.doc_id)
            if t_doc is not None and (t_doc > t_ctx or d.doc_id not in recus):
                self._violation(agent, did, d.doc_id, t_doc, "futur" if t_doc > t_ctx else "non_recu", t_ctx)

    def _violation(self, agent, did: str, element: str, t_el: int, raison: str, t_ctx: int) -> None:
        self.violations += 1
        self._emettre("temporal.violation", {"agent_id": agent.id, "decision_event_id": did, "element_id": element,
                                             "t_sim_element": int(t_el), "t_sim_contexte": int(t_ctx),
                                             "raison": raison})

    def _documents_cites(self, rappel) -> list[dict]:
        if not rappel:
            return []
        docs = [{"doc_id": d.doc_id, "score": d.score, "t_sim_doc": None} for d in rappel[1]]
        for s in rappel[0]:                          # souvenir né d'un article ou d'un communiqué reçu
            if s.doc_id and s.doc_id not in {d["doc_id"] for d in docs}:
                docs.append({"doc_id": s.doc_id, "score": s.score, "t_sim_doc": self._docs_publies.get(s.doc_id)})
        return docs

    def _textes_rappel(self, rappel) -> list[tuple[str, str]]:
        if not rappel:
            return []
        return ([(self.horloge.libelle(s.t_sim), s.texte) for s in rappel[0]]
                + [(f"document « {d.titre} »", d.texte) for d in rappel[1]])

    @staticmethod
    def _situation(agent) -> str:
        """Requête de rappel : ce que l'agent vient de vivre. Une question fixe (« faut-il retirer ? »)
        ramenait toujours le même document du corpus, quelle que soit la situation."""
        recent = " ; ".join(txt for _, txt in agent.carnet[-3:])
        return recent or "la Caisse Coopérative de Brévanne"

    def nom_public(self, a) -> str:
        """Comment un habitant désigne un autre dans son carnet : les personnages sont connus de tous."""
        return a.nom if getattr(a, "principal", False) or isinstance(a, Institutionnel) else "un habitant"

    def l_agence(self, ag: str) -> str:
        """« l'Agence du Bourg » : le nom d'une agence précédé de son article, pour les carnets."""
        return "l'" + self.nom_lieu(ag)

    def nom_lieu(self, lieu: str) -> str:
        return self.monde.lieux[lieu].nom if lieu in self.monde.lieux else lieu

    # ================================================================ début et fin

    def _demarrer(self) -> None:
        h = self.cfg["horloge"]
        self._emettre("run.started", {
            "graine": self.graine, "mode": self.cfg["mode"], "echeance_s": self.echeance, "config_nom": self.cfg["nom"],
            "config_hash": empreinte(self.cfg), "population": len(self.deposants),
            "horloge": {"pas_min": h["pas_min"], "debut_jour": h["debut"]["jour"],
                        "duree_jours": h["duree_jours"], "t_sim_article": self.t_article},
            "attribution": attribution(self.deposants, self.employe, self.direction, self.monde)})
        self._emettre("caisse.updated", self.caisse.etat())

    def _terminer(self, raison: str) -> None:
        # Réponses arrivées après le dernier pas : leur coût est compté, elles ne s'appliquent plus.
        if self._deliberation is not None and self._deliberation[0].done():
            self._publier_appels(self._deliberation[0].result())
            self._deliberation = None
        for a in self.deposants:
            if a.reflexion is not None and a.reflexion[0].done():
                self._publier_appels(a.reflexion[0].result())
            a.reflexion = None
        for e in self._echanges:
            if e.future.done():
                self._publier_appels(e.future.result())
        self._echanges = []
        self._emettre("run.ended", {"raison": raison, "resume": self.resume()})
        self.fini = True
        if self.llm is not None:
            self.llm.fermer()
        if self.memoire is not None:
            self.memoire.fermer()

    def resume(self) -> dict:
        valeurs = [v["icc"] for _, v in self.serie_icc]
        seuil, duree = 0.6, max(1, 240 // self.horloge.pas)   # critère : ICC >= 0,6 pendant 4 h
        serie, t_emergence = 0, None
        for t, v in self.serie_icc:
            serie = serie + 1 if v["icc"] >= seuil else 0
            if serie >= duree and t_emergence is None:
                t_emergence = t - (duree - 1) * self.horloge.pas
        return {
            "fin": self.horloge.libelle(self.t), "t_fin": self.t,
            "defaut": self.t_defaut is not None, "t_defaut": self.t_defaut,
            "defaut_libelle": self.horloge.libelle(self.t_defaut) if self.t_defaut is not None else None,
            "solvable": self.caisse.solvable(), "part_retiree": round(self.part_retiree(), 4),
            "retraits": self.bus.compteur["withdrawal.served"],
            "deposants_perdants": sum(1 for a in self.deposants if a.perdu > 0),
            "perte_totale": round(sum(a.perdu for a in self.deposants), 2),
            "garanti_total": round(sum(a.garanti for a in self.deposants), 2),
            "file_max": self.file_max, "icc_max": max(valeurs, default=0.0),
            "emergence": t_emergence is not None, "t_emergence": t_emergence,
            "conversations": self.bus.compteur["conversation.held"],
            "communiques": self.n_communiques,
            "evenements": sum(self.bus.compteur.values()), "par_type": dict(sorted(self.bus.compteur.items())),
            "rejets_dlq": self.bus.rejets,
            "llm": None if self.llm is None else {**self.llm.bilan(), "decisions_llm": self.decisions_llm,
                                                  "voies": dict(self.voies), "replis": self.replis,
                                                  "incidents": self.incidents},
            "rag": None if self.memoire is None else {"souvenirs": self.bus.compteur["memory.written"],
                                                      "rappels": self.n_rappels, "violations_c6": self.violations},
        }

    # ================================================================ boucle

    def pas(self) -> None:
        if self.fini:
            return
        t = self.t
        self._limite_pas = time.monotonic() + (self.echeance or 0.0)
        # L'horloge d'abord : les consommateurs (Godot) datent les événements du pas avec ce tick.
        self._emettre("clock.tick", self.horloge.decrire(t))
        self.traiter_commandes_externes()
        while self.scenario and self.scenario[0][0] <= t:
            _, _, s = self.scenario.pop(0)
            self.appliquer_commande(s["commande"], s.get("parametres") or {}, source="scenario")
        if not self.rumeur_publiee and t >= self.t_article:
            self._publier_article(self.force_rumeur, "presse")

        ouvert = self.horloge.agences_ouvertes(t)
        if self._ouvert_prec and not ouvert:
            self._fermeture_du_soir()
        self._ouvert_prec = ouvert
        self._arrivees(ouvert)
        eveille = self.horloge.eveille(t)
        if eveille:
            if not self.caisse.defaut:
                self._routine()
                self._employe()
            self._conversations()
        else:
            self._coucher()
        if not self.caisse.defaut:
            self._percevoir()
            self._decider(ouvert)
            if ouvert:
                self._servir()
            self._direction()
        self._fin_de_pas(ouvert)

        self.t += self.horloge.pas
        if self.t >= self.horloge.fin:
            self._terminer("fin_horloge")
        elif self.t_defaut is not None and self.t >= self.t_defaut + 60 * self.cfg["guichet"]["arret_apres_defaut_h"]:
            self._terminer("defaut")

    def executer(self) -> dict:
        while not self.fini:
            self.pas()
        return self.resume()

    # ================================================================ déplacements et routine

    def _arrivees(self, ouvert: bool) -> None:
        for a in self.deposants:
            if a.etat not in (EN_ROUTE, VERS_AGENCE) or a.t_arrivee > self.t:
                continue
            a.lieu = a.cible
            if a.etat == VERS_AGENCE:
                if self.agence_ouverte(a.cible, ouvert):
                    self.files[a.cible].append(a.id)
                    a.etat, a.agence = EN_FILE, a.cible
                else:                       # agence fermée entre-temps : on rentre, l'intention demeure
                    a.agence = None
                    self._rentrer(a)
            elif a.lieu == "domicile":
                a.etat = ATTENTE_OUVERTURE if a.veut_retirer else DOMICILE
            else:
                a.etat = SUR_PLACE
                if a.lieu == a.lieu_travail:
                    reste = minutes(self.routine["coucher"]) - 120 - self.horloge.minute_du_jour(self.t)
                    a.t_fin_visite = self.t + max(self.horloge.pas, reste)
                else:
                    a.t_fin_visite = self.t + self.rng.randint(*self.routine["duree_sortie_min"])

    def _choisir_lieu(self, a: Deposant) -> str:
        poids = [self.monde.lieux[l].poids / (1.0 + Monde.distance((a.x, a.z), self.monde.position(l)) / 80.0)
                 for l in self.monde.rencontres]
        return self.rng.choices(self.monde.rencontres, weights=poids)[0]

    def _routine(self) -> None:
        r = self.routine
        par_pas = 1.0 / max(self.horloge.pas_eveilles_par_jour(), 1)
        limite_travail = minutes(r["coucher"]) - 180
        for a in self.deposants:
            if a.etat == DOMICILE:
                if a.lieu_travail and self.horloge.minute_du_jour(self.t) < limite_travail:
                    self._deplacer(a, a.lieu_travail, EN_ROUTE)
                    continue
                sorties = r["sorties_min_par_jour"] + a.sociabilite * (r["sorties_max_par_jour"] - r["sorties_min_par_jour"])
                if self.rng.random() < sorties * par_pas:
                    self._deplacer(a, self._choisir_lieu(a), EN_ROUTE)
            elif a.etat == SUR_PLACE and self.t >= a.t_fin_visite:
                self._rentrer(a)

    def _coucher(self) -> None:
        for a in self.deposants:
            if a.etat == SUR_PLACE:
                self._rentrer(a)

    def _fermeture_du_soir(self) -> None:
        """À la fermeture, les files se dispersent ; chacun garde l'intention de revenir à l'ouverture."""
        for ag, file in self.files.items():
            for aid in file:
                a = self.par_id[aid]
                a.agence = None
                self._rentrer(a)
            file.clear()

    def _aller_agence(self, a: Deposant, ouvert: bool) -> None:
        pos = self._position(a)
        candidates = [ag for ag in self.monde.agences if self.agence_ouverte(ag, ouvert)]
        if not candidates:
            return
        ag = min(candidates, key=lambda g: (Monde.distance(pos, self.monde.position(g)), g))
        a.agence = ag
        self._deplacer(a, ag, VERS_AGENCE)

    # ================================================================ information

    def _publier_article(self, force: float, source: str, causes=()) -> None:
        self.rumeur_publiee = True
        self.n_articles += 1
        if self.caisse.pertes_reelles > 0 and not self.caisse.perte_realisee:
            self.caisse.realiser_perte()
            self._emettre("caisse.updated", self.caisse.etat())
        art = self.cfg["article"]
        doc = f"article-{self.n_articles:03d}"
        eid = self._emettre("article.published", {"titre": art["titre"], "texte": art["texte"],
                                                  "force": round(force, 3), "doc_id": doc, "source": source}, causes)
        self._evt_article, self._doc_article = eid, doc
        self._docs_publies[doc] = self.t
        for a in self.deposants:
            if a.connecte or (a.etat == SUR_PLACE and a.lieu == "presse"):
                a.informer(force * self.rng.uniform(0.7, 1.2), "article", eid)
                self._noter(a, self._texte_article(), "observation", 0.9, [eid], doc)

    def _texte_article(self) -> str:
        art = self.cfg["article"]
        return f"tu as lu dans la presse : « {art['titre']} » {art['texte']}"

    def publier_communique(self, ton: str, auteur: str, causes=(), texte: str | None = None) -> None:
        n = max(len(self.deposants), 1)
        en_file = sum(len(f) for f in self.files.values())
        credibilite = min(max(1.0 - 2.0 * (self.part_retiree() + en_file / n), 0.05), 1.0)
        self.n_communiques += 1
        texte = texte or textes.COMMUNIQUES[ton]
        doc = f"communique-{self.n_communiques:03d}"
        cid = self._emettre("communique.published", {
            "auteur": auteur, "ton": ton, "texte": texte, "credibilite": round(credibilite, 3),
            "doc_id": doc}, causes)
        self._docs_publies[doc] = self.t
        self.t_dernier_communique = self.t
        self._dernier_communique = f"{self.horloge.libelle(self.t)}, ton {ton} : « {texte} »"
        for a in self.deposants:
            if a.retire or (not a.connecte and self.rng.random() > 0.4):
                continue
            r = self.rng.uniform(0.6, 1.4)          # chacun ne réagit pas pareil
            a.informe = True                         # un communiqué fait aussi connaître l'affaire
            self._noter(a, f"la direction de la Caisse a publié un communiqué : « {texte} »", "observation", 0.8,
                        [cid], doc)
            if ton == "rassurant":
                if credibilite > 0.45:
                    a.modifier(-0.3 * credibilite * a.confiance * r, 0, "communique", cid)
                else:                                # « s'ils rassurent, c'est que ça va mal »
                    a.modifier(0.12 * r, 0, "communique", cid)
            elif ton == "factuel":
                a.modifier(-0.25 * credibilite * r * (0.5 + 0.5 * a.confiance), -0.1 * credibilite * r, "communique", cid)
            else:
                a.modifier(0.08 * r * (1.0 - a.confiance), 0, "communique", cid)

    def _conversations(self) -> None:
        groupes: dict[str, list[Deposant]] = {}
        for a in self.deposants:
            if a.etat in (SUR_PLACE, EN_FILE):
                groupes.setdefault(a.lieu, []).append(a)
        intervalle = self.R["conversation_intervalle_min"]
        for lieu in sorted(groupes):
            g = [a for a in groupes[lieu] if self.t - a.dernier_echange >= intervalle]
            self.rng.shuffle(g)
            for i in range(0, len(g) - 1, 2):
                self._converser(g[i], g[i + 1], lieu)

    def _converser(self, a: Deposant, b: Deposant, lieu: str) -> None:
        pa, pb = a.pression(self.couverture(a.depot)), b.pression(self.couverture(b.depot))
        loc, aud, p_loc = (a, b, pa) if pa >= pb else (b, a, pb)
        if not loc.informe and not aud.informe:
            return
        a.dernier_echange = b.dernier_echange = self.t
        R = self.R
        # R-conversation : les mauvaises nouvelles se propagent mieux que les bonnes.
        attraction = R["conversation_attraction"] * (1.0 - aud.loyaute * 0.5) * self.rng.uniform(0.7, 1.3)
        peur = _lerp(aud.peur, loc.peur, attraction if loc.peur > aud.peur else attraction * 0.5)
        estime = _lerp(aud.estime_autres, max(aud.estime_autres, loc.estime_autres), 0.3)
        estime += R["conversation_estime"] * loc.peur   # voir quelqu'un d'inquiet, c'est apprendre que d'autres pourraient retirer
        phrase = textes.phrase_pour(loc, p_loc, self.rng)
        mode, call_id = "regles", None
        if loc.type == "llm" and loc.phrase_llm:     # ce que l'agent a dit vouloir dire lors de sa réflexion
            phrase, mode, call_id = loc.phrase_llm, "llm", loc.call_phrase
        avant = (aud.peur, aud.estime_autres)
        aud.peur, aud.estime_autres = min(max(peur, 0.0), 1.0), min(max(estime, 0.0), 1.0)
        reponse = textes.reaction_pour(aud.pression(self.couverture(aud.depot)), aud.seuil)
        aud.peur, aud.estime_autres = avant
        eid = self._emettre("conversation.held", {"lieu": lieu, "locuteur_id": loc.id, "auditeur_id": aud.id,
                                                  "phrase": phrase, "reponse": reponse, "mode": mode,
                                                  "call_id": call_id})
        self._noter(aud, f"{self.nom_lieu(lieu)}, {self.nom_public(loc)} t'a dit : « {phrase} »", "conversation",
                    0.5, [eid])
        if loc.informe:
            aud.informe = True
        aud.modifier(min(max(peur, 0.0), 1.0) - aud.peur, min(max(estime, 0.0), 1.0) - aud.estime_autres,
                     "conversation", eid)

    def _percevoir(self) -> None:
        R = self.R
        monte, descend = R["apprentissage_file"]
        presents: dict[str, list[Deposant]] = {}
        for a in self.deposants:
            if a.etat in (SUR_PLACE, EN_FILE):
                presents.setdefault(a.lieu, []).append(a)
        for a in self.deposants:
            if a.etat in (SUR_PLACE, EN_FILE):
                pos = self._position(a)
                # R-file : une file qui s'allonge inquiète vite ; une agence calme rassure lentement.
                for ag in self.monde.agences:
                    if Monde.distance(pos, self.monde.position(ag)) < R["visibilite_file_m"]:
                        n_file = len(self.files[ag])
                        note = a.vu_files.get(ag, 0)
                        if n_file >= 3 and abs(n_file - note) >= 3:
                            self._noter(a, f"tu as vu {n_file} personnes faire la queue devant {self.l_agence(ag)}",
                                        "observation", min(0.4 + n_file / 20.0, 0.9))
                            a.vu_files[ag] = n_file
                        elif n_file == 0 and note >= 3:
                            self._noter(a, f"il n'y a plus de file devant {self.l_agence(ag)}", "observation", 0.4)
                            a.vu_files[ag] = 0
                        vu = min(n_file / R["file_saturee"], 1.0)
                        cible = _lerp(a.estime_autres, vu, monte if vu > a.estime_autres else descend)
                        if abs(cible - a.estime_autres) > 1e-9:
                            a.modifier(0, cible - a.estime_autres, "file_observee")
                # R-ambiance : l'humeur des personnes présentes
                autres = [o for o in presents[a.lieu] if o is not a]
                if len(autres) >= 2:
                    anxieux = sum(2 if (o.etat == EN_FILE or o.retire or o.veut_retirer) else 1 if o.peur > 0.35 else 0
                                  for o in autres)
                    humeur = anxieux / (len(autres) * 2.0)
                    if humeur > a.estime_autres:
                        a.modifier(0, (humeur - a.estime_autres) * R["ambiance_poids"], "ambiance")
                if a.lieu == "presse" and self.rumeur_publiee and not a.informe:
                    a.informer(self.force_rumeur * 0.6, "article", self._evt_article)
                    self._noter(a, self._texte_article(), "observation", 0.9, [self._evt_article], self._doc_article)
            # R-oubli : l'inquiétude retombe lentement si rien ne l'entretient
            if not a.touche:
                a.cause = "oubli"
            a.peur = max(0.0, a.peur - R["oubli_peur_par_pas"])
            a.estime_autres = max(0.0, a.estime_autres - R["oubli_estime_par_pas"])

    # ================================================================ décisions

    def _decider(self, ouvert: bool) -> None:
        self._recevoir_echanges()                    # réponses en retard des pas précédents, dans l'ordre d'envoi
        self._recevoir_reflexions(ouvert)
        reflexions = []
        for a in self.deposants:
            if a.retire or a.etat in (VERS_AGENCE, EN_FILE):
                continue
            if a.veut_retirer:
                if ouvert and a.etat in (DOMICILE, ATTENTE_OUVERTURE, SUR_PLACE):
                    self._aller_agence(a, ouvert)
                continue
            if a.type == "llm" and self.llm is not None:
                if self._doit_reflechir(a):
                    reflexions.append(a)
                continue                             # entre deux réflexions, il garde sa dernière décision
            self._decider_regle(a, ouvert)
        if reflexions:
            self._decider_llm(reflexions, ouvert)

    def _decider_regle(self, a: Deposant, ouvert: bool, repli: str | None = None) -> None:
        cov = self.couverture(a.depot)
        p = a.pression(cov)
        if p > a.seuil:
            action, regle, cmp = "retirer", "R-seuil", ">"
        elif p > 0.6 * a.seuil:
            action, regle, cmp = "attendre", "R-vigilance", ">"
        else:
            action, regle, cmp = "rien", "R-calme", "≤"
        ref = a.seuil if action != "attendre" else 0.6 * a.seuil
        just = (f"pression {p:.2f} {cmp} {'seuil' if action != 'attendre' else '60 % du seuil'} {ref:.2f} "
                f"(peur {a.peur:.2f}, estime que les autres retirent {a.estime_autres:.2f}, "
                f"couverture par la garantie {cov:.0%})")
        if repli:
            just = f"repli sur la règle après un incident LLM ({repli}) : {just}"
        self._appliquer(a, action, regle, just, ouvert, forcer=repli is not None)

    def _appliquer(self, a: Deposant, action: str, regle: str | None, just: str, ouvert: bool,
                   call_id: str | None = None, forcer: bool = False, rappel=None,
                   t_contexte: int | None = None, voie: str | None = None) -> str | None:
        did = None
        if action != a.intention or forcer:
            did = self._decision(a, action, regle, just, a.causes_ids, call_id, t_contexte, rappel, voie)
        if action == "retirer":
            a.veut_retirer = True
            if ouvert:
                self._aller_agence(a, ouvert)
            elif a.etat == DOMICILE:
                a.etat = ATTENTE_OUVERTURE
        return did

    def _doit_reflechir(self, a: Deposant) -> bool:
        if a.reflexion is not None:                  # sa réponse précédente n'est pas encore arrivée
            return False
        if not self.horloge.eveille(self.t) or self.t - a.t_llm < self.L.get("intervalle_decision_min", 60):
            return False
        s = self.L.get("seuil_reflexion", 0.10)
        return (a.n_notes > a.n_notes_vues or abs(a.peur - a.peur_llm) >= s
                or abs(a.estime_autres - a.estime_llm) >= s)

    def _decider_llm(self, agents: list[Deposant], ouvert: bool) -> None:
        requetes, rappels = self._requetes_reflexion(agents)
        for a, rq, rappel in zip(agents, requetes, rappels):
            a.reflexion = (self.llm.soumettre(rq), self.t, rappel)
            a.t_llm, a.n_notes_vues = self.t, a.n_notes
        if not self._attendre([a.reflexion[0] for a in agents]):
            return
        for a in agents:                             # arrivées avant l'échéance : appliquées dans ce pas
            if a.reflexion[0].done():
                future, t0, rappel = a.reflexion
                a.reflexion = None
                self._appliquer_reflexion(a, rappel, future.result(), ouvert, t0, "dans_le_pas")

    def _recevoir_reflexions(self, ouvert: bool) -> None:
        """Réflexions en retard dont la réponse est arrivée : appliquées dans l'ordre des agents."""
        for a in self.deposants:
            if a.reflexion is None or not a.reflexion[0].done():
                continue
            future, t0, rappel = a.reflexion
            a.reflexion = None
            res = future.result()
            if a.retire or a.veut_retirer or a.etat in (VERS_AGENCE, EN_FILE):
                self._publier_appels(res)            # la situation a changé entre-temps : coût compté, rien d'autre
                continue
            self._appliquer_reflexion(a, rappel, res, ouvert, t0, "en_retard")

    def _requetes_reflexion(self, agents: list[Deposant]) -> tuple[list, list]:
        moment = self.horloge.libelle(self.t)
        rappels = self._rappeler(agents, [self._situation(a) for a in agents])
        requetes = []
        for a, rappel in zip(agents, rappels):
            recents = {txt for _, txt in a.carnet}
            souvenirs = [(self.horloge.libelle(s.t_sim), s.texte) for s in (rappel[0] if rappel else [])
                         if s.texte not in recents]           # pas de doublon avec le carnet
            documents = [(d.titre, d.texte) for d in (rappel[1] if rappel else [])]
            requetes.append(self._requete(
                a, "decision",
                prompts.systeme_deposant(a.nom if a.principal else None, a.profil, a.persona, a.depot),
                prompts.utilisateur_deposant(moment, self.horloge.agences_ouvertes(self.t), self.leviers["garantie"],
                                             self.leviers["plafond_garantie"], self.leviers["plafond_retrait"],
                                             [(self.horloge.libelle(t), txt) for t, txt in a.carnet],
                                             a.peur, a.estime_autres, souvenirs, documents),
                prompts.valider_deposant))
        return requetes, rappels

    def _appliquer_reflexion(self, a: Deposant, rappel, res, ouvert: bool, t0: int, voie: str) -> None:
        self._publier_appels(res)
        if not res.ok:
            self.replis += 1
            a.peur_llm, a.estime_llm = a.peur, a.estime_autres
            self._decider_regle(a, ouvert, repli=res.incident["nature"])
            return
        d = res.donnees
        # Les croyances d'un agent LLM sont celles qu'il déclare (docs/contexte_commun.md).
        a.modifier(d["peur"] - a.peur, d["estime_autres"] - a.estime_autres, "reflexion")
        a.peur_llm, a.estime_llm = a.peur, a.estime_autres
        a.phrase_llm, a.call_phrase = d["phrase"], res.requete.call_id
        did = self._appliquer(a, d["action"], None, d["justification"], ouvert, res.requete.call_id,
                              forcer=True, rappel=rappel, t_contexte=t0, voie=voie)
        self._verifier_c6(a, rappel, did, t0)
        if self.memoire is not None:                 # sa propre réflexion devient un souvenir
            self._souvenir(a, f"j'ai décidé {DECIDE[d['action']]} : {d['justification']}", "reflexion", 0.3, [did])

    def _servir(self) -> None:
        capacite = self.horloge.pas // self.cfg["guichet"]["service_min"]
        for ag in self.monde.agences:
            if ag in self.fermees:
                continue
            for _ in range(capacite):
                if not self.files[ag] or self.caisse.defaut:
                    break
                self._servir_client(self.par_id[self.files[ag].pop(0)], ag)

    def _servir_client(self, a: Deposant, ag: str) -> None:
        plafond = self.leviers["plafond_retrait"]
        demande = a.depot * plafond
        if plafond < 1.0:
            demande = max(demande, min(a.depot, 500.0))
        paye = self.caisse.retirer(demande)
        garanti = perdu = 0.0
        defaut_maintenant = self.caisse.defaut and self.t_defaut is None
        if defaut_maintenant:
            # Le client servi au moment du défaut : on solde son compte UNE fois (corrige la double
            # perte de la maquette Godot, où finalize_default et on_served comptaient la perte deux fois).
            reste = a.depot - paye
            garanti = min(reste, self.leviers["plafond_garantie"]) if self.leviers["garantie"] else 0.0
            perdu = reste - garanti
            a.depot = 0.0
        else:
            a.depot = max(0.0, a.depot - paye)
        a.paye += paye
        a.garanti += garanti
        a.perdu += perdu
        eid = self._emettre("withdrawal.served", {"agent_id": a.id, "agence_id": ag, "demande": round(demande, 2),
                                                  "paye": round(paye, 2), "perdu": round(perdu, 2),
                                                  "garanti": round(garanti, 2)})
        self._emettre("caisse.updated", self.caisse.etat(), [eid])
        a.veut_retirer, a.agence = False, None
        a.phrase_llm = None                          # son intention est accomplie : il parlera de son retrait
        if a.depot <= 0.01:
            a.retire = True
        else:                                        # retrait plafonné : il reviendra, et la limite l'inquiète
            a.modifier(0.1, 0, "guichet", eid)
            self._noter(a, f"au guichet de {self.l_agence(ag)}, tu n'as pu retirer que {prompts.montant(paye)} ; "
                           f"il te reste {prompts.montant(a.depot)} à la Caisse", "guichet", 0.8, [eid])
        self._echange_guichet(a, ag, eid)
        self._rentrer(a)
        if defaut_maintenant:
            self._defaut(eid, a)

    def _echange_guichet(self, a: Deposant, ag: str, eid: str) -> None:
        R = self.R
        if self.rng.random() >= R["question_guichet"]:
            return
        consigne = self.leviers["consigne_employe"]
        question = self.rng.choice(textes.QUESTIONS)
        if not (ag == self.employe.lieu and self.employe.type == "llm" and self.llm is not None):
            self._repondre_au_guichet(a, ag, eid, question, consigne)
            return
        rappel = self._rappeler([self.employe], [f"Un client demande : {question}"])[0]
        rq = self._requete(
            self.employe, "guichet", prompts.systeme_employe(self.employe.nom, self.employe.persona),
            prompts.utilisateur_employe(self.horloge.libelle(self.t), consigne, self.caisse.solvable(),
                                        self.part_retiree(), len(self.files[ag]), question,
                                        self._textes_rappel(rappel)),
            prompts.valider_employe)
        e = Echange(self.llm.soumettre(rq), self.t, a, ag, eid, question, consigne, rappel)
        if self._attendre([e.future]) and e.future.done():
            self._recevoir_echange(e, "dans_le_pas")
        else:
            self._echanges.append(e)

    def _recevoir_echanges(self) -> None:
        """Réponses en retard de l'employé : appliquées dans l'ordre des questions."""
        restants = []
        for e in self._echanges:
            if e.future.done():
                self._recevoir_echange(e, "en_retard")
            else:
                restants.append(e)
        self._echanges = restants

    def _recevoir_echange(self, e: Echange, voie: str) -> None:
        llm = e.future.result()
        self._publier_appels(llm)
        if not llm.ok:
            self.replis += 1
        self._repondre_au_guichet(e.client, e.agence, e.eid, e.question, e.consigne, llm, e.rappel, e.t0, voie)

    def _repondre_au_guichet(self, a: Deposant, ag: str, eid: str, question: str, consigne: str, llm=None,
                             rappel=None, t0: int | None = None, voie: str | None = None) -> None:
        """La réponse de l'employé : celle du LLM s'il a répondu, sinon celle de la règle (consigne)."""
        R = self.R
        if llm is not None and llm.ok:
            elude, reponse = llm.donnees["action"] == "eluder", llm.donnees["reponse"]
        else:
            elude = consigne == "silence" and self.rng.random() < R["employe_elude_si_silence"]
            if elude:
                reponse = self.rng.choice(textes.REPONSES_ELUDEES)
            elif self.caisse.solvable():
                reponse = self.rng.choice(textes.REPONSES_FRANCHES_SOLVABLE)
            else:
                reponse = self.rng.choice(textes.REPONSES_FRANCHES_INSOLVABLE)
        call_id = llm.requete.call_id if llm is not None and llm.ok else None
        cid = self._emettre("counter.exchanged", {"agence_id": ag, "client_id": a.id, "question": question,
                                                  "reponse": reponse, "elude": elude, "consigne": consigne,
                                                  "call_id": call_id}, [eid])
        self._noter(a, f"au guichet de {self.l_agence(ag)}, tu as demandé « {question} » ; "
                       f"l'employé a répondu : « {reponse} »", "guichet", 0.7, [cid])
        if ag == self.employe.lieu:                 # le personnage « employé d'agence » tient le guichet du Bourg
            if call_id is not None:
                did = self._decision(self.employe, "eluder" if elude else "repondre", None,
                                     llm.donnees["justification"], [cid], call_id, t0, rappel, voie)
                self._verifier_c6(self.employe, rappel, did, t0)
                if self.memoire is not None:
                    self._souvenir(self.employe, f"un client m'a demandé « {question} » ; j'ai répondu « {reponse} »",
                                   "guichet", 0.5, [cid])
            else:
                repli = f"repli sur la règle après un incident LLM ({llm.incident['nature']}) : " if llm else ""
                self._decision(self.employe, "eluder" if elude else "repondre", "R-consigne",
                               f"{repli}consigne de la direction : « {consigne} »", [cid])
        if elude:                                    # une question éludée inquiète (souvenir clé pour A1)
            a.modifier(0.08, 0.08, "guichet", cid)
        else:
            a.modifier(-0.05 if self.caisse.solvable() else 0.15, 0, "guichet", cid)

    def _employe(self) -> None:
        """L'employé déjeune au café ; s'il n'est pas tenu au silence (ou s'il s'en écarte), il parle."""
        if not self.horloge.agences_ouvertes(self.t - 60) and not self.horloge.agences_ouvertes(self.t):
            return
        m = self.horloge.minute_du_jour(self.t)
        debut = minutes(self.routine["pause_dejeuner_employe"])
        if m == debut and self.employe.lieu != "cafe":
            self._emettre("agent.moved", {"agent_id": self.employe.id, "de": self.employe.lieu, "vers": "cafe",
                                          "t_sim_arrivee": self.t + 5})
            self.employe.lieu = "cafe"
        elif m == debut + 60 and self.employe.lieu == "cafe":
            self._emettre("agent.moved", {"agent_id": self.employe.id, "de": "cafe", "vers": "bourg",
                                          "t_sim_arrivee": self.t + 5})
            self.employe.lieu = "bourg"
        if self.employe.lieu != "cafe" or not self.rumeur_publiee or self._fuite_jour == self.horloge.jour(self.t):
            return
        if self.part_retiree() < self.R["seuil_fuite_employe"]:
            return
        if self.leviers["consigne_employe"] != "transparence" and self.rng.random() >= 0.2:
            return
        au_cafe = [a for a in self.deposants if a.etat == SUR_PLACE and a.lieu == "cafe"]
        if not au_cafe:
            return
        cible = self.rng.choice(au_cafe)
        self._fuite_jour = self.horloge.jour(self.t)
        did = self._decision(self.employe, "repondre", "R-fuite-cafe",
                             f"part des dépôts déjà retirée {self.part_retiree():.0%} ≥ "
                             f"{self.R['seuil_fuite_employe']:.0%} ; consigne « {self.leviers['consigne_employe']} »")
        eid = self._emettre("conversation.held", {"lieu": "cafe", "locuteur_id": self.employe.id,
                                                  "auditeur_id": cible.id, "phrase": textes.FUITE_CAFE,
                                                  "reponse": "Alors c'est vrai…", "mode": "regles", "call_id": None},
                            [did])
        cible.informe = True
        self._noter(cible, f"au café, l'employé d'agence t'a confié : « {textes.FUITE_CAFE} »", "conversation",
                    0.8, [eid])
        cible.modifier(0.25, 0.25, "conversation", eid)

    def _direction(self) -> None:
        self._recevoir_deliberation()
        if not self.leviers["direction_autonome"] or not self.rumeur_publiee or self.caisse.defaut:
            return
        R = self.R
        m = self.horloge.minute_du_jour(self.t)
        if m not in {minutes(h) for h in R["direction_controles"]}:
            return
        if self.t_dernier_communique is not None and self.t - self.t_dernier_communique < R["direction_delai_min_h"] * 60:
            return
        file_totale = sum(len(f) for f in self.files.values())
        part = self.part_retiree()
        if self.direction.type == "llm" and self.llm is not None:
            # Comme tout appel LLM : appliquée dans ce pas si la réponse arrive avant l'échéance, sinon au pas
            # où elle arrive, avec t_sim_contexte = l'instant où la direction a commencé à réfléchir (C3).
            if self._deliberation is None:
                art = self.cfg["article"]
                rappel = self._rappeler([self.direction], [f"Faut-il publier un communiqué ? {part:.0%} retirés, "
                                                           f"{file_totale} personnes en file"])[0]
                rq = self._requete(
                    self.direction, "communique", prompts.systeme_direction(self.direction.nom, self.direction.persona),
                    prompts.utilisateur_direction(self.horloge.libelle(self.t), f"{art['titre']} {art['texte']}",
                                                  self.caisse.reserves, self.reserves_depart, part, file_totale,
                                                  self.caisse.solvable(), self.leviers["garantie"],
                                                  self._dernier_communique, self._textes_rappel(rappel)),
                    prompts.valider_direction)
                self._deliberation = (self.llm.soumettre(rq), self.t, rappel)
                if self._attendre([self._deliberation[0]]):
                    self._recevoir_deliberation("dans_le_pas")
            return
        self._direction_regle(file_totale, part)

    def _recevoir_deliberation(self, voie: str = "en_retard") -> None:
        if self._deliberation is None or not self._deliberation[0].done():
            return
        future, t0, rappel = self._deliberation
        self._deliberation = None
        res = future.result()
        self._publier_appels(res)
        if self.caisse.defaut:
            return
        if res.ok:
            d = res.donnees
            did = self._decision(self.direction, d["action"], None, d["justification"],
                                 call_id=res.requete.call_id, t_contexte=t0, rappel=rappel, voie=voie)
            if self.memoire is not None:
                self._souvenir(self.direction, f"{self.horloge.libelle(t0)} : j'ai décidé {DECIDE[d['action']]}"
                               + (f" (ton {d['ton']}) : « {d['texte']} »" if d["action"] == "publier_communique" else "")
                               + f". {d['justification']}", "reflexion", 0.8, [did])
            if d["action"] == "publier_communique":
                self.publier_communique(d["ton"], "direction", [did], d["texte"])
            return
        self.replis += 1
        self._direction_regle(sum(len(f) for f in self.files.values()), self.part_retiree(),
                              f"repli sur la règle après un incident LLM ({res.incident['nature']}) : ")

    def _direction_regle(self, file_totale: int, part: float, repli: str = "") -> None:
        R = self.R
        if file_totale >= R["direction_seuil_file"] or part >= R["direction_seuil_retraits"]:
            did = self._decision(self.direction, "publier_communique", "R-direction",
                                 f"{repli}file totale {file_totale} (seuil {R['direction_seuil_file']}) ; part retirée "
                                 f"{part:.0%} (seuil {R['direction_seuil_retraits']:.0%})")
            self.publier_communique("factuel", "direction", [did])

    def _defaut(self, eid: str, servi: Deposant) -> None:
        self.t_defaut = self.t
        did = self._emettre("caisse.defaulted", {"solvable": self.caisse.solvable(),
                                                 "reserves_restantes": round(self.caisse.reserves, 2)}, [eid])
        for file in self.files.values():
            for aid in file:
                self._rentrer(self.par_id[aid])
            file.clear()
        for a in self.deposants:
            a.veut_retirer = False
            if a is servi or a.depot <= 0:
                continue
            garanti = min(a.depot, self.leviers["plafond_garantie"]) if self.leviers["garantie"] else 0.0
            a.garanti += garanti
            a.perdu += a.depot - garanti
            a.depot = 0.0
            a.retire = True
            a.modifier(1.0, 1.0, "fermeture_agence", did)
            if a.etat in (VERS_AGENCE, EN_FILE):
                a.agence = None
                self._rentrer(a)

    # ================================================================ commandes (D3)

    def traiter_commandes_externes(self) -> None:
        """Commandes envoyées depuis Godot via Kafka. Un message non conforme part en DLQ."""
        import json
        for brut in self.bus.commandes_recues():
            try:
                evt = json.loads(brut)
            except ValueError as e:
                self.bus.rejeter(brut, [f"JSON illisible : {e}"])
                continue
            errs = self._validateur_entrant.erreurs(evt) if isinstance(evt, dict) else ["pas un objet JSON"]
            if not errs and evt["type"] != "command.issued":
                errs = [f"type inattendu sur guichet.commands : {evt['type']}"]
            if errs:
                self.bus.rejeter(evt, errs)
                continue
            p = evt["payload"]
            self.appliquer_commande(p["commande"], p.get("parametres") or {}, "godot", [evt["event_id"]])

    def appliquer_commande(self, commande: str, parametres: dict, source: str = "godot", causes=()) -> bool:
        manquants = [k for k in PARAMETRES_REQUIS.get(commande, ["?"]) if k not in parametres]
        if commande not in PARAMETRES_REQUIS or manquants:
            self.bus.rejeter({"commande": commande, "parametres": parametres},
                             [f"commande inconnue ou paramètres manquants : {manquants or commande}"])
            return False
        p = parametres
        cid = self._emettre("command.issued", {"commande": commande, "parametres": {**p, "source": source}}, causes)
        if commande == "garantie":
            self.leviers["garantie"] = bool(p.get("active", True))
            if "plafond" in p:
                self.leviers["plafond_garantie"] = float(p["plafond"])
        elif commande == "plafond_retrait":
            self.leviers["plafond_retrait"] = min(max(float(p["valeur"]), 0.05), 1.0)
        elif commande == "fermer_agence":
            self._fermer_agence(p["agence_id"], cid)
        elif commande == "rouvrir_agence":
            self.fermees.discard(p["agence_id"])
        elif commande == "consigne_employe":
            self.leviers["consigne_employe"] = p["consigne"]
        elif commande == "publier_communique":
            self.publier_communique(p.get("ton", "factuel"), p.get("auteur", "utilisateur"), [cid])
        elif commande == "injecter_rumeur":
            self._publier_article(float(p.get("force", 0.5)), "injection", [cid])
        elif commande == "pertes_reelles":
            self.caisse.pertes_reelles = float(p["valeur"])
            self.caisse.realiser_perte()
            self._emettre("caisse.updated", self.caisse.etat(), [cid])
        elif commande == "pause":
            self.pause = True
        elif commande == "reprendre":
            self.pause = False
        elif commande == "vitesse":
            self.rythme_s = float(p["rythme_s"])
        return True

    def _fermer_agence(self, ag: str, cid: str) -> None:
        self.fermees.add(ag)
        pos_ag = self.monde.position(ag)
        file, self.files[ag] = self.files[ag], []
        for aid in file:
            a = self.par_id[aid]
            a.modifier(0.2, 0.15, "fermeture_agence", cid)
            self._noter(a, f"L'{self.nom_lieu(ag)} a fermé ses portes alors que tu faisais la queue", "observation",
                        0.9, [cid])
            a.agence = None
            a.etat = SUR_PLACE
            self._aller_agence(a, self.horloge.agences_ouvertes(self.t))
        for a in self.deposants:
            if a.etat == SUR_PLACE and Monde.distance(self._position(a), pos_ag) < 60.0:
                a.modifier(0.15, 0, "fermeture_agence", cid)
                self._noter(a, f"tu as vu {self.l_agence(ag)} fermer ses portes en pleine journée", "observation",
                            0.9, [cid])

    # ================================================================ publication de fin de pas

    def _fin_de_pas(self, ouvert: bool) -> None:
        for ag in self.monde.agences:
            etat = (len(self.files[ag]), self.agence_ouverte(ag, ouvert))
            if etat != self._files_publiees[ag]:
                self._emettre("queue.changed", {"agence_id": ag, "longueur": etat[0], "ouverte": etat[1]})
                self._files_publiees[ag] = etat
        self.file_max = max(self.file_max, sum(len(f) for f in self.files.values()))
        croyances_ids = []
        for a in self.deposants:
            if abs(a.peur - a.peur_publiee) >= 0.05 or abs(a.estime_autres - a.estime_publiee) >= 0.05:
                croyances_ids.append(self._emettre("belief.updated", {
                    "agent_id": a.id, "peur": round(a.peur, 4), "estime_autres": round(a.estime_autres, 4),
                    "peur_avant": round(a.peur_publiee, 4), "estime_autres_avant": round(a.estime_publiee, 4),
                    "cause": a.cause}, a.causes_ids))
                a.peur_publiee, a.estime_publiee, a.causes_ids = a.peur, a.estime_autres, []
            a.touche = False
        self._publier_indicateurs(croyances_ids)
        self._emettre("state.snapshot", {
            "agents": [{"id": a.id, "etat": a.etat, "lieu": a.lieu, "peur": round(a.peur, 3),
                        "estime_autres": round(a.estime_autres, 3), "depot": round(a.depot, 2),
                        "retire": a.retire, "perdu": round(a.perdu, 2)} for a in self.deposants],
            "files": {ag: len(f) for ag, f in self.files.items()},
            "agences_ouvertes": {ag: self.agence_ouverte(ag, ouvert) for ag in self.monde.agences},
            "caisse": self.caisse.etat(), "leviers": dict(self.leviers)})

    def _publier_indicateurs(self, croyances_ids: list[str]) -> None:
        """Indicateurs instantanés, publiés à chaque pas (B2). `sources` = variations de croyance de ce pas
        (F6). La version à fenêtre glissante viendra du service d'indicateurs (semaine 10)."""
        v = icc([a.estime_autres for a in self.deposants])
        self.serie_icc.append((self.t, v))
        self._emettre("indicator.computed", {"nom": "icc", "valeur": v["icc"], "fenetre_min": 0,
                                             "composantes": {"attente": v["attente"], "convergence": v["convergence"]},
                                             "sources": croyances_ids, "retard_ms": 0})
        # Basculement (repris de la maquette) : retraits engagés et imminents comparés aux réserves.
        plafond = self.leviers["plafond_retrait"]
        engages = au_bord = 0.0
        for a in self.deposants:
            if a.retire:
                continue
            if a.veut_retirer or a.etat in (VERS_AGENCE, EN_FILE):
                engages += a.depot * plafond
            elif a.pression(self.couverture(a.depot)) > 0.75 * a.seuil:
                au_bord += a.depot * plafond
        if self.caisse.defaut:
            niveau, etat = 3, "Défaut"
        elif engages > self.caisse.reserves:
            niveau, etat = 2, "Irréversible"
        elif engages + au_bord > self.caisse.reserves:
            niveau, etat = 1, "Proche"
        else:
            niveau, etat = 0, "Faible"
        if etat != self._basculement:
            self._basculement = etat
            self._emettre("indicator.computed", {"nom": "basculement", "valeur": niveau, "fenetre_min": 0,
                                                 "composantes": {"etat": etat, "engages": round(engages, 2),
                                                                 "au_bord": round(au_bord, 2),
                                                                 "reserves": round(self.caisse.reserves, 2)},
                                                 "sources": [], "retard_ms": 0})
