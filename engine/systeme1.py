"""Système 1 des déposants LLM : une décision rapide, sans appel API, qui ne passe la main au LLM
(Système 2) que lorsqu'il le faut.

Dans le moteur, « attendre » et « rien » ont le même effet : seul le retrait change la dynamique. Le
Système 1 répond donc à une seule question, « cette personne va-t-elle retirer son argent ? », avec une
probabilité p. La confiance est celle de la réponse choisie : max(p, 1 - p).
  - non, avec une confiance >= systeme1.confiance_min : le Système 1 décide seul, sans appel ;
  - oui : escalade vers le LLM, qui confirme ou non (un retrait est toujours vérifié) ;
  - confiance trop faible : escalade vers le LLM (situation ambiguë).
L'aiguillage lui-même est dans le moteur (Simulation._systeme1).

Trois moteurs (systeme1.moteur) :
  regle  : R-seuil. p vaut 0,5 quand la pression est au seuil et atteint 0 ou 1 à `marge` du seuil.
  nli    : un modèle d'inférence en langue naturelle, local (GPU si disponible). Il lit ce que l'agent sait,
           en français, et juge deux hypothèses opposées (retrait, maintien) ; p compare leurs scores
           d'implication, comme en classification zero-shot à deux étiquettes.
  appris : un classifieur entraîné à prédire la décision du LLM sur ses décisions passées
           (tools/systeme1_apprentissage.py). Il lit texte_s1, le même texte qu'à l'entraînement. Son seuil
           de décision seule vient de la validation (meta.json), sauf systeme1.confiance_min_appris.

Mesures du 27/10/2026 (3 graines, mode hybride) : regle divise les appels par 23 mais supprime la panique
(1 % retiré, aucun défaut, contre 15 % et 3 défauts) ; nli garde l'issue, mais sa probabilité ne prédit pas
les décisions du LLM (il en appelle encore les deux tiers). D'où le moteur appris.

Pourquoi deux hypothèses (essai du 27/10/2026 sur cinq situations types) : avec la seule hypothèse « Tu
décides de retirer ton argent de la Caisse. », un déposant serein recevait p = 0,72. Avec la paire retenue,
il reçoit 0,00 ; après l'article ou devant une petite file, p ≈ 0,5 (le LLM tranche) ; devant une longue
file, 0,97. Ces cinq cas ne sont pas un étalonnage : l'accord avec le LLM reste à mesurer sur les runs.
"""
import os
from dataclasses import dataclass

from llm import prompts

MODELE_NLI = "MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7"
HYPOTHESE_RETRAIT = "Tu as peur de perdre ton argent et tu vas le retirer."
HYPOTHESE_MAINTIEN = "Tu as confiance et tu gardes ton argent à la Caisse."
MODELE_APPRIS = "data/modeles/systeme1-appris"       # produit par tools/systeme1_apprentissage.py
MOTEURS = ("aucun", "regle", "nli", "appris")


@dataclass
class Avis:
    p_retirer: float
    action: str                   # retirer | attendre | rien
    detail: str                   # ce qui a fondé l'avis, repris dans la justification
    escalade: str | None = None   # retrait | ambigu : pourquoi le LLM a été appelé (renseigné par le moteur)

    @property
    def confiance(self) -> float:
        return max(self.p_retirer, 1.0 - self.p_retirer)

    def charge(self, moteur: str) -> dict:
        """decision.made.systeme1"""
        return {"moteur": moteur, "p_retirer": round(self.p_retirer, 4), "confiance": round(self.confiance, 4),
                "escalade": self.escalade}


def _action(p: float) -> str:
    return "retirer" if p > 0.5 else "attendre" if p > 0.25 else "rien"


class Systeme1Regle:
    nom, regle_id = "regle", "S1-regle"

    def __init__(self, cfg: dict):
        self.marge = float(cfg.get("marge", 0.10))

    def evaluer(self, sim, agents: list) -> list[Avis]:
        out = []
        for a in agents:
            pression = a.pression(sim.couverture(a.depot))
            p = min(max(0.5 + (pression - a.seuil) / (2.0 * self.marge), 0.0), 1.0)
            action = "retirer" if pression > a.seuil else "attendre" if pression > 0.6 * a.seuil else "rien"
            out.append(Avis(p, action, f"pression {pression:.2f}, seuil {a.seuil:.2f}"))
        return out


