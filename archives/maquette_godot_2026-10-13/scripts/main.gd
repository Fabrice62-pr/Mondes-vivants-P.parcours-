extends Node3D
## GUICHET : simulation d'un bank run dans la vallée de Brévanne.
## Ce script construit la scène, crée les déposants et orchestre la simulation :
## rumeur, conversations, files d'attente, comptabilité et leviers.

const PLACES := ["cafe", "marche", "ecole", "presse", "boulangerie"]
const PLACE_WEIGHTS := [3.0, 3.0, 1.5, 1.5, 2.0]
const COMMUNIQUES := [
	"La Caisse est solide. Vos dépôts sont en sécurité.",
	"La Caisse détient des réserves suffisantes et l'emprunteur cité ne représente qu'une part limitée de ses prêts.",
	"La direction suit la situation avec attention.",
]

var rng := RandomNumberGenerator.new()
var builder: WorldBuilder
var caisse: Caisse
var llm: LlmClient
var hud: Hud
var camera_rig: CameraRig
var agents: Array = []
var agency_list: Array = []

var sim_time := 0.0
var rumor_fired := false
var last_communique := ""
var no_return_logged := false
var tipping := "Faible"
var _conv_timer := 0.0
var _stats_timer := 0.0
var _leak_timer := 0.0
var _agents_root: Node3D

## Leviers. Ceux marqués (relance) ne s'appliquent qu'au prochain lancement.
var params := {
	"population": 140,            # (relance)
	"reserve_ratio": 0.15,        # (relance) part des dépôts gardée en caisse
	"loan_loss": 0.0,             # (relance) pertes réelles si l'emprunteur fait défaut
	"connected_share": 0.2,       # (relance) part de déposants très connectés
	"rumor_time": 20.0,           # (relance) secondes avant l'article
	"rumor_strength": 0.55,       # (relance)
	"guarantee": false,
	"guarantee_ceiling": 20000.0,
	"withdraw_cap": 1.0,          # part du dépôt retirable par passage au guichet
	"employee_silent": true,
	"use_llm": false,             # (relance)
	"llm_budget": 150,            # (relance)
	"llm_endpoint": "http://127.0.0.1:8787/dialogue",
	"seed": 0,                    # 0 = aléatoire
}


func _ready() -> void:
	_setup_environment()
	builder = WorldBuilder.new()
	builder.name = "Vallee"
	add_child(builder)
	builder.build()
	agency_list = builder.agencies.values()
	for ag in agency_list:
		ag.sim = self
	camera_rig = CameraRig.new()
	camera_rig.name = "CameraRig"
	camera_rig.process_mode = Node.PROCESS_MODE_ALWAYS
	add_child(camera_rig)
	caisse = Caisse.new()
	caisse.name = "Caisse"
	caisse.defaulted.connect(_on_default)
	add_child(caisse)
	llm = LlmClient.new()
	llm.name = "LlmClient"
	add_child(llm)
	_agents_root = Node3D.new()
	_agents_root.name = "Deposants"
	add_child(_agents_root)
	hud = Hud.new()
	hud.name = "Hud"
	hud.sim = self
	add_child(hud)
	reset_simulation()


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


# ---------------------------------------------------------------- lancement

func reset_simulation() -> void:
	for a in agents:
		if is_instance_valid(a):
			a.queue_free()
	agents.clear()
	for ag in agency_list:
		ag.clear_queue()
		ag.is_open = true
	var sd: int = params["seed"]
	if sd == 0:
		rng.randomize()
	else:
		rng.seed = sd
	sim_time = 0.0
	rumor_fired = false
	last_communique = ""
	no_return_logged = false
	tipping = "Faible"
	_leak_timer = 0.0
	llm.endpoint = params["llm_endpoint"]
	llm.reset_budget(params["llm_budget"])
	_spawn_agents()
	var total := 0.0
	for a in agents:
		total += a.deposit
	caisse.setup(total, params["reserve_ratio"], params["loan_loss"])
	hud.clear_log()
	hud.log_line("[b]Vallée de Brévanne.[/b] %d déposants, %s en dépôts, %s en caisse." % [
		agents.size(), fmt_money(total), fmt_money(caisse.reserves)])
	hud.log_line("L'article paraîtra dans %d s." % int(params["rumor_time"]))
	hud.refresh_controls()


