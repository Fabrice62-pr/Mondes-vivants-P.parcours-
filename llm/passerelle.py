"""Passerelle LLM : le seul chemin entre les agents et les modèles.

Règles (docs/README_api_key.md, § 7 et 8) :
- le modèle vient UNIQUEMENT de l'attribution publiée dans run.started : une requête pour un autre
  modèle est refusée (incident `modele_non_attribue`) ;
- limite de débit par modèle (requêtes et tokens par minute, requêtes par jour) ;
- en cas d'échec (délai, quota, erreur, JSON invalide, réponse hors rôle) : un nouvel essai sur le MÊME
  modèle, puis repli sur la règle de l'agent, avec un incident. Jamais un autre modèle ;
- chaque essai produit un `llm.called` (tokens, latence, statut, numéro d'essai : E4).

En mode synchrone, le moteur envoie toutes les requêtes d'un pas en un lot et attend les réponses
(barrière). Les requêtes partent en parallèle (`max_simultanes`).

La passerelle ne publie rien elle-même : elle renvoie les charges utiles des événements, que le moteur
publie dans l'ordre, depuis le fil principal (numérotation `seq` stable).
"""
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .fournisseurs import Fournisseur, Reponse
from .limites import CompteurJour, Limiteur

# Statut d'un essai (llm.called.statut) -> nature de l'incident (incident.raised.nature)
NATURE = {"timeout": "timeout", "json_invalide": "json_invalide", "hors_role": "hors_role",
          "quota": "quota", "erreur": "erreur_api"}


@dataclass
class Requete:
    call_id: str
    agent_id: str
    modele: str                        # clé du catalogue (configs/models.yaml)
    usage: str                         # decision | guichet | communique | ...
    systeme: str
    utilisateur: str
    valider: Callable[[str], tuple]    # texte -> (données | None, statut, détail)
    temperature: float = 0.7


@dataclass
class Resultat:
    requete: Requete
    donnees: dict | None = None
    appels: list = field(default_factory=list)     # charges utiles llm.called, dans l'ordre des essais
    incident: dict | None = None                    # charge utile incident.raised si repli

    @property
    def ok(self) -> bool:
        return self.donnees is not None


