"""Archiveur : l'historique des runs en Parquet, interrogeable en SQL avec DuckDB (F3, F5).

    python -m services.archiveur                      # archive les runs qui ne le sont pas encore
    python -m services.archiveur --run <run_id>       # (ré)archive un run
    python -m services.archiveur --forcer             # réarchive tous les runs
    python -m services.archiveur --sql requetes/modele_unique.sql
    python -m services.archiveur --requete "SELECT config, count(*) FROM runs GROUP BY config"

Chaque run terminé a son dossier data/runs/<run_id>/ ; l'archiveur y ajoute archive/<table>.parquet. Un run
interrompu (sans resume.json, inactif depuis 10 min) est d'abord résumé par services/reconstitution.py, puis
archivé avec runs.interrompu = true.
La base data/archive.duckdb ne contient que des **vues** sur ces fichiers : elle se reconstruit à tout
moment, et une requête rejouée sur les mêmes runs donne le même résultat (F5).

DuckDB lit lui-même events.jsonl et extrait les colonnes en SQL (une insertion ligne par ligne depuis
Python prenait plus de dix minutes pour une vingtaine de runs).
"""
import argparse
import json
import sys
from pathlib import Path

from engine.config import ROOT

RUNS = ROOT / "data" / "runs"
BASE = ROOT / "data" / "archive.duckdb"

ENVELOPPE = ("{event_id: 'VARCHAR', type: 'VARCHAR', version: 'INTEGER', run_id: 'VARCHAR', seq: 'INTEGER', "
             "t_sim: 'INTEGER', t_emit: 'VARCHAR', t_proc: 'VARCHAR', producer: 'VARCHAR', causes: 'VARCHAR[]', "
             "payload: 'JSON'}")


def _t(champ: str, type_sql: str = "VARCHAR") -> str:
    if type_sql == "VARCHAR":
        return f"payload->>'{champ}' AS {champ}"
    if type_sql == "LISTE":
        return f"from_json(payload->'{champ}', '[\"VARCHAR\"]') AS {champ}"
    if type_sql == "JSON":
        return f"payload->'{champ}' AS {champ}"
    return f"TRY_CAST(payload->>'{champ}' AS {type_sql}) AS {champ}"


BASE_COLS = "run_id, t_sim, seq"

# table -> (type d'événement ou None pour tous, colonnes SQL)
TABLES = {
    "evenements": (None, ["event_id", "type", "producer AS producteur", "t_emit", "causes", "payload"]),
    "decisions": ("decision.made", ["event_id", _t("agent_id"), _t("type"), _t("action"), _t("regle_id"),
                                    _t("justification"), _t("t_sim_contexte", "INTEGER"), _t("call_id"),
                                    _t("souvenirs", "LISTE"), _t("documents", "JSON"), _t("voie"),
                                    _t("systeme1", "JSON"), "causes"]),
    "llm_appels": ("llm.called", [_t("call_id"), _t("agent_id"), _t("usage"), _t("fournisseur"), _t("modele"),
                                  _t("tier"), _t("tokens_entree", "INTEGER"), _t("tokens_sortie", "INTEGER"),
                                  _t("latence_ms", "INTEGER"), _t("statut"), _t("essai", "INTEGER")]),
    "conversations": ("conversation.held", ["event_id", _t("lieu"), _t("locuteur_id"), _t("auditeur_id"),
                                            _t("phrase"), _t("reponse"), _t("mode"), _t("call_id")]),
    "croyances": ("belief.updated", ["event_id", _t("agent_id"), _t("peur", "DOUBLE"), _t("estime_autres", "DOUBLE"),
                                     _t("peur_avant", "DOUBLE"), _t("estime_autres_avant", "DOUBLE"), _t("cause"),
                                     "causes"]),
    "indicateurs": ("indicator.computed", ["producer AS producteur", _t("nom"), _t("valeur", "DOUBLE"),
                                           _t("composantes", "JSON"), _t("sources", "LISTE"),
                                           _t("retard_ms", "INTEGER")]),
    "retraits": ("withdrawal.served", [_t("agent_id"), _t("agence_id"), _t("demande", "DOUBLE"), _t("paye", "DOUBLE"),
                                       _t("perdu", "DOUBLE"), _t("garanti", "DOUBLE")]),
    "souvenirs": ("memory.written", [_t("agent_id"), _t("memoire_id"), _t("nature"), _t("texte"),
                                     _t("importance", "DOUBLE"), _t("sources", "LISTE")]),
    "communiques": ("communique.published", [_t("auteur"), _t("ton"), _t("texte"), _t("credibilite", "DOUBLE"),
                                             _t("doc_id")]),
    "incidents": ("incident.raised", [_t("nature"), _t("agent_id"), _t("call_id"), _t("detail"), _t("repli")]),
    "violations": ("temporal.violation", [_t("agent_id"), _t("decision_event_id"), _t("element_id"),
                                          _t("t_sim_element", "INTEGER"), _t("t_sim_contexte", "INTEGER"),
                                          _t("raison")]),
}