func _spawn_agents() -> void:
	var homes: Array = builder.homes.duplicate()
	homes.shuffle()
	# Personnages nommés du monde
	_make_agent("La boulangère", "commerçante, toute sa trésorerie est à la Caisse", 48000.0,
		builder.places["boulangerie"], 0.5, 0.55, 0.65, false, true)
	_make_agent("Le retraité", "fonctionnaire retraité, client depuis quarante ans", 60000.0,
		_home_near(homes, WorldData.to_world(-45, -16)), 0.92, 0.95, 0.9, false, true)
	_make_agent("L'entrepreneur", "jeune entrepreneur connecté, lit tout et réagit vite", 26000.0,
		_home_near(homes, WorldData.to_world(58, 20)), 0.45, 0.2, 0.45, true, true)
	var n: int = params["population"]
	var cs: float = params["connected_share"]
	for i in n:
		var home: Vector3 = homes[i % homes.size()] + Vector3(rng.randf_range(-2, 2), 0, rng.randf_range(-2, 2))
		var r := rng.randf()
		if r < cs:
			_make_agent("Connecté %d" % i, "très connecté, réagit vite aux nouvelles",
				rng.randf_range(5000, 25000), home, rng.randf_range(0.42, 0.55), 0.25, 0.5, true, false)
		elif r < cs + 0.25:
			_make_agent("Fidèle %d" % i, "client fidèle et confiant", rng.randf_range(3000, 30000), home,
				rng.randf_range(0.7, 0.85), 0.8, 0.85, false, false)
		elif r < cs + 0.45:
			_make_agent("Commerçant %d" % i, "commerçant, besoin de trésorerie", rng.randf_range(15000, 50000),
				home, rng.randf_range(0.5, 0.62), 0.5, 0.65, false, false)
		else:
			_make_agent("Prudent %d" % i, "épargnant prudent", rng.randf_range(2000, 20000), home,
				rng.randf_range(0.52, 0.68), 0.45, 0.6, false, false)


func _home_near(homes: Array, p: Vector3) -> Vector3:
	var best: Vector3 = homes[0]
	for h in homes:
		if (h as Vector3).distance_to(p) < best.distance_to(p):
			best = h
	return best


func _make_agent(n: String, arch: String, dep: float, home: Vector3, thr: float, loy: float,
		tr: float, conn: bool, is_named: bool) -> void:
	var a := Depositor.new()
	a.sim = self
	a.display_name = n
	a.archetype = arch
	a.deposit = round(dep / 100.0) * 100.0
	a.initial_deposit = a.deposit
	a.home = Vector3(home.x, WorldData.height_world(home.x, home.z), home.z)
	a.threshold = thr
	a.loyalty = loy
	a.trust = tr
	a.connected = conn
	a.named = is_named
	a.speed = rng.randf_range(4.5, 7.0)
	_agents_root.add_child(a)
	agents.append(a)


# ---------------------------------------------------------------- boucle

func _process(delta: float) -> void:
	if caisse.failed:
		_stats_timer -= delta
		if _stats_timer <= 0.0:
			_stats_timer = 0.5
			hud.update_stats(_compute_stats())
		return
	sim_time += delta
	if not rumor_fired and sim_time >= params["rumor_time"]:
		_fire_rumor()
	_conv_timer -= delta
	if _conv_timer <= 0.0:
		_conv_timer = 1.5
		_run_conversations()
	_leak_timer -= delta
	if not params["employee_silent"] and rumor_fired and _leak_timer <= 0.0:
		_employee_leak()
	_stats_timer -= delta
	if _stats_timer <= 0.0:
		_stats_timer = 0.5
		hud.update_stats(_compute_stats())


func _fire_rumor() -> void:
	rumor_fired = true
	if params["loan_loss"] > 0.0:
		caisse.realize_loss()
	hud.log_line("[color=#e9a68f][b]Vendredi, la presse publie :[/b] « Un gros emprunteur de la Caisse serait en difficulté. »[/color]")
	var presse: Vector3 = builder.places["presse"]
	var s: float = params["rumor_strength"]
	for a in agents:
		if a.connected or a.global_position.distance_to(presse) < 30.0:
			a.inform(s * rng.randf_range(0.7, 1.2))


func press_check(a: Depositor) -> void:
	if rumor_fired and not a.informed and a.global_position.distance_to(builder.places["presse"]) < 20.0:
		a.inform(params["rumor_strength"] * 0.6)


func pick_place(a: Depositor) -> String:
	var total := 0.0
	var w: Array = []
	for i in PLACES.size():
		var d: float = (builder.places[PLACES[i]] as Vector3).distance_to(a.home)
		var v: float = PLACE_WEIGHTS[i] / (1.0 + d / 80.0)
		w.append(v)
		total += v
	var r := rng.randf() * total
	for i in PLACES.size():
		r -= w[i]
		if r <= 0.0:
			return PLACES[i]
	return PLACES[0]


