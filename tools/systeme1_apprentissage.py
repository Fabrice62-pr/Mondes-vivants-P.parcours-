"""Système 1 appris : un petit classifieur entraîné à prédire la décision du LLM (« retirer » ou non) à partir
de ce que le déposant savait au moment de décider. Le Système 1 zero-shot (regle, nli) n'était pas fidèle au
LLM : regle filtrait les retraits que le LLM aurait décidés, et la probabilité de nli ne prédisait pas ses
décisions (mesures du 27/10/2026, docs/hypotheses.md H8).

    python tools/systeme1_apprentissage.py donnees      # data/systeme1/donnees.jsonl, depuis data/runs
    python tools/systeme1_apprentissage.py entrainer    # data/modeles/systeme1-appris/
    python tools/systeme1_apprentissage.py evaluer      # courbe « décidé seul / retraits du LLM manqués »

Données : chaque decision.made d'un déposant LLM (type llm) d'un run qui a gardé les souvenirs des agents
(memory.written, rag.actif). Le carnet au moment de la décision est reconstruit à partir des souvenirs ; les
croyances à partir des belief.updated publiés avant le pas de la décision.

Ce que l'agent ne savait pas encore est écarté (C6). Dans un pas, les décisions viennent après les
commandes, l'article, les conversations et la perception, mais avant le guichet et la direction : une note
datée du pas de la décision n'est gardée que si elle ne vient ni du guichet ni d'un communiqué de la
direction.

Découpage par run, jamais par décision (deux décisions d'un même run se ressemblent trop) : le test est fait
des runs « reference », où le LLM a été appelé à chaque fois que l'agent avait du nouveau, comme le Système 1
le sera.
"""
import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine.config import ROOT, charger_config  # noqa: E402
from engine.horloge import Horloge  # noqa: E402
from engine.systeme1 import MODELE_APPRIS, texte_s1  # noqa: E402  (le même texte que dans le moteur)
from llm import prompts  # noqa: E402

RUNS = ROOT / "data" / "runs"
DONNEES = ROOT / "data" / "systeme1" / "donnees.jsonl"
MODELE = ROOT / MODELE_APPRIS
BASE = "MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7"
NOTES = 10                                   # le carnet garde les 10 dernières notes (engine/agents.py)
NATURES_CARNET = {"observation", "conversation", "guichet"}   # « reflexion » : souvenir, pas une note du carnet


# ================================================================ données

def _config(dossier: Path, debut: dict) -> dict:
    f = dossier / "config.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    # Runs interrompus antérieurs au 27/10 : config.json n'était écrit qu'à la fin. La configuration de même
    # nom sert pour l'horloge et les personnages (inchangés depuis), pas pour les leviers du run.
    nom = debut.get("config_nom", "principal")
    return charger_config(f"{nom}.yaml" if nom == "principal" else f"scenarios/{nom}.yaml")


