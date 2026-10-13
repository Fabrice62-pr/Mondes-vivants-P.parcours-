"""Limites de débit par modèle : requêtes et tokens par minute, requêtes par jour (quotas gratuits).

Les quotas se comptent par modèle chez Groq (1 000 requêtes/jour, 8 000 tokens/minute) : un limiteur
par modèle, partagé par tous les agents qui l'utilisent. Le compteur journalier est conservé d'un run
à l'autre dans data/logs/llm_quota.json, puisque le quota de Groq est remis à zéro chaque jour (UTC).
"""
import json
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path


class CompteurJour:
    def __init__(self, fichier: Path | None):
        self.fichier = fichier
        self._verrou = threading.Lock()
        self._jour = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._n: dict[str, int] = {}
        if fichier is not None and fichier.exists():
            try:
                self._n = json.loads(fichier.read_text(encoding="utf-8")).get(self._jour, {})
            except ValueError:
                self._n = {}

    def valeur(self, modele: str) -> int:
        with self._verrou:
            return self._n.get(modele, 0)

    def ajouter(self, modele: str) -> None:
        with self._verrou:
            self._n[modele] = self._n.get(modele, 0) + 1
            if self.fichier is not None:
                self.fichier.parent.mkdir(parents=True, exist_ok=True)
                self.fichier.write_text(json.dumps({self._jour: self._n}, indent=1), encoding="utf-8")


class Limiteur:
    """Fenêtre glissante de 60 s. `reserver` attend son tour, sans dépasser l'échéance donnée."""

    def __init__(self, modele: str, limites: dict, jour: CompteurJour):
        self.modele = modele
        self.rpm = limites.get("rpm")
        self.tpm = limites.get("tpm")
        self.rpd = limites.get("rpd")
        self.jour = jour
        self._verrou = threading.Lock()
        self._fenetre: deque = deque()        # [instant, tokens]
        self._bloque_jusqua = 0.0

    def _attente(self, maintenant: float, tokens: int) -> float:
        while self._fenetre and maintenant - self._fenetre[0][0] >= 60.0:
            self._fenetre.popleft()
        attente = max(0.0, self._bloque_jusqua - maintenant)
        if self.rpm and len(self._fenetre) >= self.rpm:
            attente = max(attente, 60.0 - (maintenant - self._fenetre[0][0]))
        if self.tpm:
            utilises = sum(t for _, t in self._fenetre)
            if utilises + tokens > self.tpm and self._fenetre:
                # attendre que les plus anciens sortent de la fenêtre
                cumul = utilises
                for instant, t in self._fenetre:
                    cumul -= t
                    if cumul + tokens <= self.tpm:
                        attente = max(attente, 60.0 - (maintenant - instant))
                        break
        return attente

    def reserver(self, tokens: int, echeance: float) -> list | None:
        """Renvoie la réservation (à corriger avec les tokens réels), ou None si le quota ne le permet pas
        avant l'échéance (ou si le quota du jour est épuisé)."""
        if self.rpd and self.jour.valeur(self.modele) >= self.rpd:
            return None
        while True:
            with self._verrou:
                maintenant = time.monotonic()
                attente = self._attente(maintenant, tokens)
                if attente <= 0.0:
                    r = [maintenant, tokens]
                    self._fenetre.append(r)
                    return r
            if time.monotonic() + attente > echeance:
                return None
            time.sleep(min(attente, 0.5))

    def corriger(self, reservation: list, tokens_reels: int) -> None:
        with self._verrou:
            reservation[1] = tokens_reels

    def bloquer(self, secondes: float) -> None:
        """Après un 429 : plus aucun envoi sur ce modèle pendant le délai demandé par le fournisseur."""
        with self._verrou:
            self._bloque_jusqua = max(self._bloque_jusqua, time.monotonic() + secondes)