def _inquietude(peur: float) -> str:
    for borne, mot in ((0.15, "serein"), (0.35, "un peu inquiet"), (0.55, "inquiet"), (0.75, "très inquiet")):
        if peur < borne:
            return mot
    return "paniqué"


def _part(estime: float) -> str:
    for borne, mot in ((0.1, "très faible"), (0.3, "faible"), (0.5, "moyenne"), (0.7, "forte")):
        if estime < borne:
            return mot
    return "très forte"


class Systeme1Nli:
    """Les nombres sont traduits en mots : un modèle NLI raisonne mal sur des valeurs chiffrées."""
    nom, regle_id = "nli", "S1-nli"

    def __init__(self, cfg: dict, appareil: str | None = None):
        for cle in ("HF_HUB_DISABLE_PROGRESS_BARS", "HF_HUB_DISABLE_SYMLINKS_WARNING"):
            os.environ.setdefault(cle, "1")
        os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
        import torch                                  # import tardif : bibliothèque lourde
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        self._torch = torch
        self.appareil = appareil or ("cuda" if torch.cuda.is_available() else "cpu")
        self.modele = cfg.get("modele_nli", MODELE_NLI)
        self.hypotheses = (cfg.get("hypothese_retrait", HYPOTHESE_RETRAIT),
                           cfg.get("hypothese_maintien", HYPOTHESE_MAINTIEN))
        self.notes = int(cfg.get("notes_nli", 5))
        self._tok = AutoTokenizer.from_pretrained(self.modele)
        self._m = AutoModelForSequenceClassification.from_pretrained(self.modele).to(self.appareil).eval()
        etiquettes = {v.lower(): int(k) for k, v in self._m.config.id2label.items()}
        self._implication = etiquettes["entailment"]

    def premisse(self, sim, a) -> str:
        qui = prompts._phrase(a.persona or prompts.PROFILS.get(a.profil, ""))
        garantie = (f"Une garantie publique couvre les dépôts jusqu'à {prompts.montant(sim.leviers['plafond_garantie'])}."
                    if sim.leviers["garantie"] else "Aucune garantie publique ne couvre les dépôts.")
        notes = " ".join(prompts._phrase(f"{sim.horloge.libelle(t)}, {txt}") for t, txt in a.carnet[-self.notes:])
        return (f"{qui} Tu as {prompts.montant(a.depot)} déposés à la Caisse Coopérative de Brévanne. {garantie} "
                f"Ce que tu as appris : {notes or 'rien de particulier.'} Tu es {_inquietude(a.peur)}. Selon toi, "
                f"la part des autres déposants qui vont retirer leur argent est {_part(a.estime_autres)}.")

    def probabilites(self, premisses: list[str]) -> list[float]:
        """p(retrait) de chaque prémisse : un seul lot, chaque prémisse face aux deux hypothèses."""
        if not premisses:
            return []
        n = len(premisses)
        with self._torch.inference_mode():
            enc = self._tok(premisses * 2, [self.hypotheses[0]] * n + [self.hypotheses[1]] * n,
                            truncation="only_first", max_length=512, padding=True, return_tensors="pt").to(self.appareil)
            implication = self._m(**enc).logits[:, self._implication].float()
            return self._torch.stack([implication[:n], implication[n:]], 1).softmax(-1)[:, 0].tolist()

    def evaluer(self, sim, agents: list) -> list[Avis]:
        ps = self.probabilites([self.premisse(sim, a) for a in agents])
        return [Avis(p, _action(p), f"le modèle {self.modele.split('/')[-1]} juge le retrait probable à {p:.0%}")
                for p in ps]


