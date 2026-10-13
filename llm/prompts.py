"""Prompts des agents LLM et validation de leurs réponses (A4).

Un agent ne reçoit que ce qu'il a lui-même appris pendant le run (son carnet), daté : jamais l'état
interne de la Caisse ni ce que pensent les autres (C6). Le carnet est une mémoire de court terme ; la
mémoire longue et le RAG arrivent à la semaine 6.

Chaque validateur renvoie (données | None, statut, détail), avec statut parmi ok, json_invalide, hors_role.
"""
import json
import re

PROFILS = {
    "connecte": "Tu suis l'actualité en ligne et sur les réseaux sociaux, et tu réagis vite aux nouvelles.",
    "fidele": "Tu es client de la Caisse depuis longtemps et tu lui fais confiance.",
    "commercant": "Tu tiens un commerce : ta trésorerie professionnelle est à la Caisse et tu en as besoin "
                  "pour payer tes fournisseurs.",
    "prudent": "Tu es un épargnant prudent : tu as mis de côté avec peine et tu ne veux rien perdre.",
}

ACTIONS_DEPOSANT = {"retirer", "attendre", "rien"}
ACTIONS_GUICHET = {"repondre", "eluder"}
ACTIONS_DIRECTION = {"publier_communique", "rien"}
TONS = {"rassurant", "factuel", "vague"}


def montant(v: float) -> str:
    return f"{int(round(v)):,}".replace(",", " ") + " unités"


def extraire_json(texte: str):
    """Le premier objet JSON du texte, même entouré de ``` ou de phrases. Lève ValueError sinon."""
    t = texte.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    debut, fin = t.find("{"), t.rfind("}")
    if debut < 0 or fin <= debut:
        raise ValueError("aucun objet JSON")
    return json.loads(t[debut:fin + 1])


def _nombre(d: dict, k: str) -> float:
    v = d.get(k)
    if isinstance(v, str):
        v = float(v.replace(",", "."))
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not 0.0 <= float(v) <= 1.0:
        raise ValueError(f"« {k} » doit être un nombre entre 0 et 1")
    return float(v)


def _texte(d: dict, k: str, longueur: int, requis: bool = True) -> str:
    v = d.get(k)
    if v is None and not requis:
        return ""
    if not isinstance(v, str) or not v.strip():
        raise ValueError(f"« {k} » doit être un texte non vide")
    v = " ".join(v.split())
    return v if len(v) <= longueur else v[:longueur - 1].rstrip() + "…"


def _valider(texte: str, actions: set, champs) -> tuple:
    try:
        d = extraire_json(texte)
        if not isinstance(d, dict):
            raise ValueError("la réponse n'est pas un objet")
    except (ValueError, TypeError) as e:
        return None, "json_invalide", str(e)[:120]
    action = str(d.get("action", "")).strip().lower()
    if action not in actions:
        return None, "hors_role", f"action « {action} » hors du rôle (attendu : {', '.join(sorted(actions))})"
    try:
        out = {"action": action, **champs(d)}
    except (ValueError, TypeError) as e:
        return None, "json_invalide", str(e)[:120]
    return out, "ok", ""


# ================================================================ déposant

def _phrase(t: str) -> str:
    t = " ".join((t or "").split())
    return t if not t or t.endswith((".", "!", "?")) else t + "."


def systeme_deposant(nom: str | None, profil: str, persona: str | None, depot: float) -> str:
    """nom : celui d'un personnage principal ; None pour un déposant ordinaire."""
    qui = _phrase(persona or PROFILS.get(profil, ""))
    moi = f"Tu incarnes {nom}, qui vit à Brévanne" if nom else "Tu vis à Brévanne"
    return (f"{moi}, une petite vallée fictive. {qui} "
            f"Tu as {montant(depot)} déposés à la Caisse Coopérative de Brévanne, la banque locale. "
            "Comme tout le monde, tu sais qu'une banque ne garde en caisse qu'une partie des dépôts : si beaucoup "
            "de clients retirent en même temps, les premiers sont servis et les derniers risquent de ne rien "
            "récupérer. "
            "Tu raisonnes comme cette personne, avec ses intérêts et ses émotions, uniquement à partir de ce "
            "que tu sais. Tu ne connais pas les comptes de la Caisse ni les pensées des autres. "
            "Tu réponds en français, uniquement par un objet JSON.")


