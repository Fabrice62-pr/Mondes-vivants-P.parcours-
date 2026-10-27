extends Node3D
## GUICHET : visualisation de la simulation (D1) et pilotage (D3).
## La simulation tourne dans le moteur Python ; Godot reçoit ses événements par le bridge
## (services/bridge.py) et renvoie les actions de l'utilisateur sous forme de commandes.

const STAFF := ["employe", "direction"]
## Événements gardés en mémoire pour remonter aux sources de l'ICC (B4, F6).
const TRACE_TYPES := ["belief.updated", "conversation.held", "article.published", "communique.published",
	"counter.exchanged", "command.issued"]

var builder: WorldBuilder
var camera_rig: CameraRig
var hud: Hud
var bridge: BridgeClient
var agents := {}                 # agent_id -> AgentView
var queues := {}                 # agence -> Array[agent_id], dans l'ordre d'arrivée
var run_id := ""
var run_info := {}
var t_sim := 0
var pas_min := 15
var tick_interval := 0.5         # secondes réelles entre deux pas (mesurées)
var selected: AgentView = null
var traces := {}                 # event_id -> {type, t, p, causes} (voir TRACE_TYPES)
var souvenirs := {}              # memoire_id -> {quand, texte, nature} (mémoire des agents LLM, D2)
var _home_use := {}              # indice de maison -> nombre d'habitants déjà placés
var _last_tick := -1.0
var _press_pos := Vector2.ZERO
var _agents_root: Node3D


func _ready() -> void:
	_setup_environment()
	builder = WorldBuilder.new()
	builder.name = "Vallee"
	add_child(builder)
	builder.build()
	for id in builder.agencies:
		queues[id] = []
	camera_rig = CameraRig.new()
	camera_rig.name = "CameraRig"
	add_child(camera_rig)
	_agents_root = Node3D.new()
	_agents_root.name = "Agents"
	add_child(_agents_root)
	bridge = BridgeClient.new()
	bridge.name = "Bridge"
	add_child(bridge)
	hud = Hud.new()
	hud.name = "Hud"
	hud.main = self
	add_child(hud)
	bridge.message_received.connect(_on_message)
	bridge.connection_changed.connect(hud.on_connection)


func _setup_environment() -> void:
	var env := Environment.new()
	env.background_mode = Environment.BG_COLOR
	env.background_color = Color("cfdce3")
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = Color("e6edf0")
	env.ambient_light_energy = 0.65
	env.tonemap_mode = Environment.TONE_MAPPER_FILMIC
	env.fog_enabled = true
	env.fog_light_color = Color("cfdce3")
	env.fog_density = 0.0011
	var we := WorldEnvironment.new()
	we.environment = env
	add_child(we)
	var sun := DirectionalLight3D.new()
	sun.name = "Soleil"
	sun.light_color = Color("fff3e0")
	sun.light_energy = 1.1
	sun.shadow_enabled = true
	sun.directional_shadow_max_distance = 450.0
	sun.rotation_degrees = Vector3(-55, -40, 0)
	add_child(sun)


# ---------------------------------------------------------------- envoi vers le moteur

func send_command(commande: String, parametres: Dictionary) -> void:
	if not bridge.send({"action": "commande", "commande": commande, "parametres": parametres}):
		hud.log_line("[color=#ff8a6a]Bridge non connecté : commande « %s » non envoyée.[/color]" % commande)


func launch_run(config: String, deposants: int, graine: int, rythme: float, llm: bool, mode: String) -> void:
	bridge.send({"action": "lancer", "config": config, "deposants": deposants, "graine": graine, "rythme": rythme,
		"llm": llm, "mode": mode})


## Rejoue un run enregistré (E3) : le bridge relit son historique, sans aucun appel LLM.
func replay_run(id: String, rythme: float) -> void:
	if not bridge.send({"action": "rejouer", "run_id": id, "rythme": rythme}):
		hud.log_line("[color=#ff8a6a]Bridge non connecté : rejeu impossible.[/color]")


func stop_run() -> void:
	bridge.send({"action": "arreter"})


func request_runs() -> void:
	if not bridge.send({"action": "comparer"}):
		hud.log_line("[color=#ff8a6a]Bridge non connecté : comparaison indisponible.[/color]")


