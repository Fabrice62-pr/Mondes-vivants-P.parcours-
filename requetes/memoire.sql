-- A1, F1, F2, C6 : ce que les agents se rappellent en décidant.
-- Documents du corpus et documents du run les plus souvent rappelés.
SELECT d->>'doc_id' AS doc_id, count(*) AS rappels
FROM decisions, unnest(from_json(documents, '["JSON"]')) AS u(d)
GROUP BY 1
ORDER BY 2 DESC;

-- Nature des souvenirs rappelés (observation, conversation, guichet, réflexion).
SELECT s.nature, count(*) AS rappels
FROM decisions d, unnest(d.souvenirs) AS u(memoire_id)
JOIN souvenirs s ON s.run_id = d.run_id AND s.memoire_id = u.memoire_id
GROUP BY 1
ORDER BY 2 DESC;

-- C6 : souvenirs rappelés postérieurs au contexte de la décision. Doit renvoyer 0 ligne.
SELECT d.run_id, d.agent_id, u.memoire_id, s.t_sim AS t_souvenir, d.t_sim_contexte
FROM decisions d, unnest(d.souvenirs) AS u(memoire_id)
JOIN souvenirs s ON s.run_id = d.run_id AND s.memoire_id = u.memoire_id
WHERE s.t_sim > d.t_sim_contexte OR s.agent_id <> d.agent_id;

SELECT run_id, count(*) AS violations FROM violations GROUP BY 1;
