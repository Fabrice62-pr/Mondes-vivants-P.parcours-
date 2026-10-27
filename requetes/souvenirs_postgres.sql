-- Souvenirs des agents conservés dans PostgreSQL (rag/schema.sql), tous runs confondus.
-- À lancer dans le conteneur, depuis le dossier guichet-godot :
--   invite de commandes (cmd) :
--     docker exec -i guichet-postgres psql -U guichet -d guichet < requetes\souvenirs_postgres.sql
--   PowerShell :
--     Get-Content requetes\souvenirs_postgres.sql | docker exec -i guichet-postgres psql -U guichet -d guichet
-- (requetes/memoire.sql fait les mêmes analyses sur l'archive DuckDB, à partir des événements.)

-- Runs dont les agents ont écrit des souvenirs ; fin vide = run interrompu.
SELECT r.run_id, r.modele_plongement, r.debut, r.fin, count(s.memoire_id) AS souvenirs,
       count(DISTINCT s.agent_id) AS agents
FROM memoire_runs r LEFT JOIN souvenirs s USING (run_id)
GROUP BY 1, 2, 3, 4
ORDER BY r.debut DESC;

-- Nature des souvenirs par run (observation, conversation, guichet, réflexion).
SELECT run_id, nature, count(*) AS souvenirs, round(avg(importance)::numeric, 2) AS importance_moyenne
FROM souvenirs
GROUP BY 1, 2
ORDER BY 1, 3 DESC;

-- Agents qui se souviennent le plus, dans le dernier run.
SELECT agent_id, count(*) AS souvenirs, min(t_sim) AS premier_t_sim, max(t_sim) AS dernier_t_sim
FROM souvenirs
WHERE run_id = (SELECT run_id FROM memoire_runs ORDER BY debut DESC LIMIT 1)
GROUP BY 1
ORDER BY 2 DESC
LIMIT 15;

-- Documents du run (article, communiqués) d'où viennent le plus de souvenirs.
SELECT run_id, doc_id, count(*) AS souvenirs
FROM souvenirs
WHERE doc_id IS NOT NULL
GROUP BY 1, 2
ORDER BY 3 DESC;
