"""Bridge FastAPI (services/bridge.py), sans Kafka : un faux producteur garde ce qui serait publié."""
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from services.bridge import Bridge, creer_app  # noqa: E402


class FauxProducteur:
    def __init__(self):
        self.publies = []

    def produce(self, topic, valeur, key=None):
        self.publies.append(topic)

    def poll(self, _):
        return 0

    def flush(self, _):
        return 0

    def list_topics(self, timeout):
        return {}


@pytest.fixture
def client():
    app = creer_app(lambda: Bridge("aucun", producteur=FauxProducteur()), kafka=False)
    with TestClient(app) as c:
        b = app.state.bridge
        b.lances = []
        b._demarrer = lambda args, nom: b.lances.append(args)    # ne lance pas de vrai moteur
        yield c, b


def _hello(ws):
    hello = ws.receive_json()
    assert hello["type"] == "bridge.hello" and "principal.yaml" in hello["configs"]
    return ws


def test_messages_refuses(client):
    c, _ = client
    with c.websocket_connect("/ws") as ws:
        _hello(ws)
        for brut in ["pas du json", '{"action": "voler"}', '{"action": "lancer", "deposants": -3}',
                     '{"action": "lancer", "inconnu": 1}', '{"action": "rejouer", "run_id": "../x"}']:
            ws.send_text(brut)
            r = ws.receive_json()
            assert r["type"] == "bridge.statut" and r["etat"] == "erreur", brut


def test_lancer_annonce_a_tous_les_clients(client):
    c, b = client
    with c.websocket_connect("/ws") as ws1, c.websocket_connect("/ws") as ws2:
        _hello(ws1), _hello(ws2)
        ws1.send_json({"action": "lancer", "config": "principal.yaml", "deposants": 50, "graine": 3, "rythme": 0.2,
                       "llm": False, "mode": "asynchrone"})
        for ws in (ws1, ws2):
            assert ws.receive_json() == {"type": "bridge.statut", "etat": "lance", "detail": "principal.yaml"}
    args = b.lances[0]
    for attendu in (["--deposants", "50"], ["--graines", "3"], ["--mode", "asynchrone"]):
        i = args.index(attendu[0])
        assert args[i:i + 2] == attendu
    assert "--sans-llm" in args


def test_lancer_avec_systeme1_et_etiquette(client):
    c, b = client
    assert c.post("/runs", json={"systeme1": "nli", "etiquette": "s1-nli"}).json()["etat"] == "lance"
    args = b.lances[-1]
    for attendu in (["--systeme1", "nli"], ["--etiquette", "s1-nli"]):
        i = args.index(attendu[0])
        assert args[i:i + 2] == attendu
    assert c.post("/runs", json={"systeme1": "magie"}).status_code == 422
    assert c.post("/runs", json={"etiquette": "a b/c"}).status_code == 422      # pas d'espace ni de « / »


def test_commande_conforme_ou_dlq(client):
    c, b = client
    with c.websocket_connect("/ws") as ws:
        _hello(ws)
        ws.send_json({"action": "commande", "commande": "garantie", "parametres": {"actif": True}})
        assert ws.receive_json()["etat"] == "commande_envoyee"
        ws.send_json({"action": "commande", "commande": "inexistante"})
        assert ws.receive_json()["etat"] == "erreur"
    assert b.producteur.publies == ["guichet.commands", "guichet.dlq"]


def test_evenement_kafka_relaye_et_rattrape(client):
    c, b = client
    evt = {"type": "run.started", "run_id": "r1", "t_sim": 0, "payload": {}}
    with c.websocket_connect("/ws") as ws:
        _hello(ws)
        b.loop.call_soon_threadsafe(b.recevoir, evt)             # comme le fil d'écoute de Kafka
        assert ws.receive_json() == evt
    with c.websocket_connect("/ws") as tard:                     # un client qui arrive en cours de run
        _hello(tard)
        assert tard.receive_json() == evt


def test_routes_rest(client):
    c, b = client
    assert "principal.yaml" in c.get("/configurations").json()
    assert c.get("/sante").json()["kafka"] is True
    assert c.post("/runs", json={"config": "nexiste_pas.yaml"}).status_code == 404
    assert c.post("/runs", json={"rythme": -1}).status_code == 422
    assert c.post("/runs", json={"graine": 2}).json()["etat"] == "lance"
    assert c.delete("/runs/actif").status_code == 404           # le faux lancement n'a pas de processus
    assert c.post("/runs/inconnu/rejeu").status_code == 404
    assert c.post("/commandes", json={"commande": "pause"}).json()["etat"] == "commande_envoyee"
    assert c.get("/docs").status_code == 200
