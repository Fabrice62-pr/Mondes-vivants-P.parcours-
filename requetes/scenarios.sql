-- B3, E2, F5 : comparaison des scénarios, moyenne et dispersion sur les graines.
-- Ne garde que les runs allés jusqu'au défaut ou à la fin de l'horloge (resume.json présent).
SELECT config, mode, llm_actif, rag_actif, deposants,
       count(*)                                   AS runs,
       round(avg(icc_max), 3)                     AS icc_max_moyen,
       round(stddev_samp(icc_max), 3)             AS icc_max_ecart_type,
       sum(CASE WHEN emergence THEN 1 ELSE 0 END) AS emergences,
       sum(CASE WHEN defaut THEN 1 ELSE 0 END)    AS defauts,
       round(avg(part_retiree), 3)                AS part_retiree_moyenne,
       round(avg(file_max), 1)                    AS file_max_moyenne
FROM runs
GROUP BY ALL
ORDER BY config, mode, llm_actif, rag_actif, deposants;