def _rappels(lignes: list, souvenirs, documents) -> None:
    if souvenirs:
        lignes.append("")
        lignes.append("Des souvenirs plus anciens te reviennent :")
        lignes.extend(f"- {quand} : {quoi}" for quand, quoi in souvenirs)
    if documents:
        lignes.append("")
        lignes.append("Ce que l'on sait à Brévanne, et qui te revient à l'esprit :")
        lignes.extend(f"- {titre} : {' '.join(texte.split())}" for titre, texte in documents)


def utilisateur_deposant(moment: str, agences_ouvertes: bool, garantie: bool, plafond_garantie: float,
                         plafond_retrait: float, carnet: list[tuple[str, str]], peur: float, estime: float,
                         souvenirs=(), documents=()) -> str:
    lignes = [f"Nous sommes {moment}. Les agences de la Caisse sont {'ouvertes' if agences_ouvertes else 'fermées'} "
              "en ce moment (elles ouvrent de 9 h à 17 h, du lundi au samedi)."]
    if garantie:
        lignes.append(f"Une garantie publique couvre les dépôts jusqu'à {montant(plafond_garantie)} par personne.")
    else:
        lignes.append("Aucune garantie publique ne couvre les dépôts.")
    if plafond_retrait < 1.0:
        lignes.append(f"La Caisse limite chaque retrait à {plafond_retrait:.0%} du dépôt par passage au guichet.")
    lignes.append("")
    lignes.append("Ce que tu as appris, du plus ancien au plus récent :")
    if carnet:
        lignes.extend(f"- {quand} : {quoi}" for quand, quoi in carnet)
    else:
        lignes.append("- rien de particulier.")
    _rappels(lignes, souvenirs, documents)
    lignes.append("")
    lignes.append(f"Ton ressenti jusqu'ici : inquiétude {peur:.2f} (0 = aucune, 1 = panique) ; part des autres "
                  f"déposants qui vont retirer leur argent, selon toi : {estime:.2f}. Ces nombres résument ton "
                  "état d'esprit : ne les recopie pas dans ta justification ni dans ta phrase.")
    lignes.append("")
    lignes.append("Décide maintenant ce que tu fais. Réponds avec ce JSON :")
    lignes.append('{"action": "retirer" ou "attendre" ou "rien", "peur": nombre de 0 à 1, '
                  '"estime_autres": nombre de 0 à 1, "justification": "1 ou 2 phrases à la première personne, '
                  'qui citent ce que tu sais", "phrase": "ce que tu dirais à un voisin en le croisant, une phrase courte"}')
    lignes.append("retirer = aller à l'agence retirer tout ton argent dès qu'elle est ouverte ; "
                  "attendre = rester vigilant sans agir ; rien = continuer comme d'habitude.")
    return "\n".join(lignes)


def valider_deposant(texte: str) -> tuple:
    return _valider(texte, ACTIONS_DEPOSANT, lambda d: {
        "peur": _nombre(d, "peur"), "estime_autres": _nombre(d, "estime_autres"),
        "justification": _texte(d, "justification", 300), "phrase": _texte(d, "phrase", 160)})


# ================================================================ employé d'agence, au guichet

def systeme_employe(nom: str, persona: str) -> str:
    return (f"Tu incarnes {nom}, employé au guichet de l'Agence du Bourg de la Caisse Coopérative de Brévanne, "
            f"une banque locale d'une vallée fictive. {_phrase(persona)} Tu réponds en français, uniquement par un objet JSON.")