AGENTS = """
SELECT ev.run_id, a->>'agent_id' AS agent_id, a->>'nom' AS nom, a->>'role' AS role, a->>'type' AS type,
       a->>'tier' AS tier, a->>'modele' AS modele, a->>'fournisseur' AS fournisseur, a->>'profil' AS profil,
       TRY_CAST(a->>'depot' AS DOUBLE) AS depot, TRY_CAST(a->>'connecte' AS BOOLEAN) AS connecte,
       a->>'quartier' AS quartier
FROM ev, unnest(from_json(payload->'attribution', '["JSON"]')) AS u(a)
WHERE type = 'run.started'
"""


def archiver_run(dossier: Path, con=None) -> bool:
    """Écrit dossier/archive/*.parquet. Renvoie False si le run n'est pas terminé (pas de resume.json)."""
    import duckdb
    ev_f, resume_f = dossier / "events.jsonl", dossier / "resume.json"
    if not resume_f.exists() or not ev_f.exists() or ev_f.stat().st_size == 0:
        return False
    con = con or duckdb.connect()
    sortie = dossier / "archive"
    sortie.mkdir(exist_ok=True)
    con.execute("DROP TABLE IF EXISTS ev")
    con.execute(f"CREATE TEMP TABLE ev AS SELECT * FROM read_json('{ev_f.as_posix()}', format = 'newline_delimited', "
                f"columns = {ENVELOPPE}, maximum_object_size = 104857600)")
    for nom, (type_, colonnes) in TABLES.items():
        filtre = f"WHERE type = '{type_}'" if type_ else ""
        con.execute(f"COPY (SELECT {BASE_COLS}, {', '.join(colonnes)} FROM ev {filtre} ORDER BY seq) "
                    f"TO '{(sortie / f'{nom}.parquet').as_posix()}' (FORMAT parquet)")
    con.execute(f"COPY ({AGENTS}) TO '{(sortie / 'agents.parquet').as_posix()}' (FORMAT parquet)")
    _ecrire_run(con, dossier, sortie)
    return True


# Latence des appels LLM et retard d'application des décisions LLM, mesurés sur l'historique du run.
# json_extract_string plutôt que ->> : combiné à une soustraction, ->> est mal analysé par DuckDB.
MESURES = """
WITH llm AS (SELECT TRY_CAST(json_extract_string(payload, '$.latence_ms') AS INTEGER) AS latence
             FROM ev WHERE type = 'llm.called'),
     dec AS (SELECT t_sim - TRY_CAST(json_extract_string(payload, '$.t_sim_contexte') AS INTEGER) AS retard,
                    json_extract_string(payload, '$.voie') AS voie
             FROM ev WHERE type = 'decision.made' AND json_extract_string(payload, '$.type') = 'llm')
SELECT (SELECT median(latence) FROM llm) AS latence_mediane_ms,
       (SELECT quantile_cont(latence, 0.95) FROM llm) AS latence_p95_ms,
       (SELECT count(*) FROM dec WHERE voie = 'en_retard') AS decisions_en_retard,
       (SELECT avg(retard) FROM dec) AS retard_moyen_min,
       (SELECT max(retard) FROM dec) AS retard_max_min
"""


def _ecrire_run(con, dossier: Path, sortie: Path) -> None:
    r = json.loads((dossier / "resume.json").read_text(encoding="utf-8"))
    cfg_f = dossier / "config.json"
    cfg = json.loads(cfg_f.read_text(encoding="utf-8")) if cfg_f.exists() else {}
    llm, rag = r.get("llm") or {}, r.get("rag") or {}
    s1 = llm.get("systeme1") or {}
    mesures = dict(zip(("latence_mediane_ms", "latence_p95_ms", "decisions_en_retard", "retard_moyen_min",
                        "retard_max_min"), con.execute(MESURES).fetchone()))
    ligne = {
        "run_id": dossier.name, "config": cfg.get("nom") or r.get("config_nom"), "graine": r.get("graine"),
        "mode": cfg.get("mode") or r.get("mode"), "etiquette": r.get("etiquette"),
        "interrompu": bool(r.get("interrompu")),
        "deposants": cfg.get("population", {}).get("deposants"), "llm_actif": bool(cfg.get("llm", {}).get("actif")),
        "rag_actif": bool(cfg.get("rag", {}).get("actif")) and bool(cfg.get("llm", {}).get("actif")),
        **{k: r.get(k) for k in ("fin", "t_fin", "defaut", "t_defaut", "solvable", "part_retiree", "file_max",
                                 "icc_max", "emergence", "t_emergence", "conversations", "communiques",
                                 "evenements", "rejets_dlq", "duree_reelle_s")},
        "appels_llm": llm.get("appels", 0), "tokens_entree": llm.get("tokens_entree", 0),
        "tokens_sortie": llm.get("tokens_sortie", 0), "replis": llm.get("replis", 0),
        "decisions_llm": llm.get("decisions_llm", 0),
        "souvenirs": rag.get("souvenirs", 0), "violations_c6": rag.get("violations_c6", 0),
        "systeme1": s1.get("moteur") or (cfg.get("systeme1", {}).get("moteur") if llm else None) or "aucun",
        "s1_evaluations": s1.get("evaluations"), "s1_decide_seul": s1.get("decide_seul"),
        "s1_escalades_retrait": (s1.get("escalades") or {}).get("retrait"),
        "s1_escalades_ambigu": (s1.get("escalades") or {}).get("ambigu"),
        "s1_reportees": s1.get("reportees"),
        "s1_retraits_confirmes": (s1.get("verdicts_llm") or {}).get("retrait_confirme"),
        "s1_retraits_infirmes": (s1.get("verdicts_llm") or {}).get("retrait_infirme"),
        **mesures,
    }
    tmp = sortie / "_run.json"
    tmp.write_text(json.dumps(ligne, ensure_ascii=False), encoding="utf-8")
    # Types fixés : une colonne vide dans un run (pas de défaut, pas de LLM) serait lue comme JSON, et la vue
    # sur tous les runs ne pourrait plus en faire la moyenne.
    colonnes = ", ".join(f"TRY_CAST({k} AS {TYPES_RUNS.get(k, 'VARCHAR')}) AS {k}" for k in ligne)
    con.execute(f"COPY (SELECT {colonnes} FROM read_json('{tmp.as_posix()}')) "
                f"TO '{(sortie / 'runs.parquet').as_posix()}' (FORMAT parquet)")
    tmp.unlink()