class Passerelle:
    def __init__(self, catalogue: dict, cfg_llm: dict, attribution: dict[str, str],
                 fournisseurs: dict | None = None, fichier_quota: Path | None = None):
        self.modeles = catalogue["models"]
        self.attribution = dict(attribution)        # agent_id -> modèle, figé pour le run
        self.essais = int(cfg_llm.get("essais", 2))
        self.budget = int(cfg_llm.get("budget_appels", 4000))
        self.delai_defaut = float(cfg_llm.get("delai_max_s", 30))
        # En mode synchrone, le pas attend que le quota se libère (barrière) au lieu de faire décider
        # l'agent par règle ; au-delà de cette attente, repli avec incident « quota ».
        self.attente_quota = float(cfg_llm.get("attente_quota_max_s", 120))
        # Un 429 (quota, ou serveur saturé : « backend_out_of_capacity » chez Mistral) est passager : on
        # réessaie après une attente croissante, sans entamer les essais prévus pour les vraies erreurs.
        self.essais_quota = int(cfg_llm.get("essais_quota", 4))
        self.max_simultanes = int(cfg_llm.get("max_simultanes", 6))
        self.fournisseurs = fournisseurs or {n: Fournisseur(n, p) for n, p in catalogue["providers"].items()}
        jour = CompteurJour(fichier_quota)
        self.limiteurs = {m: Limiteur(m, v.get("limites", {}), jour) for m, v in self.modeles.items()}
        self.jour = jour
        self.n_appels = 0
        self.stats: dict[str, dict] = {}
        self._signales: set = set()                # incidents qui ne se répètent pas (clé, budget)
        self._verrou = threading.Lock()             # compteurs partagés entre les fils d'exécution
        self._simultanes = threading.BoundedSemaphore(self.max_simultanes)   # requêtes sans barrière en vol
        self._pool = ThreadPoolExecutor(max_workers=self.max_simultanes, thread_name_prefix="llm")

    def fermer(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------ lots

    def executer(self, requetes: list[Requete]) -> list[Resultat]:
        """Exécute un lot en parallèle ; les résultats sont rendus dans l'ordre des requêtes."""
        if not requetes:
            return []
        return list(self._pool.map(self._executer_une, requetes))

    def soumettre(self, rq: Requete) -> Future:
        """Requête sans barrière : le résultat arrive plus tard (direction, dont le modèle met près d'une
        minute à répondre). Fil d'exécution « daemon » : un run qui s'arrête ne l'attend pas."""
        f: Future = Future()

        def travail():
            try:
                with self._simultanes:
                    f.set_result(self._executer_une(rq))
            except Exception as e:   # ne jamais laisser une requête sans réponse
                f.set_result(self._incident(Resultat(rq), "erreur_api", f"exception : {e}"))
        threading.Thread(target=travail, daemon=True, name=f"llm-{rq.call_id}").start()
        return f

    # ------------------------------------------------------------ une requête

    def _incident(self, res: Resultat, nature: str, detail: str, repli: str = "regles") -> Resultat:
        rq = res.requete
        res.incident = {"nature": nature, "agent_id": rq.agent_id, "call_id": rq.call_id,
                        "detail": detail[:300] or nature, "repli": repli}
        return res

    def _executer_une(self, rq: Requete) -> Resultat:
        res = Resultat(rq)
        if self.attribution.get(rq.agent_id) != rq.modele:
            return self._incident(res, "modele_non_attribue",
                                  f"{rq.agent_id} : {rq.modele} demandé, {self.attribution.get(rq.agent_id)} attribué")
        m = self.modeles[rq.modele]
        fournisseur = self.fournisseurs[m["provider"]]
        if hasattr(fournisseur, "disponible") and not fournisseur.disponible():
            cle = ("cle", m["provider"])
            res = self._incident(res, "erreur_api", f"clé API absente pour {m['provider']} (voir .env)")
            if cle in self._signales:
                res.incident["repli"] = "ignore"     # déjà signalé : compté, pas republié
            self._signales.add(cle)
            return res
        delai = float(m.get("timeout_s", self.delai_defaut))
        utilisateur = rq.utilisateur
        dernier = ""
        essai = echecs = n_quota = 0
        while echecs < self.essais:
            with self._verrou:
                epuise = self.n_appels >= self.budget
                if not epuise:
                    self.n_appels += 1          # réservé tout de suite : le budget ne peut pas être dépassé
                deja = "budget" in self._signales
                if epuise:
                    self._signales.add("budget")
            if epuise:
                res = self._incident(res, "budget_epuise", f"budget de {self.budget} appels atteint pour ce run")
                if deja:
                    res.incident["repli"] = "ignore"
                return res
            debut = time.monotonic()
            # ~3,5 caractères par token en français ; la sortie réelle (~100 à 300 tokens) est corrigée après l'appel
            estime = int((len(rq.systeme) + len(utilisateur)) / 3.5) + min(int(m.get("max_tokens", 400)), 300)
            reservation = self.limiteurs[rq.modele].reserver(estime, debut + self.attente_quota)
            if reservation is None:
                with self._verrou:
                    self.n_appels -= 1
                res.appels.append(self._appel(rq, essai, Reponse(0, latence_ms=int((time.monotonic() - debut) * 1000)),
                                              "quota"))
                return self._incident(res, "quota", f"limite de débit de {rq.modele} atteinte (ou quota du jour épuisé)")
            reste = delai
            self.jour.ajouter(rq.modele)
            rep = fournisseur.appeler(m, rq.systeme, utilisateur, rq.temperature, reste)
            self.limiteurs[rq.modele].corriger(reservation, rep.tokens_entree + rep.tokens_sortie or estime)
            if rep.statut == 200 and not rep.erreur:
                donnees, statut, detail = rq.valider(rep.texte)
            elif rep.statut == 429:
                statut, detail, donnees = "quota", rep.erreur, None
                n_quota += 1
                self.limiteurs[rq.modele].bloquer(rep.attente_s or 2.0 * 2 ** (n_quota - 1))
            elif rep.statut == 0 and ("timed out" in rep.erreur or "timeout" in rep.erreur.lower()):
                statut, detail, donnees = "timeout", f"pas de réponse en {reste:.0f} s", None
            else:
                statut, detail, donnees = "erreur", f"HTTP {rep.statut} : {rep.erreur}", None
            res.appels.append(self._appel(rq, essai, rep, statut))
            if statut == "ok":
                res.donnees = donnees
                return res
            dernier = f"{statut} : {detail}"
            essai += 1
            if statut == "quota" and n_quota <= self.essais_quota:
                continue                             # attente puis nouvel essai, hors du compte des échecs
            echecs += 1
            if statut in ("json_invalide", "hors_role"):
                utilisateur = (rq.utilisateur + "\n\nATTENTION : ta réponse précédente était refusée (" + detail +
                               "). Réponds UNIQUEMENT avec l'objet JSON demandé, sans aucun texte autour.")
        return self._incident(res, NATURE.get(dernier.split(" :")[0], "erreur_api"),
                              f"{essai} essais sur {rq.modele} ; dernier : {dernier}")

    def _appel(self, rq: Requete, essai: int, rep: Reponse, statut: str) -> dict:
        m = self.modeles[rq.modele]
        with self._verrou:
            s = self.stats.setdefault(rq.modele, {"appels": 0, "ok": 0, "echecs": 0, "tokens_entree": 0,
                                                  "tokens_sortie": 0, "latence_ms_total": 0})
            s["appels"] += 1
            s["ok" if statut == "ok" else "echecs"] += 1
            s["tokens_entree"] += rep.tokens_entree
            s["tokens_sortie"] += rep.tokens_sortie
            s["latence_ms_total"] += rep.latence_ms
        return {"call_id": rq.call_id, "agent_id": rq.agent_id, "usage": rq.usage, "fournisseur": m["provider"],
                "modele": rq.modele, "tier": m.get("tier", "auxiliaire"), "tokens_entree": rep.tokens_entree,
                "tokens_sortie": rep.tokens_sortie, "latence_ms": rep.latence_ms, "statut": statut, "essai": essai}

    def bilan(self) -> dict:
        """Coût du run (E4, E5), repris dans resume.json."""
        tot = {"appels": 0, "ok": 0, "echecs": 0, "tokens_entree": 0, "tokens_sortie": 0}
        par_modele = {}
        for m, s in sorted(self.stats.items()):
            par_modele[m] = {**{k: v for k, v in s.items() if k != "latence_ms_total"},
                             "latence_ms_moyenne": round(s["latence_ms_total"] / max(s["appels"], 1))}
            for k in tot:
                tot[k] += s[k]
        return {**tot, "par_modele": par_modele}