def utilisateur_employe(moment: str, consigne: str, solvable: bool, part_retiree: float, file: int,
                        question: str, rappels=()) -> str:
    etat = ("Tu sais que les comptes sont sains : l'emprunteur en difficulté ne pèse qu'une petite part des prêts."
            if solvable else "Tu sais que les pertes sur le prêt de l'emprunteur en difficulté sont réelles.")
    consignes = {"silence": "La direction t'a demandé de ne rien dire sur la situation de la Caisse.",
                 "transparence": "La direction t'a demandé de répondre franchement aux clients."}
    souvenirs = ["", "Ce qui te revient à l'esprit :"] + [f"- {q} : {t}" for q, t in rappels] if rappels else []
    return "\n".join([
        f"Nous sommes {moment}. {etat}",
        f"Depuis le début de la semaine, {part_retiree:.0%} des dépôts ont été retirés. "
        f"{file} personne(s) attendent derrière ce client.",
        consignes.get(consigne, consignes["silence"]) + " Tu peux t'en écarter si ta conscience te le dicte.",
        *souvenirs,
        "",
        f"Un client te demande : « {question} »",
        "",
        'Réponds avec ce JSON : {"action": "repondre" ou "eluder", "reponse": "ce que tu dis au client, '
        '1 ou 2 phrases", "justification": "pourquoi tu réponds ainsi, 1 phrase"}',
    ])


def valider_employe(texte: str) -> tuple:
    return _valider(texte, ACTIONS_GUICHET, lambda d: {
        "reponse": _texte(d, "reponse", 240), "justification": _texte(d, "justification", 240)})


# ================================================================ direction

def systeme_direction(nom: str, persona: str) -> str:
    return (f"Tu incarnes {nom} de la Caisse Coopérative de Brévanne, une banque locale d'une vallée fictive. "
            f"{_phrase(persona)} Tu réponds en français, uniquement par un objet JSON.")


def utilisateur_direction(moment: str, article: str, reserves: float, reserves_depart: float, part_retiree: float,
                          file: int, solvable: bool, garantie: bool, dernier_communique: str | None,
                          rappels=()) -> str:
    souvenirs = ["", "Ce qui te revient à l'esprit :"] + [f"- {q} : {t}" for q, t in rappels] if rappels else []
    return "\n".join([
        f"Nous sommes {moment}. La presse a publié : « {article} »",
        f"Réserves en caisse : {montant(reserves)} sur {montant(reserves_depart)} au départ. "
        f"{part_retiree:.0%} des dépôts ont déjà été retirés. {file} personne(s) font la queue aux guichets.",
        "La Caisse est solvable : l'emprunteur ne représente qu'une part limitée des prêts." if solvable
        else "Les pertes sur le prêt de l'emprunteur sont réelles : la Caisse est fragilisée.",
        "Une garantie publique des dépôts est en vigueur." if garantie else "Aucune garantie publique des dépôts.",
        f"Dernier communiqué : {dernier_communique}" if dernier_communique else "Aucun communiqué publié pour l'instant.",
        *souvenirs,
        "",
        "Décides-tu de publier un communiqué maintenant ? Un communiqué maladroit peut aggraver la panique.",
        'Réponds avec ce JSON : {"action": "publier_communique" ou "rien", "ton": "rassurant" ou "factuel" ou '
        '"vague" (si tu publies), "texte": "le communiqué, 1 à 3 phrases (si tu publies)", '
        '"justification": "ton raisonnement, 1 ou 2 phrases"}',
    ])


def valider_direction(texte: str) -> tuple:
    def champs(d):
        out = {"justification": _texte(d, "justification", 300)}
        if str(d.get("action", "")).strip().lower() == "publier_communique":
            ton = str(d.get("ton", "")).strip().lower()
            if ton not in TONS:
                raise ValueError(f"ton « {ton} » inconnu (rassurant, factuel ou vague)")
            out["ton"], out["texte"] = ton, _texte(d, "texte", 400)
        return out
    return _valider(texte, ACTIONS_DIRECTION, champs)