func place_point(id: String) -> Vector3:
	var c: Vector3 = builder.places[id]
	var ang := rng.randf() * TAU
	var rad := rng.randf_range(2.0, 7.0)
	var x := c.x + cos(ang) * rad
	var z := c.z + sin(ang) * rad
	return Vector3(x, WorldData.height_world(x, z), z)


func on_arrive_place(_a: Depositor, _place: String) -> void:
	pass


func nearest_open_agency(p: Vector3) -> Agency:
	var best: Agency = null
	var best_d := INF
	for ag in agency_list:
		if not ag.is_open:
			continue
		var d: float = ag.global_position.distance_to(p)
		if d < best_d:
			best_d = d
			best = ag
	return best


func guarantee_cover(deposit: float) -> float:
	if not params["guarantee"] or deposit <= 0.0:
		return 0.0
	return clampf(params["guarantee_ceiling"] / deposit, 0.0, 1.0)


func on_decision(a: Depositor) -> void:
	if a.named:
		hud.log_line("[b]%s[/b] part retirer son argent à l'%s." % [a.display_name, a.agency.display_name])


# ---------------------------------------------------------------- guichet

func serve(a: Depositor, ag: Agency) -> void:
	if not is_instance_valid(a):
		return
	var requested: float = a.deposit * params["withdraw_cap"]
	if params["withdraw_cap"] < 1.0:
		requested = maxf(requested, minf(a.deposit, 500.0))
	var paid := caisse.withdraw(requested)
	var p_lost := 0.0
	if paid < requested and caisse.failed:
		var covered: float = minf(a.deposit - paid, params["guarantee_ceiling"]) if params["guarantee"] else 0.0
		p_lost = maxf(0.0, a.deposit - paid - covered)
		paid += covered
	a.on_served(paid, requested, p_lost)
	if a.named:
		hud.log_line("%s passe au guichet et retire %s." % [a.display_name, fmt_money(paid)])


func _on_default() -> void:
	hud.log_line("[color=#ff8a6a][b]DÉFAUT à t = %d s.[/b] Les réserves sont épuisées : la Caisse ne peut plus payer.[/color]" % int(sim_time))
	if caisse.is_solvent():
		hud.log_line("[color=#ff8a6a]La Caisse restait solvable : panique autoréalisatrice.[/color]")
	else:
		hud.log_line("[color=#ff8a6a]La Caisse était insolvable : panique justifiée.[/color]")
	for ag in agency_list:
		ag.clear_queue()
	for a in agents:
		var cov: float = minf(a.deposit, params["guarantee_ceiling"]) if params["guarantee"] else 0.0
		a.finalize_default(cov)


# ---------------------------------------------------------------- leviers

func publish_communique(tone: int) -> void:
	var st := _compute_stats()
	var cred := clampf(1.0 - 2.0 * (st["withdrawn_share"] + st["queued_share"]), 0.05, 1.0)
	last_communique = COMMUNIQUES[tone]
	hud.log_line("[color=#9fd3c0][b]Communiqué de la direction[/b] (crédibilité %d %%) : « %s »[/color]" % [int(cred * 100), last_communique])
	for a in agents:
		if a.withdrawn:
			continue
		if not a.connected and rng.randf() > 0.6:
			continue
		var r := rng.randf_range(0.6, 1.4)   # chacun ne réagit pas pareil
		match tone:
			0:
				if cred > 0.45:
					a.fear -= 0.3 * cred * a.trust * r
				else:
					a.fear += 0.12 * r       # « s'ils rassurent, c'est que ça va mal »
			1:
				a.fear -= 0.25 * cred * r * (0.5 + 0.5 * a.trust)
				a.est_others -= 0.1 * cred * r
			2:
				a.fear += 0.08 * r * (1.0 - a.trust)
		a.fear = clampf(a.fear, 0.0, 1.0)
		a.est_others = clampf(a.est_others, 0.0, 1.0)


func set_agency_open(id: String, v: bool) -> void:
	var ag: Agency = builder.agencies[id]
	ag.set_open(v)
	hud.log_line("%s %s." % [ag.display_name, "rouvre" if v else "ferme ses portes"])
	if not v:
		for a in agents:
			if a.global_position.distance_to(ag.global_position) < 60.0:
				a.fear = clampf(a.fear + 0.15, 0.0, 1.0)


