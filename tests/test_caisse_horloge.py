from engine.caisse import Caisse
from engine.config import charger_config
from engine.horloge import Horloge


def test_retrait_normal():
    c = Caisse(1000.0, 0.15, 0.08, 0.0)
    assert c.retirer(100.0) == 100.0
    assert c.reserves == 50.0 and c.depots == 900.0 and not c.defaut


def test_defaut_quand_la_demande_depasse_les_reserves():
    c = Caisse(1000.0, 0.15, 0.08, 0.0)
    assert c.retirer(200.0) == 150.0          # paie ce qui reste en caisse
    assert c.defaut and c.reserves == 0.0
    assert c.solvable()                        # panique autoréalisatrice : la Caisse était solvable
    assert c.retirer(10.0) == 0.0              # plus rien après le défaut


def test_pertes_reelles_rendent_insolvable():
    c = Caisse(1000.0, 0.15, 0.08, 0.5)
    assert c.solvable()
    c.realiser_perte()
    assert not c.solvable()


def test_horloge_calendrier():
    cfg = charger_config("principal.yaml")
    h = Horloge(cfg["horloge"], cfg["routine"])
    assert h.jour_semaine(0) == "mercredi" and h.minute_du_jour(0) == 8 * 60
    t_article = h.t_de(3, "07:30")
    assert h.jour_semaine(t_article) == "vendredi" and h.libelle(t_article) == "J3 vendredi 07:30"
    assert not h.agences_ouvertes(h.t_de(1, "08:45"))
    assert h.agences_ouvertes(h.t_de(1, "09:00"))
    assert not h.agences_ouvertes(h.t_de(1, "17:00"))
    assert not h.agences_ouvertes(h.t_de(5, "10:00"))   # J5 = dimanche
    assert h.fin == 7 * 1440 - 8 * 60
