"""Mémoire des agents et base de connaissances (A1, F1, C6), dans Chroma.

- **Corpus** (`rag/corpus.yaml`) : connaissances publiques de la vallée, antérieures au run. Collection
  persistante dans data/rag/chroma, reconstruite quand le corpus ou le modèle de plongement change.
- **Souvenirs** : ce que chaque agent LLM a appris pendant le run (memory.written). Collection propre au
  run, **en mémoire vive** : un run interrompu ne laisse rien sur le disque, et l'historique des souvenirs
  reste dans les événements (rejeu, semaine 7).

Rappel d'un souvenir : on cherche les plus proches de la situation, **parmi les souvenirs de l'agent
datés d'avant l'instant de la décision** (filtre dans la requête), puis on les reclasse comme dans
« Generative Agents » (Park et al., 2023) : similarité + importance + fraîcheur.
"""
import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

RACINE = Path(__file__).resolve().parent


@dataclass
class Souvenir:
    memoire_id: str
    texte: str
    t_sim: int
    importance: float
    score: float
    doc_id: str | None = None      # document publié pendant le run dont vient le souvenir (article, communiqué)
    nature: str = "observation"


@dataclass
class Document:
    doc_id: str
    titre: str
    texte: str
    score: float
    t_sim_doc: int | None = None


def _nom_collection(texte: str) -> str:
    """Chroma : 3 à 63 caractères, lettres, chiffres, « . _ - »."""
    return re.sub(r"[^A-Za-z0-9._-]", "-", texte)[:63].strip("-._") or "collection"


class Memoire:
    def __init__(self, plongement, run_id: str, dossier: Path | None = None, cfg: dict | None = None):
        import chromadb                                   # import tardif : bibliothèque lourde
        from chromadb.config import Settings
        cfg = cfg or {}
        self.plongement = plongement
        self.k_souvenirs = int(cfg.get("k_souvenirs", 4))
        self.k_documents = int(cfg.get("k_documents", 2))
        self.demi_vie_h = float(cfg.get("demi_vie_h", 24))
        self.max_reflexions = int(cfg.get("max_reflexions", 1))
        reglages = Settings(anonymized_telemetry=False, allow_reset=False)
        self._vive = chromadb.EphemeralClient(settings=reglages)
        if dossier is None:
            self._client = self._vive
        else:
            dossier.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=str(dossier), settings=reglages)
        self.corpus = self._charger_corpus()
        self._souvenirs = self._vive.get_or_create_collection(_nom_collection(f"souvenirs-{run_id}"),
                                                              metadata={"hnsw:space": "cosine"})
        self._en_attente: list[tuple[str, str, dict]] = []
        self.n_souvenirs = 0

    # ------------------------------------------------------------ corpus

    def _charger_corpus(self):
        docs = yaml.safe_load((RACINE / "corpus.yaml").read_text(encoding="utf-8"))["documents"]
        empreinte = hashlib.sha256((self.plongement.nom + json.dumps(docs, sort_keys=True, ensure_ascii=False))
                                   .encode("utf-8")).hexdigest()[:12]
        nom = _nom_collection(f"corpus-{empreinte}")
        existantes = {c.name if hasattr(c, "name") else c for c in self._client.list_collections()}
        if nom in existantes:
            return self._client.get_collection(nom)
        for vieux in existantes:                         # ancien corpus ou ancien modèle : on le remplace
            if str(vieux).startswith("corpus-"):
                self._client.delete_collection(vieux)
        col = self._client.create_collection(nom, metadata={"hnsw:space": "cosine"})
        textes = [f"{d['titre']}. {' '.join(d['texte'].split())}" for d in docs]
        col.add(ids=[d["doc_id"] for d in docs], documents=textes, embeddings=self.plongement.passages(textes),
                metadatas=[{"titre": d["titre"]} for d in docs])
        return col

    # ------------------------------------------------------------ souvenirs

    def ecrire(self, memoire_id: str, agent_id: str, texte: str, t_sim: int, importance: float,
               doc_id: str | None = None, nature: str = "observation") -> None:
        """Le souvenir est mis en attente ; `vider` calcule les plongements en un seul lot."""
        meta = {"agent_id": agent_id, "t_sim": int(t_sim), "importance": float(importance), "nature": nature}
        if doc_id:
            meta["doc_id"] = doc_id
        self._en_attente.append((memoire_id, texte, meta))

    def vider(self) -> None:
        if not self._en_attente:
            return
        ids, textes, metas = zip(*self._en_attente)
        self._souvenirs.add(ids=list(ids), documents=list(textes), metadatas=list(metas),
                            embeddings=self.plongement.passages(list(textes)))
        self.n_souvenirs += len(ids)
        self._en_attente = []

    def rappeler(self, requetes: list[tuple[str, str, int]]) -> list[tuple[list[Souvenir], list[Document]]]:
        """Pour chaque (agent_id, situation, t_sim) : ses souvenirs les plus utiles et les documents du corpus
        les plus proches. Les plongements des situations sont calculés en un seul lot."""
        self.vider()
        if not requetes:
            return []
        vecteurs = self.plongement.questions([q for _, q, _ in requetes])
        out = []
        for (agent_id, _, t), v in zip(requetes, vecteurs):
            out.append((self._souvenirs_de(agent_id, v, t), self._documents(v)))
        return out

    def _souvenirs_de(self, agent_id: str, v: list[float], t: int) -> list[Souvenir]:
        if self.k_souvenirs <= 0:
            return []
        filtre = {"$and": [{"agent_id": agent_id}, {"t_sim": {"$lte": int(t)}}]}
        n = self._souvenirs.count()
        if n == 0:
            return []
        r = self._souvenirs.query(query_embeddings=[v], n_results=min(12, n), where=filtre,
                                  include=["documents", "metadatas", "distances"])
        candidats = []
        reflexions = 0
        for mid, txt, meta, dist in zip(r["ids"][0], r["documents"][0], r["metadatas"][0], r["distances"][0]):
            similarite = 1.0 - float(dist)
            fraicheur = math.pow(0.5, (t - int(meta["t_sim"])) / 60.0 / self.demi_vie_h)
            score = similarite + 0.5 * float(meta["importance"]) + 0.5 * fraicheur
            candidats.append(Souvenir(mid, txt, int(meta["t_sim"]), float(meta["importance"]), round(score, 4),
                                      meta.get("doc_id")))
            candidats[-1].nature = meta.get("nature", "observation")
        candidats.sort(key=lambda s: -s.score)
        # Au plus un souvenir de ses propres décisions : sinon l'agent ne fait que ressasser sa décision
        # précédente (constaté au premier essai : 4 rappels sur 4 étaient des « j'ai décidé d'attendre »).
        out = []
        for s in candidats:
            if s.nature == "reflexion":
                if reflexions >= self.max_reflexions:
                    continue
                reflexions += 1
            out.append(s)
        return out[:self.k_souvenirs]

    def _documents(self, v: list[float]) -> list[Document]:
        if self.k_documents <= 0:
            return []
        r = self.corpus.query(query_embeddings=[v], n_results=self.k_documents,
                              include=["documents", "metadatas", "distances"])
        return [Document(did, meta["titre"], txt, round(1.0 - float(dist), 4))
                for did, txt, meta, dist in zip(r["ids"][0], r["documents"][0], r["metadatas"][0], r["distances"][0])]

    def fermer(self) -> None:
        try:
            self._vive.delete_collection(self._souvenirs.name)
        except Exception:
            pass