func _employee_leak() -> void:
	var st := _compute_stats()
	if st["withdrawn_share"] < 0.08:
		return
	var cafe: Vector3 = builder.places["cafe"]
	for a in agents:
		if a.state == Depositor.State.CHATTING and a.global_position.distance_to(cafe) < 15.0:
			a.fear = clampf(a.fear + 0.25, 0.0, 1.0)
			a.est_others = clampf(a.est_others + 0.25, 0.0, 1.0)
			a.informed = true
			hud.log_line("Au café, l'employé d'agence glisse à [b]%s[/b] que les retraits s'accumulent." % a.display_name)
			_leak_timer = 12.0
			return


# ---------------------------------------------------------------- conversations

func _run_conversations() -> void:
	for place in PLACES:
		var c: Vector3 = builder.places[place]
		var here: Array = []
		for a in agents:
			if a.state == Depositor.State.CHATTING and a.target_place == place \
					and sim_time - a.last_talk > 4.0 and a.global_position.distance_to(c) < 14.0:
				here.append(a)
		here.shuffle()
		var i := 0
		while i + 1 < here.size():
			_converse(here[i], here[i + 1], place)
			i += 2


func _converse(a: Depositor, b: Depositor, place: String) -> void:
	a.last_talk = sim_time
	b.last_talk = sim_time
	var speaker := a if a.pressure() >= b.pressure() else b
	var listener := b if speaker == a else a
	if not speaker.informed and not listener.informed:
		return
	if params["use_llm"] and llm.can_request():
		var payload := {
			"lieu": WorldData.site(place).get("name", place),
			"temps_simulation_s": int(sim_time),
			"article_paru": rumor_fired,
			"files_d_attente": _queues_dict(),
			"agences_fermees": _closed_list(),
			"dernier_communique": last_communique,
			"garantie_des_depots": ("jusqu'à %s par déposant" % fmt_money(params["guarantee_ceiling"])) if params["guarantee"] else "aucune",
			"locuteur": speaker.profile(),
			"auditeur": listener.profile(),
		}
		llm.request_dialogue(payload, func(res): _on_llm_result(res, speaker, listener, place))
	else:
		_rule_dialogue(speaker, listener, place)


func _on_llm_result(res, speaker: Depositor, listener: Depositor, place: String) -> void:
	if not is_instance_valid(speaker) or not is_instance_valid(listener):
		return
	if res == null:
		_rule_dialogue(speaker, listener, place)
		return
	var phrase := str(res.get("phrase", ""))
	var reaction := str(res.get("reaction", ""))
	var delta := clampf(float(res.get("delta_peur_auditeur", 0.0)), -0.3, 0.3)
	var intention := str(res.get("intention_auditeur", "attendre"))
	listener.informed = listener.informed or speaker.informed
	listener.fear = clampf(listener.fear + delta, 0.0, 1.0)
	match intention:
		"retirer":
			listener.est_others = clampf(listener.est_others + 0.15, 0.0, 1.0)
		"rassurer":
			listener.est_others = clampf(listener.est_others - 0.05, 0.0, 1.0)
	if phrase != "":
		speaker.say(phrase)
	if reaction != "":
		listener.say(reaction)
	hud.log_line("[i]%s[/i] — %s : « %s » / %s : « %s »" % [
		WorldData.site(place).get("name", place), speaker.display_name, phrase, listener.display_name, reaction])


func _rule_dialogue(speaker: Depositor, listener: Depositor, place: String) -> void:
	if speaker.informed:
		listener.informed = true
	var pull := 0.35 * (1.0 - listener.loyalty * 0.5) * rng.randf_range(0.7, 1.3)
	# Les mauvaises nouvelles se propagent mieux que les bonnes.
	if speaker.fear > listener.fear:
		listener.fear = lerpf(listener.fear, speaker.fear, pull)
	else:
		listener.fear = lerpf(listener.fear, speaker.fear, pull * 0.5)
	listener.est_others = lerpf(listener.est_others, maxf(listener.est_others, speaker.est_others), 0.3)
	# Voir quelqu'un d'inquiet, c'est apprendre que d'autres pourraient retirer.
	listener.est_others += 0.12 * speaker.fear
	listener.fear = clampf(listener.fear, 0.0, 1.0)
	listener.est_others = clampf(listener.est_others, 0.0, 1.0)
	var phrase := _phrase_for(speaker)
	speaker.say(phrase)
	if speaker.named or listener.named or speaker.pressure() > speaker.threshold * 0.8:
		hud.log_line("[i]%s[/i] — %s : « %s »" % [WorldData.site(place).get("name", place), speaker.display_name, phrase])


