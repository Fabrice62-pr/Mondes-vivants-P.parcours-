"""Métriques du tableau de bord : décisions des agents, confiance et fiabilité de l'IA.

Chaque métrique est UNE requête SQL, écrite une seule fois. Elle porte sur les tables de l'archiveur
(decisions, croyances, llm_appels, indicateurs, agents...), qui existent sous deux formes :
  - l'historique : vues sur les Parquet des runs archivés (services.archiveur.ouvrir) ;
  - le direct : vues sur les événements reçus de Kafka (services.tableau), construites avec les mêmes
    projections (services.archiveur.selection).
Un même run donne donc les mêmes chiffres en direct et dans l'historique (tests/test_tableau.py).

Le paramètre $run choisit un run ; NULL prend tous les runs de la base. Les séries temporelles n'ont de
sens que pour un run.
"""
import json
import math

# Mode de décision lisible (decision.made.type).
DECIDEUR = """CASE type WHEN 'llm' THEN 'LLM' WHEN 'systeme1' THEN 'Système 1' WHEN 'regles' THEN 'Règle'
              ELSE 'Repli sur règle' END"""

RUN = "($run IS NULL OR run_id = $run)"

METRIQUES = {
    # Les chiffres clés en tête du tableau de bord.
    "synthese": f"""
        WITH d AS (SELECT * FROM decisions WHERE {RUN}),
             l AS (SELECT * FROM llm_appels WHERE {RUN}),
             i AS (SELECT * FROM indicateurs WHERE nom = 'icc' AND {RUN}),
             r AS (SELECT * FROM retraits WHERE {RUN})
        SELECT (SELECT count(*) FROM d) AS decisions,
               (SELECT count(*) FROM d WHERE type = 'llm') AS decisions_llm,
               (SELECT count(*) FROM d WHERE type = 'systeme1') AS decisions_systeme1,
               (SELECT count(*) FROM d WHERE type = 'regles') AS decisions_regles,
               (SELECT count(*) FROM d WHERE type = 'repli_regles') AS decisions_repli,
               (SELECT count(*) FROM l) AS appels_llm,
               (SELECT count(*) FROM l WHERE statut = 'ok') AS appels_reussis,
               (SELECT count(*) FROM r) AS retraits,
               (SELECT max(t_sim) FROM d) AS t_sim_max,
               (SELECT arg_max(valeur, seq) FROM i) AS icc_actuel,
               (SELECT max(valeur) FROM i) AS icc_max,
               (SELECT count(DISTINCT run_id) FROM d) AS runs
    """,

    # Qui décide : part de chaque mode de décision, par rôle d'agent.
    "qui_decide": f"""
        SELECT a.role, {DECIDEUR.replace('type', 'd.type')} AS decideur, count(*) AS decisions
        FROM decisions d JOIN agents a USING (run_id, agent_id)
        WHERE ($run IS NULL OR d.run_id = $run)
        GROUP BY ALL ORDER BY a.role, decisions DESC, decideur
    """,

    # ICC du moteur, pas à pas (un run).
    "serie_icc": f"""
        SELECT t_sim, arg_max(valeur, seq) AS valeur FROM indicateurs WHERE nom = 'icc' AND {RUN}
        GROUP BY t_sim ORDER BY t_sim
    """,

    # Décisions par heure simulée et par mode de décision (un run).
    "serie_decisions": f"""
        SELECT t_sim // 60 * 60 AS heure, {DECIDEUR} AS decideur, count(*) AS decisions
        FROM decisions WHERE {RUN} GROUP BY ALL ORDER BY heure, decideur
    """,

    # Peur et estimation des autres renvoyées par les LLM, moyennées par heure simulée (un run).
    "serie_peur": f"""
        SELECT t_sim // 60 * 60 AS heure, avg(peur) AS peur, avg(estime_autres) AS estime_autres, count(*) AS reponses
        FROM croyances WHERE cause = 'reflexion' AND {RUN} GROUP BY ALL ORDER BY heure
    """,

    # Calibration du Système 1 : par tranche de probabilité de retrait annoncée, la part de retraits décidés.
    # Une décision prise seule par le Système 1 n'a pas d'avis du LLM : elle compte avec sa propre action.
    "calibration_s1": f"""
        WITH s AS (SELECT TRY_CAST(systeme1->>'p_retirer' AS DOUBLE) AS p, action, type
                   FROM decisions WHERE {RUN})
        SELECT least(floor(p * 5) / 5, 0.8) AS tranche_min, count(*) AS decisions, avg(p) AS p_annoncee,
               avg((action = 'retirer')::INT) AS part_retrait_reelle,
               count(*) FILTER (WHERE type = 'systeme1') AS decide_seul
        FROM s WHERE p IS NOT NULL GROUP BY ALL ORDER BY tranche_min
    """,

    # Valeurs de peur écrites par les LLM : une vraie mesure continue n'aurait pas de pics sur les nombres ronds.
    "peur_declaree": f"""
        SELECT round(peur, 2) AS peur, count(*) AS reponses,
               abs(round(peur, 2) * 20 - round(round(peur, 2) * 20)) < 1e-6 AS multiple_de_005
        FROM croyances WHERE cause = 'reflexion' AND {RUN} GROUP BY ALL ORDER BY peur
    """,

    # Ancrage : corrélation entre la peur donnée au LLM dans le prompt (peur_avant) et celle qu'il renvoie.
    "ancrage": """
        SELECT a.modele, a.tier, count(*) AS reponses, corr(c.peur, c.peur_avant) AS correlation_peur,
               avg(c.peur - c.peur_avant) AS variation_moyenne, avg(c.peur) AS peur_moyenne
        FROM croyances c JOIN agents a USING (run_id, agent_id)
        WHERE c.cause = 'reflexion' AND ($run IS NULL OR c.run_id = $run)
        GROUP BY ALL ORDER BY correlation_peur DESC NULLS LAST, a.modele
    """,

    # Les points de l'ancrage : pour chaque réflexion, la peur rappelée dans le prompt et celle renvoyée.
    "ancrage_points": """
        SELECT a.modele, c.peur_avant, c.peur
        FROM croyances c JOIN agents a USING (run_id, agent_id)
        WHERE c.cause = 'reflexion' AND c.peur_avant IS NOT NULL AND ($run IS NULL OR c.run_id = $run)
        ORDER BY c.run_id, c.seq LIMIT 3000
    """,

    # Fiabilité technique par modèle : un échec devient un repli sur la règle, une latence longue un retard.
    "fiabilite_modeles": f"""
        WITH l AS (SELECT modele, any_value(tier) AS tier, any_value(fournisseur) AS fournisseur, count(*) AS appels,
                          count(*) FILTER (WHERE statut = 'ok') AS reussis,
                          count(*) FILTER (WHERE statut = 'quota') AS quota,
                          count(*) FILTER (WHERE statut NOT IN ('ok', 'quota')) AS erreurs,
                          median(latence_ms) FILTER (WHERE statut = 'ok') AS latence_mediane_ms,
                          quantile_cont(latence_ms, 0.95) FILTER (WHERE statut = 'ok') AS latence_p95_ms,
                          sum(tokens_entree + tokens_sortie) AS tokens
                   FROM llm_appels WHERE {RUN} GROUP BY modele),
             d AS (SELECT a.modele, count(*) AS decisions, avg((d.action = 'retirer')::INT) AS part_retrait
                   FROM decisions d JOIN agents a USING (run_id, agent_id)
                   WHERE d.type = 'llm' AND a.role = 'deposant' AND ($run IS NULL OR d.run_id = $run) GROUP BY 1)
        SELECT l.*, l.reussis / l.appels AS taux_reussite, d.decisions, d.part_retrait
        FROM l LEFT JOIN d USING (modele) ORDER BY appels DESC, modele
    """,

    # Décisions des institutions (direction, employés) : justification, communiqué, ICC avant et 4 h après.
    "decisions_majeures": """
        WITH i AS (SELECT run_id, t_sim, seq, valeur FROM indicateurs WHERE nom = 'icc')
        SELECT d.event_id, d.run_id, d.t_sim, a.role, a.nom, a.modele, d.type, d.action, d.justification,
               cm.ton, cm.texte AS communique, cm.credibilite,
               (SELECT arg_max(valeur, seq) FROM i WHERE i.run_id = d.run_id AND i.t_sim <= d.t_sim) AS icc_avant,
               (SELECT arg_max(valeur, seq) FROM i WHERE i.run_id = d.run_id AND i.t_sim <= d.t_sim + 240) AS icc_4h_apres
        FROM decisions d JOIN agents a USING (run_id, agent_id)
        LEFT JOIN communiques cm ON cm.run_id = d.run_id AND cm.t_sim = d.t_sim AND d.action = 'publier_communique'
        WHERE a.role <> 'deposant' AND ($run IS NULL OR d.run_id = $run)
        ORDER BY d.run_id, d.seq
    """,

    # Les dernières décisions, pour le fil et les filtres de la page.
    "fil_decisions": """
        SELECT d.event_id, d.run_id, d.t_sim, d.agent_id, a.nom, a.role, a.modele, d.type, d.action, d.voie,
               d.justification, TRY_CAST(d.systeme1->>'p_retirer' AS DOUBLE) AS s1_p_retirer
        FROM decisions d LEFT JOIN agents a USING (run_id, agent_id)
        WHERE ($run IS NULL OR d.run_id = $run)
        ORDER BY d.run_id DESC, d.seq DESC LIMIT 300
    """,
}