def exemples_du_run(dossier: Path) -> list[dict]:
    ev_f = dossier / "events.jsonl"
    if not ev_f.exists():
        return []
    lignes = []
    with ev_f.open(encoding="utf-8") as f:
        for ligne in f:
            try:
                lignes.append(json.loads(ligne))
            except ValueError:
                continue
    if not lignes or lignes[0]["type"] != "run.started":
        return []
    debut = lignes[0]["payload"]
    if not any(e["type"] == "memory.written" for e in lignes):
        return []                                  # sans souvenirs, le carnet ne peut pas être reconstruit
    cfg = _config(dossier, debut)
    horloge = Horloge(cfg["horloge"], cfg["routine"])
    personas = {p["id"]: p.get("persona") for p in cfg.get("personnages_principaux", [])}
    agents = {a["agent_id"]: a for a in debut["attribution"] if a["role"] == "deposant" and a["type"] == "llm"}
    leviers = {"garantie": cfg["leviers"]["garantie"], "plafond_garantie": cfg["leviers"]["plafond_garantie"],
               "plafond_retrait": cfg["leviers"]["plafond_retrait"]}
    leviers_t: list[tuple[int, dict]] = [(0, dict(leviers))]
    auteurs: dict[str, str] = {}                   # communique.published.event_id -> auteur
    notes: dict[str, list[tuple[int, str, str, list]]] = {a: [] for a in agents}   # (t, texte, nature, sources)
    croyances: dict[str, list[tuple[int, float, float]]] = {a: [] for a in agents}
    payes: dict[str, list[tuple[int, float]]] = {a: [] for a in agents}
    decisions = []
    for e in lignes:
        typ, p, t = e["type"], e["payload"], e["t_sim"]
        if typ == "communique.published":
            auteurs[e["event_id"]] = p["auteur"]
        elif typ == "command.issued" and p["commande"] in ("garantie", "plafond_retrait"):
            q = p["parametres"]
            if p["commande"] == "garantie":
                leviers["garantie"] = bool(q.get("active", True))
                leviers["plafond_garantie"] = float(q.get("plafond", leviers["plafond_garantie"]))
            else:
                leviers["plafond_retrait"] = min(max(float(q["valeur"]), 0.05), 1.0)
            leviers_t.append((t, dict(leviers)))
        elif typ == "memory.written" and p["agent_id"] in agents and p["nature"] in NATURES_CARNET:
            notes[p["agent_id"]].append((t, p["texte"], p["nature"], p.get("sources") or []))
        elif typ == "belief.updated" and p["agent_id"] in agents:
            croyances[p["agent_id"]].append((t, p["peur"], p["estime_autres"]))
        elif typ == "withdrawal.served" and p["agent_id"] in agents:
            payes[p["agent_id"]].append((t, p["paye"]))
        elif typ == "decision.made" and p["type"] == "llm" and p["agent_id"] in agents:
            decisions.append((p, t))
    out = []
    for p, t in decisions:
        aid, t0 = p["agent_id"], p["t_sim_contexte"]
        a = agents[aid]

        def connue(n):
            tn, _, nature, sources = n
            if tn < t0:
                return True
            return tn == t0 and nature != "guichet" and not any(auteurs.get(s) == "direction" for s in sources)

        carnet = [(horloge.libelle(n[0]), n[1]) for n in notes[aid] if connue(n)][-NOTES:]
        avant = [c for c in croyances[aid] if c[0] < t0]
        peur, estime = (avant[-1][1], avant[-1][2]) if avant else (0.0, 0.0)
        lev = [lv for tl, lv in leviers_t if tl <= t0][-1]
        depot = a["depot"] - sum(v for tp, v in payes[aid] if tp < t0)
        qui = personas.get(aid) or prompts.PROFILS.get(a["profil"], "")
        out.append({
            "run_id": dossier.name, "etiquette": debut.get("etiquette"), "agent_id": aid, "modele": a["modele"],
            "t_sim_contexte": t0, "retirer": p["action"] == "retirer", "peur": peur, "estime": estime,
            "texte": texte_s1(a["modele"], qui, horloge.libelle(t0), depot,
                              lev["plafond_garantie"] if lev["garantie"] else None, lev["plafond_retrait"],
                              carnet, peur, estime)})
    return out


def donnees() -> None:
    DONNEES.parent.mkdir(parents=True, exist_ok=True)
    n = pos = runs = 0
    with DONNEES.open("w", encoding="utf-8") as f:
        for d in sorted(p for p in RUNS.iterdir() if p.is_dir()):
            ex = exemples_du_run(d)
            if ex:
                runs += 1
            for x in ex:
                f.write(json.dumps(x, ensure_ascii=False) + "\n")
                n += 1
                pos += x["retirer"]
    print(f"{n} exemples ({pos} retraits, {pos / max(n, 1):.1%}) tirés de {runs} runs -> {DONNEES}")


def charger() -> list[dict]:
    return [json.loads(l) for l in DONNEES.open(encoding="utf-8")]


def decoupage(ex: list[dict], graine: int = 0) -> tuple[list, list, list]:
    """test : runs « reference » ; validation : des runs tirés au hasard jusqu'à réunir 15 % des retraits (les
    retraits sont rares : 15 % des runs pouvaient n'en contenir que 6) ; le reste : entraînement."""
    test = [x for x in ex if x["etiquette"] == "reference"]
    retraits: dict[str, int] = {}
    for x in ex:
        if x["etiquette"] != "reference":
            retraits[x["run_id"]] = retraits.get(x["run_id"], 0) + x["retirer"]
    autres = sorted(retraits)
    random.Random(graine).shuffle(autres)
    val_runs, n = set(), 0
    for r in autres:
        if n >= 0.15 * sum(retraits.values()):
            break
        val_runs.add(r)
        n += retraits[r]
    val = [x for x in ex if x["run_id"] in val_runs]
    train = [x for x in ex if x["etiquette"] != "reference" and x["run_id"] not in val_runs]
    return train, val, test