TYPES_RUNS = {
    **{k: "BOOLEAN" for k in ("interrompu", "llm_actif", "rag_actif", "defaut", "solvable", "emergence")},
    **{k: "DOUBLE" for k in ("part_retiree", "icc_max", "duree_reelle_s", "latence_mediane_ms", "latence_p95_ms",
                             "retard_moyen_min")},
    **{k: "BIGINT" for k in ("graine", "deposants", "t_fin", "t_defaut", "file_max", "t_emergence", "conversations",
                             "communiques", "evenements", "rejets_dlq", "appels_llm", "tokens_entree",
                             "tokens_sortie", "replis", "decisions_llm", "souvenirs", "violations_c6",
                             "s1_evaluations", "s1_decide_seul", "s1_escalades_retrait", "s1_escalades_ambigu",
                             "s1_reportees", "s1_retraits_confirmes", "s1_retraits_infirmes",
                             "decisions_en_retard", "retard_max_min")},
}


def ouvrir(base: Path = BASE, runs: Path = RUNS):
    """Base DuckDB dont chaque table est une vue sur les Parquet de tous les runs archivés."""
    import duckdb
    con = duckdb.connect(str(base))
    for nom in [*TABLES, "agents", "runs"]:
        if any(runs.glob(f"*/archive/{nom}.parquet")):
            motif = (runs / "*" / "archive" / f"{nom}.parquet").as_posix()
            con.execute(f"CREATE OR REPLACE VIEW {nom} AS SELECT * FROM read_parquet('{motif}', union_by_name = true)")
    return con


def archiver_tout(runs: Path = RUNS, forcer: bool = False) -> list[str]:
    """Archive les runs terminés, et d'abord les runs interrompus, dont on reconstitue le résumé."""
    import duckdb
    from services.reconstitution import reconstituer_tout
    reconstituer_tout(runs)
    con = duckdb.connect()
    faits = []
    for d in sorted(p for p in runs.iterdir() if p.is_dir()):
        if not forcer and (d / "archive" / "runs.parquet").exists():
            continue
        if archiver_run(d, con):
            faits.append(d.name)
    return faits


def executer_sql(con, texte: str) -> list:
    """Exécute chaque requête d'un texte SQL (séparées par « ; ») ; renvoie les relations des SELECT."""
    out = []
    for q in texte.split(";"):
        utile = "\n".join(l for l in q.splitlines() if not l.strip().startswith("--")).strip()
        if utile:
            out.append(con.sql(utile))
    return out


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Archive des runs (Parquet + DuckDB)")
    ap.add_argument("--run", help="archiver (ou réarchiver) ce run")
    ap.add_argument("--forcer", action="store_true", help="réarchiver tous les runs")
    ap.add_argument("--sql", help="fichier .sql à exécuter sur l'archive")
    ap.add_argument("--requete", help="requête SQL à exécuter sur l'archive")
    a = ap.parse_args(argv)
    if a.run:
        print("archivé" if archiver_run(RUNS / a.run) else "run inconnu ou pas terminé")
    else:
        faits = archiver_tout(forcer=a.forcer)
        print(f"{len(faits)} run(s) archivé(s)")
    if a.sql or a.requete:
        con = ouvrir()
        for rel in executer_sql(con, Path(a.sql).read_text(encoding="utf-8") if a.sql else a.requete):
            if rel is not None:
                rel.show(max_width=220, max_rows=80)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
