-- D2, A4 : d'où viennent les décisions (LLM, règle, repli) et que décident les agents.
SELECT r.config, r.llm_actif, d.type, d.action, count(*) AS n
FROM decisions d JOIN runs r USING (run_id)
GROUP BY ALL
ORDER BY r.config, r.llm_actif, n DESC;

-- H4 : la boulangère et le retraité face à la même crise (justifications de leurs décisions LLM).
SELECT run_id, t_sim, agent_id, action, justification
FROM decisions
WHERE agent_id IN ('boulangere', 'retraite') AND type = 'llm'
ORDER BY run_id, agent_id, t_sim;
