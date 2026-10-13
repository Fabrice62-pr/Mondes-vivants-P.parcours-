-- A5 : un agent ne change jamais de modèle pendant un run. Doit renvoyer 0 ligne.
-- (vérification annoncée dans docs/README_api_key.md, § 7)
SELECT run_id, agent_id, count(DISTINCT modele) AS modeles
FROM llm_appels
GROUP BY run_id, agent_id
HAVING count(DISTINCT modele) > 1;

-- Même contrôle contre l'attribution publiée dans run.started : doit renvoyer 0 ligne.
SELECT l.run_id, l.agent_id, l.modele AS appele, a.modele AS attribue
FROM llm_appels l JOIN agents a USING (run_id, agent_id)
WHERE l.modele <> a.modele;
