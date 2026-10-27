"""Mémoire des agents et base de connaissances (A1, F1, C6), dans PostgreSQL avec pgvector.

- **Corpus** (`rag/corpus.yaml`) : connaissances publiques de la vallée, antérieures au run. Table `corpus`,
  recalculée quand le corpus ou le modèle de plongement change.
- **Souvenirs** : ce que chaque agent LLM a appris pendant le run (memory.written). Table `souvenirs`,
  **conservée après le run** : on peut l'interroger en SQL (requetes/souvenirs_postgres.sql). Un agent ne
  rappelle jamais que les souvenirs de son propre run.

Le schéma est dans `rag/schema.sql`. PostgreSQL range et cherche les vecteurs ; il ne les calcule pas : les
plongements viennent toujours du modèle local (rag/plongement.py).

Rappel d'un souvenir : on cherche les plus proches de la situation, **parmi les souvenirs de l'agent
datés d'avant l'instant de la décision** (filtre dans la requête SQL), puis on les reclasse comme dans
« Generative Agents » (Park et al., 2023) : similarité + importance + fraîcheur.
"""
import hashlib
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import psycopg
import yaml
from pgvector.psycopg import register_vector

RACINE = Path(__file__).resolve().parent
# Service postgres de docker-compose.yml. 127.0.0.1 et non localhost : sous Windows, localhost essaie d'abord
# l'IPv6 ::1, où le conteneur n'écoute pas, et la connexion peut alors attendre plus d'une minute.
URL_DEFAUT = "postgresql://guichet:guichet@127.0.0.1:5433/guichet"
CANDIDATS = 12                     # souvenirs les plus proches lus en base, avant le reclassement


def url_postgres() -> str:
    return os.environ.get("POSTGRES_URL", URL_DEFAUT)


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


def _vecteur(v) -> np.ndarray:
    return np.asarray(v, dtype=np.float32)


