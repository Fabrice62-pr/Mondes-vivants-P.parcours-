-- E4, E5 : coût de chaque run (appels, tokens, temps) et par modèle.
SELECT run_id, config, mode, deposants, appels_llm, tokens_entree + tokens_sortie AS tokens, replis,
       round(duree_reelle_s) AS duree_s,
       round((tokens_entree + tokens_sortie) / nullif(appels_llm, 0)) AS tokens_par_appel
FROM runs
WHERE llm_actif
ORDER BY run_id;

SELECT modele, fournisseur, tier,
       count(*)                                         AS appels,
       sum(CASE WHEN statut = 'ok' THEN 1 ELSE 0 END)   AS reussis,
       round(100.0 * sum(CASE WHEN statut = 'ok' THEN 1 ELSE 0 END) / count(*), 1) AS taux_reussite_pct,
       sum(tokens_entree + tokens_sortie)               AS tokens,
       round(median(latence_ms))                        AS latence_mediane_ms,
       round(quantile_cont(latence_ms, 0.95))           AS latence_p95_ms
FROM llm_appels
GROUP BY ALL
ORDER BY appels DESC;