# ---------------------------------------------------------------- réception des événements

func _on_message(m: Dictionary) -> void:
	var typ: String = m.get("type", "")
	if typ.begins_with("bridge."):
		hud.on_bridge(m)
		return
	if typ == "run.started":
		_start_run(m)
		return
	if m.get("run_id", "") != run_id:
		return                          # événement d'un autre run (ou arrivé avant run.started)
	var p: Dictionary = m.get("payload", {})
	t_sim = maxi(t_sim, int(m.get("t_sim", 0)))
	if typ in TRACE_TYPES:
		traces[m["event_id"]] = {"type": typ, "t": int(m["t_sim"]), "p": p, "causes": m.get("causes", [])}
	match typ:
		"clock.tick":
			_on_tick(p)
		"agent.moved":
			_on_moved(p, int(m["t_sim"]))
		"state.snapshot":
			_on_snapshot(p)
		"indicator.computed":
			hud.on_indicator(p, int(m["t_sim"]))
		"conversation.held":
			_on_conversation(p)
		"decision.made":
			_on_decision(p, int(m["t_sim"]))
		"counter.exchanged":
			_on_counter(p)
		"withdrawal.served":
			var a: AgentView = agents.get(p["agent_id"])
			if a and a.named:
				hud.log_line("[b]%s[/b] retire %s." % [a.display_name, hud.fmt_money(float(p["paye"]))])
		"article.published":
			hud.log_line("[color=#e9a68f][b]La presse publie :[/b] « %s » %s[/color]" % [p["titre"], p["texte"]])
		"communique.published":
			hud.log_line("[color=#9fd3c0][b]Communiqué %s[/b] (%s, crédibilité %d %%) : « %s »[/color]" % [
				p["ton"], "forcé par l'utilisateur" if p["auteur"] == "utilisateur" else "décidé par la direction",
				int(float(p["credibilite"]) * 100), p["texte"]])
		"caisse.updated":
			hud.on_caisse(p)
		"caisse.defaulted":
			hud.log_line("[color=#ff8a6a][b]DÉFAUT[/b] à %s : les réserves sont épuisées. %s[/color]" % [
				hud.clock_text, "La Caisse restait solvable : panique autoréalisatrice." if p["solvable"]
				else "La Caisse était insolvable : panique justifiée."])
		"command.issued":
			hud.on_command(p)
		"queue.changed":
			var ag: Agency = builder.agencies.get(p["agence_id"])
			if ag:
				ag.set_open(bool(p["ouverte"]))
		"incident.raised":
			var who: AgentView = agents.get(str(p["agent_id"]))
			hud.on_incident()
			hud.log_line("[color=#f0b46a]Incident LLM (%s)%s : %s — %s[/color]" % [p["nature"],
				(" pour " + who.display_name) if who else "", p["detail"],
				"décision prise avec sa règle" if p["repli"] == "regles" else p["repli"]])
		"llm.called":
			hud.on_llm_call(p)
		"memory.written":
			var s := {"quand": hud.clock_text, "texte": p["texte"], "nature": p["nature"]}
			souvenirs[p["memoire_id"]] = s
			var qui: AgentView = agents.get(p["agent_id"])
			if qui:
				qui.memoires.append(s)
				if qui.memoires.size() > 30:
					qui.memoires.pop_front()
			hud.on_memory()
		"temporal.violation":
			hud.log_line("[color=#ff8a6a]Violation temporelle (C6) : %s s'appuie sur %s (%s)[/color]" % [
				p["agent_id"], p["element_id"], p["raison"]])
		"run.ended":
			hud.on_run_ended(p)
			_print_summary()


func _start_run(m: Dictionary) -> void:
	for a in agents.values():
		a.queue_free()
	agents.clear()
	for id in queues:
		queues[id] = []
	selected = null
	traces.clear()
	souvenirs.clear()
	_home_use.clear()
	run_id = m["run_id"]
	run_info = m["payload"]
	pas_min = int(run_info["horloge"]["pas_min"])
	t_sim = 0
	_last_tick = -1.0
	for row in run_info["attribution"]:
		var a := AgentView.new()
		var dom: Dictionary = row["domicile"]
		var hp := Vector3(float(dom["x"]), 0.0, float(dom["z"]))
		if row["role"] == "deposant":
			hp = _assign_home(hp)
		else:
			hp = builder.places["bourg"] + Vector3(-4.0 if row["role"] == "employe" else -7.0, 0, -2.0)
		a.setup(row, hp)
		a.name = "Agent_" + str(row["agent_id"])
		_agents_root.add_child(a)
		agents[a.agent_id] = a
	for ag in builder.agencies.values():
		ag.set_open(false)
	hud.on_run_started(m)
	print("[godot] run %s : %d agents créés" % [run_id, agents.size()])


