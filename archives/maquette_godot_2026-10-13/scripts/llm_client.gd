class_name LlmClient
extends Node
## Client optionnel vers un LLM, via un PROXY local (voir proxy/server.py).
## La clé API ne doit JAMAIS être dans le jeu : elle reste sur le proxy.
##
## Le proxy reçoit un JSON décrivant deux déposants et le contexte, et renvoie :
## {"phrase": "...", "reaction": "...", "delta_peur_auditeur": -0.3..0.3,
##  "intention_auditeur": "retirer" | "attendre" | "rassurer"}

var endpoint := "http://127.0.0.1:8787/dialogue"
var max_calls := 150         # budget total par simulation
var max_in_flight := 3       # requêtes simultanées
var calls_made := 0
var in_flight := 0
var unavailable := false     # passe à true après 3 échecs d'affilée
var _failures := 0


func reset_budget(budget: int) -> void:
	max_calls = budget
	calls_made = 0
	unavailable = false
	_failures = 0


func can_request() -> bool:
	return not unavailable and calls_made < max_calls and in_flight < max_in_flight


func request_dialogue(payload: Dictionary, on_done: Callable) -> void:
	var http := HTTPRequest.new()
	http.timeout = 30.0
	add_child(http)
	calls_made += 1
	in_flight += 1
	http.request_completed.connect(_on_completed.bind(http, on_done))
	var err := http.request(endpoint, PackedStringArray(["Content-Type: application/json"]),
		HTTPClient.METHOD_POST, JSON.stringify(payload))
	if err != OK:
		in_flight -= 1
		http.queue_free()
		_fail()
		on_done.call(null)


func _fail() -> void:
	_failures += 1
	if _failures >= 3 and not unavailable:
		unavailable = true
		push_warning("LLM : proxy injoignable à %s, retour aux règles locales." % endpoint)


func _on_completed(result: int, code: int, _headers: PackedStringArray, body: PackedByteArray,
		http: HTTPRequest, on_done: Callable) -> void:
	in_flight -= 1
	http.queue_free()
	if result != HTTPRequest.RESULT_SUCCESS or code != 200:
		_fail()
		on_done.call(null)
		return
	var data = JSON.parse_string(body.get_string_from_utf8())
	if typeof(data) != TYPE_DICTIONARY:
		_fail()
		on_done.call(null)
		return
	_failures = 0
	on_done.call(data)
