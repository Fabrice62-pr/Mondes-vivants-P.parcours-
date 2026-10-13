"""Plongements (embeddings) calculés en local : aucun appel à une API, donc gratuit (contrainte G).

Modèle par défaut : intfloat/multilingual-e5-small (118 M de paramètres, bon en français, ~470 Mo),
sur le GPU s'il est disponible. Les modèles e5 attendent un préfixe « query: » pour une question et
« passage: » pour un texte à retrouver.
"""
import hashlib
import math
import os
import re

# Pas de barres de progression ni d'avertissements (liens symboliques sous Windows) dans les journaux.
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
os.environ.setdefault("TQDM_DISABLE", "1")


class Plongement:
    def __init__(self, modele: str = "intfloat/multilingual-e5-small", appareil: str | None = None):
        from sentence_transformers import SentenceTransformer   # import tardif : bibliothèque lourde
        if appareil is None:
            try:
                import torch
                appareil = "cuda" if torch.cuda.is_available() else "cpu"
            except ImportError:
                appareil = "cpu"
        self.appareil = appareil
        self.nom = modele
        self._m = SentenceTransformer(modele, device=appareil)
        self._prefixes = modele.split("/")[-1].startswith(("multilingual-e5", "e5-"))

    def _encoder(self, textes: list[str], prefixe: str) -> list[list[float]]:
        if not textes:
            return []
        if self._prefixes:
            textes = [f"{prefixe}: {t}" for t in textes]
        return self._m.encode(textes, batch_size=64, normalize_embeddings=True, show_progress_bar=False).tolist()

    def passages(self, textes: list[str]) -> list[list[float]]:
        return self._encoder(textes, "passage")

    def questions(self, textes: list[str]) -> list[list[float]]:
        return self._encoder(textes, "query")


class PlongementFaux:
    """Pour les tests : sac de mots haché en 256 dimensions, normalisé. Rapide et déterministe."""
    nom = "faux"
    appareil = "cpu"

    def _un(self, t: str) -> list[float]:
        v = [0.0] * 256
        for mot in re.findall(r"\w+", t.lower()):
            v[int(hashlib.md5(mot.encode()).hexdigest(), 16) % 256] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    def passages(self, textes: list[str]) -> list[list[float]]:
        return [self._un(t) for t in textes]

    def questions(self, textes: list[str]) -> list[list[float]]:
        return [self._un(t) for t in textes]