# Comparaison des runs : uniquement dans l'historique (la table runs vient des résumés de fin de run).
COMPARAISON = """
    SELECT run_id, config, etiquette, mode, deposants, llm_actif, systeme1, defaut, part_retiree, icc_max,
           appels_llm, replis, decisions_llm, decisions_en_retard, latence_mediane_ms, interrompu
    FROM runs ORDER BY run_id DESC
"""

# Fiche d'une décision : ce qui l'a causée, l'appel LLM qui l'a produite et ce qu'elle a changé.
FICHE = {
    "decision": """
        SELECT d.*, a.nom, a.role, a.modele, a.tier, a.profil
        FROM decisions d LEFT JOIN agents a USING (run_id, agent_id) WHERE d.event_id = $id
    """,
    "appels": """
        SELECT l.* FROM llm_appels l JOIN decisions d ON d.run_id = l.run_id AND d.call_id = l.call_id
        WHERE d.event_id = $id ORDER BY l.essai
    """,
    "causes": """
        SELECT e.event_id, e.type, e.t_sim, e.payload
        FROM decisions d, evenements e
        WHERE d.event_id = $id AND e.run_id = d.run_id AND list_contains(d.causes, e.event_id) ORDER BY e.seq
    """,
    "effets": """
        SELECT e.event_id, e.type, e.t_sim, e.payload
        FROM decisions d, evenements e
        WHERE d.event_id = $id AND e.run_id = d.run_id AND list_contains(e.causes, d.event_id) ORDER BY e.seq
    """,
}


def _valeur(type_sql: str, v):
    if isinstance(v, float) and not math.isfinite(v):
        return None                      # corrélation d'une série constante : NaN, refusé par JSON
    if type_sql == "JSON" and isinstance(v, str):
        return json.loads(v)             # DuckDB rend une colonne JSON en texte
    return v


def lignes(cur) -> list[dict]:
    """Résultat d'une requête DuckDB -> liste de dictionnaires prêts pour JSON."""
    cols = [(c[0], str(c[1])) for c in cur.description]
    return [{nom: _valeur(t, v) for (nom, t), v in zip(cols, r)} for r in cur.fetchall()]


def calculer(con, run: str | None, noms=None) -> dict[str, list[dict]]:
    """Toutes les métriques (ou celles de `noms`) pour un run, ou pour tous si run est None."""
    return {nom: lignes(con.execute(METRIQUES[nom], {"run": run})) for nom in (noms or METRIQUES)}


def comparaison(con) -> list[dict]:
    return lignes(con.execute(COMPARAISON))


def fiche(con, event_id: str) -> dict:
    out = {nom: lignes(con.execute(sql, {"id": event_id})) for nom, sql in FICHE.items()}
    out["decision"] = out["decision"][0] if out["decision"] else None
    return out