# ================================================================ entraînement et évaluation

def _modele(chemin: str, **k):
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(chemin)
    tok.truncation_side = "left"
    # float32 explicite : le modèle de base est enregistré en float16, et transformers 5 le charge tel quel ;
    # entraîné en float16, il divergeait (poids NaN dès la première époque, 27/10/2026).
    m = AutoModelForSequenceClassification.from_pretrained(chemin, dtype=torch.float32, **k)
    return tok, m.to("cuda" if torch.cuda.is_available() else "cpu")


def probabilites(tok, m, textes: list[str], lot: int = 32) -> list[float]:
    import torch
    m.eval()
    out = []
    with torch.inference_mode():
        for i in range(0, len(textes), lot):
            enc = tok(textes[i:i + lot], truncation=True, max_length=512, padding=True,
                      return_tensors="pt").to(m.device)
            with torch.autocast(m.device.type, dtype=torch.bfloat16, enabled=m.device.type == "cuda"):
                logits = m(**enc).logits.float()
            out += logits.softmax(-1)[:, 1].tolist()
    return out


def auc(y: list[bool], p: list[float]) -> float:
    """Aire sous la courbe ROC : probabilité qu'un retrait reçoive un score plus haut qu'un non-retrait."""
    pos = [s for s, v in zip(p, y) if v]
    neg = [s for s, v in zip(p, y) if not v]
    if not pos or not neg:
        return float("nan")
    rangs = sorted(range(len(p)), key=lambda i: p[i])
    r = [0.0] * len(p)
    i = 0
    while i < len(rangs):                          # rangs moyens en cas d'égalité
        j = i
        while j + 1 < len(rangs) and p[rangs[j + 1]] == p[rangs[i]]:
            j += 1
        for k in range(i, j + 1):
            r[rangs[k]] = (i + j) / 2 + 1
        i = j + 1
    somme = sum(r[i] for i, v in enumerate(y) if v)
    return (somme - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


# Grille fine en bas : les probabilités du classifieur sont tassées entre 0,02 et 0,05 (pondération des
# retraits à l'entraînement) ; une grille 0,02 / 0,05 sautait de « rien décidé seul » à « 30 % manqués ».
SEUILS = (0.02, 0.0225, 0.025, 0.0275, 0.03, 0.035, 0.04, 0.045, 0.05, 0.075, 0.1, 0.15, 0.2, 0.3, 0.5)


def courbe(y: list[bool], p: list[float], seuils=SEUILS) -> list[dict]:
    """Pour chaque seuil s : le Système 1 décide seul quand p < s (pas de retrait). Part décidée seule, et part
    des retraits du LLM qu'il aurait manqués (décidés « non » sans demander au LLM)."""
    n, pos = len(y), sum(y)
    out = []
    for s in seuils:
        seul = [v for v, q in zip(y, p) if q < s]
        out.append({"seuil": s, "decide_seul": len(seul) / max(n, 1), "retraits_manques": sum(seul) / max(pos, 1)})
    return out


def seuil_recommande(c: list[dict], manques_max: float) -> float:
    ok = [x for x in c if x["retraits_manques"] <= manques_max]
    return max(ok, key=lambda x: x["decide_seul"])["seuil"] if ok else min(x["seuil"] for x in c)


MANQUES_MAX = 0.05     # à 10 %, le seuil choisi en validation manquait 25 % des retraits sur le test (27/10/2026)


def entrainer(epoques: int = 4, lot: int = 8, accumulation: int = 2, lr: float = 2e-5,
              manques_max: float = MANQUES_MAX) -> None:
    import torch
    train, val, test = decoupage(charger())
    pos = sum(x["retirer"] for x in train)
    print(f"entraînement {len(train)} ({pos} retraits), validation {len(val)} "
          f"({sum(x['retirer'] for x in val)}), test {len(test)} ({sum(x['retirer'] for x in test)})")
    tok, m = _modele(BASE, num_labels=2, ignore_mismatched_sizes=True,
                     id2label={0: "non", 1: "retirer"}, label2id={"non": 0, "retirer": 1})
    # les retraits sont rares (~5 %) : ils pèsent plus dans la perte (au plus 10 fois)
    poids = torch.tensor([1.0, min((len(train) - pos) / max(pos, 1), 10.0)], device=m.device)
    perte = torch.nn.CrossEntropyLoss(weight=poids)
    opt = torch.optim.AdamW(m.parameters(), lr=lr, weight_decay=0.01)
    pas_total = epoques * (len(train) // (lot * accumulation) + 1)
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: min(1.0, s / max(1, 0.1 * pas_total)) * max(0.0, (pas_total - s) / pas_total))
    rng = random.Random(0)
    meilleur = -1.0
    for ep in range(epoques):
        m.train()
        rng.shuffle(train)
        for i in range(0, len(train), lot):
            b = train[i:i + lot]
            enc = tok([x["texte"] for x in b], truncation=True, max_length=512, padding=True,
                      return_tensors="pt").to(m.device)
            y = torch.tensor([int(x["retirer"]) for x in b], device=m.device)
            with torch.autocast(m.device.type, dtype=torch.bfloat16, enabled=m.device.type == "cuda"):
                logits = m(**enc).logits
            (perte(logits.float(), y) / accumulation).backward()
            if (i // lot + 1) % accumulation == 0:
                torch.nn.utils.clip_grad_norm_(m.parameters(), 1.0)
                opt.step()
                sched.step()
                opt.zero_grad()
        pv = probabilites(tok, m, [x["texte"] for x in val])
        a = auc([x["retirer"] for x in val], pv)
        print(f"époque {ep + 1} : AUC validation {a:.3f}", flush=True)
        if a > meilleur:
            meilleur = a
            MODELE.mkdir(parents=True, exist_ok=True)
            m.save_pretrained(MODELE)
            tok.save_pretrained(MODELE)
            c = courbe([x["retirer"] for x in val], pv)
            meta = {"base": BASE, "epoque": ep + 1, "auc_validation": round(a, 4), "courbe_validation": c,
                    "manques_max": manques_max, "seuil_recommande": seuil_recommande(c, manques_max),
                    "exemples": {"entrainement": len(train), "validation": len(val), "test": len(test)}}
            (MODELE / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"meilleure AUC de validation {meilleur:.3f} -> {MODELE}")


def evaluer(manques_max: float = MANQUES_MAX) -> None:
    """Évalue sur le test, et recalcule le seuil sur la validation (grille et plafond actuels)."""
    _, val, test = decoupage(charger())
    meta = json.loads((MODELE / "meta.json").read_text(encoding="utf-8"))
    tok, m = _modele(str(MODELE))
    cv = courbe([x["retirer"] for x in val], probabilites(tok, m, [x["texte"] for x in val]))
    meta.update(courbe_validation=cv, manques_max=manques_max, seuil_recommande=seuil_recommande(cv, manques_max))
    y = [x["retirer"] for x in test]
    p = probabilites(tok, m, [x["texte"] for x in test])
    pression = [0.4 * x["peur"] + 0.6 * x["estime"] for x in test]   # base : le ressenti seul, comme R-seuil
    print(f"test : {len(test)} décisions de {len({x['run_id'] for x in test})} runs « reference », "
          f"{sum(y)} retraits")
    print(f"AUC Système 1 appris {auc(y, p):.3f} ; base « ressenti seul » {auc(y, pression):.3f} ; hasard 0,500")
    print(f"seuil recommandé (validation, au plus {meta['manques_max']:.0%} de retraits manqués) : "
          f"{meta['seuil_recommande']}")
    print("seuil | décidé seul | retraits du LLM manqués")
    for x in courbe(y, p):
        print(f" {x['seuil']:.4f} |     {x['decide_seul']:6.1%} |   {x['retraits_manques']:6.1%}")
    meta["test"] = {"auc": round(auc(y, p), 4), "auc_ressenti_seul": round(auc(y, pression), 4),
                    "courbe": courbe(y, p)}
    (MODELE / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Système 1 appris : données, entraînement, évaluation")
    ap.add_argument("etape", choices=["donnees", "entrainer", "evaluer"])
    a = ap.parse_args(argv)
    {"donnees": donnees, "entrainer": entrainer, "evaluer": evaluer}[a.etape]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