## Bilan affiché dans la console à la fin d'un run (utile pour les vérifications sans fenêtre).
func _print_summary() -> void:
	var colors := {}
	var away := 0
	for a in agents.values():
		colors[a.color_name()] = colors.get(a.color_name(), 0) + 1
		if a.position.distance_to(a.home) > 3.0:
			away += 1
	print("[godot] fin du run %s à %s : %d agents affichés, %d hors de chez eux, couleurs %s, files %s" % [
		run_id, hud.clock_text, agents.size(), away, colors, queues])


## Maison proche du point tiré par le moteur, en évitant d'entasser tout le monde dans la même :
## chaque habitant déjà placé « éloigne » la maison de 25 m. Les colocataires se répartissent
## autour de la porte.
func _assign_home(p: Vector3) -> Vector3:
	var best := -1
	var best_score := INF
	for i in builder.homes.size():
		var h: Vector3 = builder.homes[i]
		var score: float = Vector2(h.x - p.x, h.z - p.z).length() + 25.0 * _home_use.get(i, 0)
		if score < best_score:
			best_score = score
			best = i
	if best < 0:
		return p
	var n: int = _home_use.get(best, 0)
	_home_use[best] = n + 1
	var door: Vector3 = builder.homes[best]
	if n == 0:
		return door
	var ang := n * 2.4   # angle d'or : les places ne se recouvrent pas
	return door + Vector3(cos(ang), 0, sin(ang)) * (1.8 + 0.5 * floorf(n / 3.0))


func _on_tick(p: Dictionary) -> void:
	var now := Time.get_ticks_msec() / 1000.0
	if _last_tick > 0.0:
		tick_interval = clampf(lerpf(tick_interval, now - _last_tick, 0.3), 0.03, 5.0)
	_last_tick = now
	hud.on_clock(p)


## Point où se tient un agent dans un lieu donné (domicile, lieu de rencontre ou agence).
func _spot(a: AgentView, lieu: String) -> Vector3:
	if lieu == "domicile":
		return a.home
	if builder.agencies.has(lieu):
		var ag: Agency = builder.agencies[lieu]
		var i: int = queues[lieu].find(a.agent_id)
		return ag.queue_slot(i if i >= 0 else queues[lieu].size())
	var c: Vector3 = builder.places.get(lieu, a.home)
	var ang := a.offset.x * 2.0
	var r := 2.5 + absf(a.offset.y) * 2.5
	var x := c.x + cos(ang) * r
	var z := c.z + sin(ang) * r
	return Vector3(x, WorldData.height_world(x, z), z)


func _on_moved(p: Dictionary, t: int) -> void:
	var a: AgentView = agents.get(p["agent_id"])
	if a == null:
		return
	var minutes := float(int(p["t_sim_arrivee"]) - t)
	var duree := clampf(minutes / pas_min * tick_interval, 0.15, tick_interval)
	if a.role != "deposant":
		a.lieu = p["vers"]
		var base: Vector3 = builder.places.get(p["vers"], a.home)
		a.move_to(a.home if p["vers"] == "bourg" else base + Vector3(-2, 0, 1.5), duree)
		return
	a.move_to(_spot(a, p["vers"]), duree)