func _phrase_for(a: Depositor) -> String:
	match a.display_name:
		"La boulangère":
			if a.pressure() > a.threshold * 0.7:
				return "Toute ma trésorerie est là-bas… je ne peux pas me permettre de la perdre."
		"Le retraité":
			return "Quarante ans que je suis client. Je ne céderai pas."
		"L'entrepreneur":
			if a.informed:
				return "Ça s'emballe en ligne. Je préfère sécuriser avant les autres."
	var p := a.pressure() / a.threshold
	var pool: Array
	if a.withdrawn or a.state == Depositor.State.TO_BANK:
		pool = ["J'ai retiré mon argent. Je ne serai pas le dernier.",
			"Il y avait déjà la queue à l'agence…",
			"Si tout le monde retire, il ne restera rien pour les derniers."]
	elif p > 0.6:
		pool = ["Tu as lu l'article ? Je n'y crois pas trop, mais bon…",
			"Moi je ne bouge pas. Mais si les voisins retirent…",
			"À l'agence, ils avaient l'air nerveux."]
	else:
		pool = ["La Caisse a toujours tenu. Pas de panique.",
			"C'est un emprunteur qui a des soucis, pas la Caisse.",
			"Je garde confiance."]
	return pool[rng.randi_range(0, pool.size() - 1)]


# ---------------------------------------------------------------- indicateurs

func _queues_dict() -> Dictionary:
	var d := {}
	for ag in agency_list:
		d[ag.display_name] = ag.queue.size()
	return d


func _closed_list() -> Array:
	var l: Array = []
	for ag in agency_list:
		if not ag.is_open:
			l.append(ag.display_name)
	return l


func _compute_stats() -> Dictionary:
	var committed := 0.0
	var near := 0.0
	var n_calm := 0
	var n_worried := 0
	var n_run := 0
	var n_done := 0
	var n_lost := 0
	var fear_sum := 0.0
	var est_sum := 0.0
	var queued := 0
	for a in agents:
		fear_sum += a.fear
		est_sum += a.est_others
		if a.lost > 0.0:
			n_lost += 1
		elif a.withdrawn:
			n_done += 1
		elif a.state == Depositor.State.TO_BANK or a.state == Depositor.State.QUEUING:
			n_run += 1
			committed += a.deposit * params["withdraw_cap"]
			if a.state == Depositor.State.QUEUING:
				queued += 1
		else:
			var p: float = a.pressure()
			if p > a.threshold * 0.6:
				n_worried += 1
			else:
				n_calm += 1
			if p > a.threshold * 0.75:
				near += a.deposit * params["withdraw_cap"]
	var n := maxi(agents.size(), 1)
	# Point de non-retour : les retraits déjà engagés dépassent ce qui reste en caisse.
	if caisse.failed:
		tipping = "Défaut"
	elif committed > caisse.reserves:
		tipping = "Irréversible"
		if not no_return_logged:
			no_return_logged = true
			hud.log_line("[color=#ff8a6a][b]Point de non-retour à t = %d s :[/b] les retraits engagés (%s) dépassent les réserves (%s).[/color]" % [
				int(sim_time), fmt_money(committed), fmt_money(caisse.reserves)])
	elif committed + near > caisse.reserves:
		tipping = "Proche"
	else:
		tipping = "Faible"
	return {
		"time": sim_time,
		"deposits": caisse.deposits,
		"initial": caisse.initial_deposits,
		"reserves": caisse.reserves,
		"reserve_start": caisse.initial_deposits * params["reserve_ratio"],
		"solvency": caisse.solvency_margin(),
		"failed": caisse.failed,
		"committed": committed,
		"near": near,
		"tipping": tipping,
		"calm": n_calm, "worried": n_worried, "run": n_run, "done": n_done, "lost": n_lost,
		"avg_fear": fear_sum / n,
		"avg_est": est_sum / n,
		"withdrawn_share": caisse.withdrawn_total / maxf(caisse.initial_deposits, 1.0),
		"queued_share": float(queued) / n,
		"llm_calls": llm.calls_made,
		"llm_budget": llm.max_calls,
	}


func fmt_money(v: float) -> String:
	var s := str(int(round(v)))
	var out := ""
	var c := 0
	for i in range(s.length() - 1, -1, -1):
		out = s[i] + out
		c += 1
		if c % 3 == 0 and i > 0 and s[i - 1] != "-":
			out = " " + out
	return out + " u"
