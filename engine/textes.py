"""Phrases des agents à règles (reprises et étendues de guichet/scripts/main.gd).
Les agents LLM produiront leurs propres phrases à partir de la semaine 6."""
import random

COMMUNIQUES = {
    "rassurant": "La Caisse est solide. Vos dépôts sont en sécurité.",
    "factuel": "La Caisse détient des réserves suffisantes et l'emprunteur cité ne représente qu'une part "
               "limitée de ses prêts.",
    "vague": "La direction suit la situation avec attention.",
}

_DEJA_RETIRE = ["J'ai retiré mon argent. Je ne serai pas le dernier.",
                "Il y avait déjà la queue à l'agence…",
                "Si tout le monde retire, il ne restera rien pour les derniers."]
_INQUIET = ["Tu as lu l'article ? Je n'y crois pas trop, mais bon…",
            "Moi je ne bouge pas. Mais si les voisins retirent…",
            "À l'agence, ils avaient l'air nerveux."]
_CALME = ["La Caisse a toujours tenu. Pas de panique.",
          "C'est un emprunteur qui a des soucis, pas la Caisse.",
          "Je garde confiance."]

QUESTIONS = ["Est-ce que mon argent est en sécurité ?",
             "C'est vrai, cette histoire d'emprunteur ?",
             "Il y a beaucoup de retraits en ce moment ?"]
REPONSES_ELUDEES = ["Je ne peux rien vous dire, monsieur, madame. Adressez-vous à la direction.",
                    "Nous n'avons pas d'information à communiquer pour l'instant.",
                    "Je ne suis pas autorisé à en parler."]
REPONSES_FRANCHES_SOLVABLE = ["La Caisse a des réserves et l'emprunteur ne pèse qu'une petite part des prêts.",
                              "Il y a plus de retraits que d'habitude, mais les comptes sont sains."]
REPONSES_FRANCHES_INSOLVABLE = ["Honnêtement, les pertes sur ce prêt sont réelles.",
                                "La situation est plus difficile qu'on ne le dit."]
FUITE_CAFE = "Entre nous, les retraits s'accumulent à l'agence…"


def phrase_pour(a, pression: float, rng: random.Random) -> str:
    if a.id == "boulangere" and pression > a.seuil * 0.7:
        return "Toute ma trésorerie est là-bas… je ne peux pas me permettre de la perdre."
    if a.id == "retraite":
        return "Quarante ans que je suis client. Je ne céderai pas."
    if a.id == "entrepreneur" and a.informe:
        return "Ça s'emballe en ligne. Je préfère sécuriser avant les autres."
    if a.retire or a.veut_retirer:
        pool = _DEJA_RETIRE
    elif pression / a.seuil > 0.6:
        pool = _INQUIET
    else:
        pool = _CALME
    return pool[rng.randrange(len(pool))]


def reaction_pour(pression: float, seuil: float) -> str:
    r = pression / seuil
    if r > 1.0:
        return "Tu as raison, je vais y aller aussi."
    if r > 0.6:
        return "Ça commence à m'inquiéter."
    return "Je ne crois pas que ce soit si grave."