func _on_snapshot(p: Dictionary) -> void:
	# Files : on garde l'ordre d'arrivée connu, puis on ajoute les nouveaux venus.
	var in_queue := {}
	for id in queues:
		in_queue[id] = []
	for row in p["agents"]:
		if row["etat"] == "en_file" and in_queue.has(row["lieu"]):
			in_queue[row["lieu"]].append(row["id"])
	for id in queues:
		var kept: Array = []
		for aid in queues[id]:
			if aid in in_queue[id]:
				kept.append(aid)
		for aid in in_queue[id]:
			if not aid in kept:
				kept.append(aid)
		queues[id] = kept
	# Réconciliation : chaque agent immobile rejoint l'endroit où le moteur le situe.
	for row in p["agents"]:
		var a: AgentView = agents.get(row["id"])
		if a == null:
			continue
		a.apply_snapshot(row)
		if a.etat in ["en_route", "vers_agence"]:
			continue
		var spot := _spot(a, a.lieu)
		if Vector2(spot.x - a.target.x, spot.z - a.target.z).length() > 0.5:
			a.move_to(spot, tick_interval * 0.8)
	hud.on_snapshot(p)
	if selected:
		hud.show_agent(selected)


func _on_conversation(p: Dictionary) -> void:
	var loc: AgentView = agents.get(p["locuteur_id"])
	var aud: AgentView = agents.get(p["auditeur_id"])
	if loc == null or aud == null:
		return
	var rep: String = p["reponse"] if p["reponse"] != null else ""
	loc.say(p["phrase"])
	aud.say(rep)
	var lieu: String = WorldData.site(p["lieu"]).get("name", p["lieu"])
	loc.remember("%s — à %s : « %s »" % [hud.clock_text, aud.display_name, p["phrase"]])
	aud.remember("%s — %s lui dit : « %s »%s" % [hud.clock_text, loc.display_name, p["phrase"],
		(" ; il répond : « %s »" % rep) if rep != "" else ""])
	if loc.named or aud.named or hud.show_all_conversations:
		hud.log_line("[i]%s[/i] — %s : « %s »%s" % [lieu, loc.display_name, p["phrase"],
			(" / %s : « %s »" % [aud.display_name, rep]) if rep != "" else ""])


func _on_decision(p: Dictionary, t: int) -> void:
	var a: AgentView = agents.get(p["agent_id"])
	if a == null:
		return
	a.intention = p["action"]
	a.derniere_decision = p.duplicate()
	a.derniere_decision["t_sim"] = t
	a.derniere_decision["quand"] = hud.clock_text
	a.update_color()
	if a.named:
		var source: String = ("LLM " + a.modele) if p["type"] == "llm" else str(p["regle_id"])
		if p.get("voie", "") == "en_retard":
			source += ", en retard de %d min" % (t - int(p["t_sim_contexte"]))
		hud.log_line("[b]%s[/b] décide : %s [color=#aebbb4](%s : %s)[/color]" % [
			a.display_name, p["action"], source, p["justification"]])


func _on_counter(p: Dictionary) -> void:
	var client: AgentView = agents.get(p["client_id"])
	if client:
		client.say(p["question"])
		client.remember("%s — au guichet, demande « %s » ; réponse%s : « %s »" % [
			hud.clock_text, p["question"], " (éludée)" if p["elude"] else "", p["reponse"]])
	if p["agence_id"] == "bourg" and agents.has("employe"):
		agents["employe"].say(p["reponse"])
	var nom: String = client.display_name if client else str(p["client_id"])
	hud.log_line("[i]Guichet %s[/i] — %s : « %s » / employé%s : « %s »" % [
		WorldData.site(p["agence_id"]).get("name", p["agence_id"]), nom, p["question"],
		" (élude)" if p["elude"] else "", p["reponse"]])


# ---------------------------------------------------------------- sélection d'un agent (D2)

func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventMouseButton and (event as InputEventMouseButton).button_index == MOUSE_BUTTON_LEFT:
		var mb := event as InputEventMouseButton
		if mb.pressed:
			_press_pos = mb.position
		elif mb.position.distance_to(_press_pos) < 6.0:
			_pick(mb.position)


func _pick(screen: Vector2) -> void:
	var cam := camera_rig.cam
	var best: AgentView = null
	var best_d := 26.0
	for a in agents.values():
		var wp: Vector3 = a.global_position + Vector3(0, 1.4, 0)
		if cam.is_position_behind(wp):
			continue
		var d := cam.unproject_position(wp).distance_to(screen)
		if d < best_d:
			best_d = d
			best = a
	if selected:
		selected.set_selected(false)
	selected = best
	if selected:
		selected.set_selected(true)
		hud.show_agent(selected)
	else:
		hud.hide_agent()
