-- B4, F6 : pour la plus forte hausse de l'ICC de chaque run (sur 1 h simulée), les causes des
-- variations de croyance de cette heure-là.
WITH icc AS (
    SELECT run_id, t_sim, valeur,
           valeur - lag(valeur, 4) OVER (PARTITION BY run_id ORDER BY t_sim) AS hausse_1h
    FROM indicateurs
    WHERE nom = 'icc' AND producteur = 'moteur'
), pic AS (
    SELECT run_id, arg_max(t_sim, hausse_1h) AS t_sim, max(hausse_1h) AS hausse_1h,
           arg_max(valeur, hausse_1h) AS icc
    FROM icc
    WHERE hausse_1h IS NOT NULL
    GROUP BY run_id
)
SELECT p.run_id, p.t_sim, round(p.icc, 2) AS icc, round(p.hausse_1h, 2) AS hausse_1h, c.cause,
       count(*) AS croyances
FROM pic p
JOIN croyances c ON c.run_id = p.run_id AND c.t_sim BETWEEN p.t_sim - 45 AND p.t_sim
GROUP BY ALL
ORDER BY p.run_id, croyances DESC;