def texte_s1(modele: str, qui: str, moment: str, depot: float, garantie: float | None, plafond_retrait: float,
             notes: list[tuple[str, str]], peur: float, estime: float) -> str:
    """Ce que lit le Système 1 appris, à l'entraînement comme dans le moteur. Les notes d'abord : si le texte est
    trop long, il est coupé au début (truncation_side = left), donc les notes les plus anciennes, jamais la
    situation de l'agent."""
    appris = " ".join(prompts._phrase(f"{quand}, {txt}") for quand, txt in notes) or "rien de particulier."
    situation = [f"Modèle {modele}.", prompts._phrase(qui), f"Nous sommes {moment}.",
                 f"Tu as {prompts.montant(depot)} à la Caisse.",
                 f"Garantie publique jusqu'à {prompts.montant(garantie)}." if garantie else "Aucune garantie publique.",
                 f"Inquiétude : {_inquietude(peur)} ({peur:.2f}).",
                 f"Part des autres qui vont retirer, selon toi : {_part(estime)} ({estime:.2f})."]
    if plafond_retrait < 1.0:
        situation.append(f"Retraits limités à {plafond_retrait:.0%} du dépôt.")
    return f"Ce que tu as appris : {appris} " + " ".join(situation)


class Systeme1Appris:
    nom, regle_id = "appris", "S1-appris"

    def __init__(self, cfg: dict, appareil: str | None = None):
        import json
        from pathlib import Path
        for cle in ("HF_HUB_DISABLE_PROGRESS_BARS", "HF_HUB_DISABLE_SYMLINKS_WARNING"):
            os.environ.setdefault(cle, "1")
        os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        from .config import ROOT
        chemin = Path(cfg.get("modele_appris", MODELE_APPRIS))
        chemin = chemin if chemin.is_absolute() else ROOT / chemin
        if not (chemin / "meta.json").exists():
            raise FileNotFoundError(f"Système 1 appris introuvable dans {chemin} : lancez "
                                    "python tools/systeme1_apprentissage.py donnees, puis entrainer")
        meta = json.loads((chemin / "meta.json").read_text(encoding="utf-8"))
        self._torch = torch
        self.appareil = appareil or ("cuda" if torch.cuda.is_available() else "cpu")
        self._tok = AutoTokenizer.from_pretrained(chemin)
        self._tok.truncation_side = "left"
        self._m = AutoModelForSequenceClassification.from_pretrained(chemin).to(self.appareil).eval()
        # décidé seul quand p < seuil, c'est-à-dire confiance = 1 - p > 1 - seuil
        self.confiance_min = float(cfg.get("confiance_min_appris", 1.0 - meta["seuil_recommande"]))

    def texte(self, sim, a) -> str:
        return texte_s1(a.modele, a.persona or prompts.PROFILS.get(a.profil, ""), sim.horloge.libelle(sim.t), a.depot,
                        sim.leviers["plafond_garantie"] if sim.leviers["garantie"] else None,
                        sim.leviers["plafond_retrait"], [(sim.horloge.libelle(t), txt) for t, txt in a.carnet],
                        a.peur, a.estime_autres)

    def probabilites(self, textes: list[str]) -> list[float]:
        if not textes:
            return []
        with self._torch.inference_mode():
            enc = self._tok(textes, truncation=True, max_length=512, padding=True,
                            return_tensors="pt").to(self.appareil)
            return self._m(**enc).logits.float().softmax(-1)[:, 1].tolist()

    def evaluer(self, sim, agents: list) -> list[Avis]:
        ps = self.probabilites([self.texte(sim, a) for a in agents])
        return [Avis(p, _action(p), f"le classifieur appris prédit un retrait du LLM à {p:.0%}") for p in ps]


def creer(cfg: dict | None):
    """Le Système 1 décrit par la configuration ; None pour « aucun »."""
    moteur = (cfg or {}).get("moteur", "aucun")
    if moteur == "aucun":
        return None
    if moteur == "regle":
        return Systeme1Regle(cfg)
    if moteur == "nli":
        return Systeme1Nli(cfg)
    if moteur == "appris":
        return Systeme1Appris(cfg)
    raise ValueError(f"systeme1.moteur inconnu : {moteur} (attendu : {', '.join(MOTEURS)})")
