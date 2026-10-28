-- Comparaison des séries de runs par étiquette (--etiquette au lancement) : effet du Système 1 sur la
-- latence et le coût, et sur l'issue de la simulation. Moyenne et dispersion sur les graines (E2).
--   python -m services.archiveur --sql requetes/etiquettes.sql
-- Les runs interrompus sont exclus : leur issue n'est pas comparable à celle d'un run allé à son terme.

-- 1. Latence et coût
SELECT coalesce(etiquette, '(sans étiquette)')          AS etiquette, mode, systeme1,
       count(*)                                          AS runs,
       round(avg(appels_llm))                            AS appels_llm_moyen,
       round(avg(tokens_entree + tokens_sortie))         AS tokens_moyen,
       round(avg(latence_mediane_ms))                    AS latence_mediane_ms,
       round(avg(latence_p95_ms))                        AS latence_p95_ms,
       round(100.0 * sum(decisions_en_retard) / nullif(sum(decisions_llm), 0), 1) AS pct_decisions_en_retard,
       round(avg(retard_moyen_min), 1)                   AS retard_moyen_min,
       max(retard_max_min)                               AS retard_max_min,
       round(100.0 * sum(s1_decide_seul) / nullif(sum(s1_evaluations), 0), 1)     AS pct_decide_par_s1,
       sum(s1_retraits_confirmes)                        AS retraits_confirmes,
       sum(s1_retraits_infirmes)                         AS retraits_infirmes,
       round(avg(duree_reelle_s))                        AS duree_reelle_s
FROM runs
WHERE NOT interrompu AND llm_actif
GROUP BY ALL
ORDER BY etiquette, mode;

-- 2. Issue de la simulation : le Système 1 ne doit pas changer la dynamique (sinon, le dire)
SELECT coalesce(etiquette, '(sans étiquette)')          AS etiquette, mode, systeme1,
       count(*)                                          AS runs,
       round(avg(part_retiree), 3)                       AS part_retiree_moy,
       round(stddev_samp(part_retiree), 3)               AS part_retiree_ecart,
       round(avg(icc_max), 3)                            AS icc_max_moy,
       round(stddev_samp(icc_max), 3)                    AS icc_max_ecart,
       sum(CASE WHEN defaut THEN 1 ELSE 0 END)           AS defauts,
       sum(CASE WHEN emergence THEN 1 ELSE 0 END)        AS emergences,
       round(avg(t_emergence) / 60.0, 1)                 AS t_emergence_moy_h,
       round(avg(file_max), 1)                           AS file_max_moy
FROM runs
WHERE NOT interrompu
GROUP BY ALL
ORDER BY etiquette, mode;

-- 3. Inventaire : tous les runs, y compris interrompus
SELECT run_id, coalesce(etiquette, '') AS etiquette, config, mode, systeme1, interrompu, fin,
       appels_llm, round(part_retiree, 3) AS part_retiree, defaut
FROM runs
ORDER BY run_id DESC;
