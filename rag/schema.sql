-- Mémoire des agents (A1, F1, C6) : schéma PostgreSQL + pgvector.
-- Exécuté par rag/memoire.py à chaque démarrage : chaque instruction ne fait rien si l'objet existe déjà.
-- Les vecteurs n'ont pas de dimension fixe : 384 pour multilingual-e5-small, 256 pour le PlongementFaux des tests.

CREATE EXTENSION IF NOT EXISTS vector;

-- Corpus de la vallée (rag/corpus.yaml), plongé avec un modèle donné. `empreinte` résume le corpus et le
-- modèle : quand l'un des deux change, le corpus est recalculé et l'ancien supprimé.
CREATE TABLE IF NOT EXISTS corpus (
    empreinte  TEXT NOT NULL,
    doc_id     TEXT NOT NULL,
    titre      TEXT NOT NULL,
    texte      TEXT NOT NULL,          -- « titre. texte », tel qu'il est plongé et cité dans le prompt
    embedding  vector NOT NULL,
    PRIMARY KEY (empreinte, doc_id)
);

-- Un run dont les agents ont écrit des souvenirs. `fin` reste vide si le run a été interrompu.
CREATE TABLE IF NOT EXISTS memoire_runs (
    run_id             TEXT PRIMARY KEY,
    modele_plongement  TEXT NOT NULL,
    debut              TIMESTAMPTZ NOT NULL DEFAULT now(),
    fin                TIMESTAMPTZ
);

-- Souvenirs des agents LLM (un par memory.written), conservés après le run pour l'analyse.
-- Un agent ne rappelle que les siens, dans son run, datés d'avant sa décision (C6).
CREATE TABLE IF NOT EXISTS souvenirs (
    run_id      TEXT NOT NULL REFERENCES memoire_runs ON DELETE CASCADE,
    memoire_id  TEXT NOT NULL,
    agent_id    TEXT NOT NULL,
    t_sim       INTEGER NOT NULL,
    importance  DOUBLE PRECISION NOT NULL,
    nature      TEXT NOT NULL,         -- observation | conversation | guichet | reflexion
    doc_id      TEXT,                  -- article ou communiqué publié pendant le run dont vient le souvenir
    texte       TEXT NOT NULL,
    embedding   vector NOT NULL,
    PRIMARY KEY (run_id, memoire_id)
);
-- Recherche exacte, sans index HNSW : un agent n'a que quelques dizaines de souvenirs, et un index approché
-- appliqué avant le filtre agent et date pourrait en rendre moins que demandé.
CREATE INDEX IF NOT EXISTS souvenirs_agent ON souvenirs (run_id, agent_id, t_sim);