class Memoire:
    def __init__(self, plongement, run_id: str, url: str | None = None, cfg: dict | None = None):
        cfg = cfg or {}
        self.plongement = plongement
        self.run_id = run_id
        self.k_souvenirs = int(cfg.get("k_souvenirs", 4))
        self.k_documents = int(cfg.get("k_documents", 2))
        self.demi_vie_h = float(cfg.get("demi_vie_h", 24))
        self.max_reflexions = int(cfg.get("max_reflexions", 1))
        url = url or url_postgres()
        try:
            self._cx = psycopg.connect(url, autocommit=True, connect_timeout=5)
        except psycopg.OperationalError as e:
            p = psycopg.conninfo.conninfo_to_dict(url)
            raise RuntimeError(f"PostgreSQL injoignable ({p.get('host')}:{p.get('port')}, base {p.get('dbname')}) : "
                               f"démarrez-le avec « docker compose up -d postgres » ou vérifiez POSTGRES_URL. {e}") from e
        self._cx.execute((RACINE / "schema.sql").read_text(encoding="utf-8"))
        register_vector(self._cx)                         # après CREATE EXTENSION : le type vector doit exister
        with self._cx.transaction():                      # un run relancé sous le même nom repart de zéro
            self._cx.execute("DELETE FROM memoire_runs WHERE run_id = %s", (run_id,))
            self._cx.execute("INSERT INTO memoire_runs (run_id, modele_plongement) VALUES (%s, %s)",
                             (run_id, plongement.nom))
        self._charger_corpus()
        self._en_attente: list[tuple] = []
        self.n_souvenirs = 0

    # ------------------------------------------------------------ corpus

    def _charger_corpus(self) -> None:
        docs = yaml.safe_load((RACINE / "corpus.yaml").read_text(encoding="utf-8"))["documents"]
        self.empreinte = hashlib.sha256((self.plongement.nom + json.dumps(docs, sort_keys=True, ensure_ascii=False))
                                        .encode("utf-8")).hexdigest()[:12]
        self.n_documents = len(docs)
        deja = self._cx.execute("SELECT count(*) FROM corpus WHERE empreinte = %s", (self.empreinte,)).fetchone()[0]
        if deja == len(docs):
            return
        textes = [f"{d['titre']}. {' '.join(d['texte'].split())}" for d in docs]
        vecteurs = self.plongement.passages(textes)
        with self._cx.transaction(), self._cx.cursor() as cur:
            cur.execute("DELETE FROM corpus")             # ancien corpus ou ancien modèle : on le remplace
            cur.executemany("INSERT INTO corpus (empreinte, doc_id, titre, texte, embedding) VALUES (%s, %s, %s, %s, %s)",
                            [(self.empreinte, d["doc_id"], d["titre"], t, _vecteur(v))
                             for d, t, v in zip(docs, textes, vecteurs)])

    # ------------------------------------------------------------ souvenirs

    def ecrire(self, memoire_id: str, agent_id: str, texte: str, t_sim: int, importance: float,
               doc_id: str | None = None, nature: str = "observation") -> None:
        """Le souvenir est mis en attente ; `vider` calcule les plongements en un seul lot."""
        self._en_attente.append((memoire_id, agent_id, texte, int(t_sim), float(importance), nature, doc_id))

    def vider(self) -> None:
        if not self._en_attente:
            return
        vecteurs = self.plongement.passages([s[2] for s in self._en_attente])
        with self._cx.transaction(), self._cx.cursor() as cur:
            cur.executemany("INSERT INTO souvenirs (run_id, memoire_id, agent_id, texte, t_sim, importance, nature, "
                            "doc_id, embedding) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                            [(self.run_id, *s, _vecteur(v)) for s, v in zip(self._en_attente, vecteurs)])
        self.n_souvenirs += len(self._en_attente)
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
            v = _vecteur(v)
            out.append((self._souvenirs_de(agent_id, v, t), self._documents(v)))
        return out

    def _souvenirs_de(self, agent_id: str, v: np.ndarray, t: int) -> list[Souvenir]:
        if self.k_souvenirs <= 0:
            return []
        lignes = self._cx.execute(
            "SELECT memoire_id, texte, t_sim, importance, nature, doc_id, embedding <=> %s AS distance "
            "FROM souvenirs WHERE run_id = %s AND agent_id = %s AND t_sim <= %s "
            "ORDER BY distance, memoire_id LIMIT %s", (v, self.run_id, agent_id, int(t), CANDIDATS)).fetchall()
        candidats = []
        for mid, txt, t_sim, importance, nature, doc_id, dist in lignes:
            similarite = 1.0 - float(dist)
            fraicheur = math.pow(0.5, (t - t_sim) / 60.0 / self.demi_vie_h)
            score = similarite + 0.5 * importance + 0.5 * fraicheur
            candidats.append(Souvenir(mid, txt, t_sim, importance, round(score, 4), doc_id, nature))
        candidats.sort(key=lambda s: -s.score)
        # Au plus un souvenir de ses propres décisions : sinon l'agent ne fait que ressasser sa décision
        # précédente (constaté au premier essai : 4 rappels sur 4 étaient des « j'ai décidé d'attendre »).
        out = []
        reflexions = 0
        for s in candidats:
            if s.nature == "reflexion":
                if reflexions >= self.max_reflexions:
                    continue
                reflexions += 1
            out.append(s)
        return out[:self.k_souvenirs]

    def _documents(self, v: np.ndarray) -> list[Document]:
        if self.k_documents <= 0:
            return []
        lignes = self._cx.execute(
            "SELECT doc_id, titre, texte, embedding <=> %s AS distance FROM corpus WHERE empreinte = %s "
            "ORDER BY distance, doc_id LIMIT %s", (v, self.empreinte, self.k_documents)).fetchall()
        return [Document(did, titre, txt, round(1.0 - float(dist), 4)) for did, titre, txt, dist in lignes]

    def fermer(self) -> None:
        """Fin du run : les derniers souvenirs sont écrits et le run est marqué terminé. Rien n'est effacé."""
        if self._cx.closed:
            return
        try:
            self.vider()
            self._cx.execute("UPDATE memoire_runs SET fin = now() WHERE run_id = %s", (self.run_id,))
        finally:
            self._cx.close()
