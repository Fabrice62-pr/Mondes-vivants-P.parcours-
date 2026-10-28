class_name AgentView
extends Node3D
## Représentation d'un agent dans la vallée. Toute la logique (croyances, décisions) vit dans le moteur
## Python : cette vue se contente d'afficher l'état reçu et d'animer les déplacements.
## Le personnage est un modèle 3D animé (Universal Animation Library de Quaternius, CC0) ; la couleur
## de son corps (ou de ses vêtements) donne son état, comme avant.

const COL_CALM := Color("4e8a5e")
const COL_WORRIED := Color("d9822b")
const COL_RUN := Color("b03c1c")
const COL_DONE := Color("8a8f93")
const COL_LOST := Color("1d1d1d")
const COL_STAFF := Color("2f5d8a")

const MODEL := preload("res://assets/personnages/ual_standard.glb")

## Allures : vitesse au sol (m/s) de chaque animation jouée à vitesse normale, mesurée sur le squelette
## (le pied d'appui doit rester immobile au sol). La cadence est ajustée à la vitesse réelle du
## personnage, pour que les pieds ne glissent pas.
const GAIT_WALK := {"anim": "Walk", "speed": 0.95}
const GAIT_JOG := {"anim": "Jog_Fwd", "speed": 3.6}
const GAIT_SPRINT := {"anim": "Sprint", "speed": 6.6}
const MAX_ANIM_SCALE := 2.0   # au-delà, la cadence deviendrait illisible : les pieds glissent un peu

## Allures souhaitées (m/s), comme de vrais piétons. Le temps simulé est très comprimé (15 min par pas) :
## un trajet ne peut pas durer plus de LAG_STEPS pas à l'écran, sinon l'agent décrocherait du moteur.
## Au-delà, il presse le pas. Ralentir le rythme (curseur « secondes par pas ») rend donc les
## déplacements plus réalistes.
const PACE_STROLL := 1.35     # promenade
const PACE_HURRY := 3.2       # se presser vers l'agence
const PACE_PANIC := 5.5       # courir à l'agence quand la peur est forte
const LAG_STEPS := 10.0
const MIN_TRIP := 0.6         # s
const TURN_RATE := 8.0        # rad/s (lissé)
const LANE := 0.55            # écart latéral max (m) : on ne marche pas tous dans la même trace

## Demandes de chemin en attente, traitées quelques-unes par image (voir process_pending).
static var nav: NavGrid
static var tick_interval := 0.5
## Grossissement selon la distance de la caméra : de loin, les habitants restent lisibles.
static var view_scale := 1.0
static var cam_pos := Vector3.ZERO
const PERSONAL := 0.7         # rayon (m) de l'espace personnel : deux habitants ne se traversent pas
static var _pending: Array[AgentView] = []

# Identité (événement run.started)
var agent_id := ""
var display_name := ""
var profil := ""
var role := "deposant"
var type_agent := "regles"
var modele := ""
var fournisseur := ""
var depot_initial := 0.0
var connecte := false
var named := false

# État (instantanés et événements)
var etat := "domicile"
var lieu := "domicile"
var peur := 0.0
var estime := 0.0
var depot := 0.0
var retire := false
var perdu := 0.0
var intention := "rien"
var derniere_decision := {}
var conversations: Array = []        # derniers échanges (texte), le plus récent à la fin
var memoires: Array = []             # derniers souvenirs écrits (memory.written), le plus récent à la fin

# Affichage
var home := Vector3.ZERO
var offset := Vector2.ZERO
var target := Vector3.ZERO          # destination du trajet en cours (ou dernier lieu atteint)
var _rest := Vector3.ZERO           # où l'on se tient à l'arrêt (dernier lieu atteint)
var _pace := 1.0                    # allure propre à l'agent (±15 %)
var _lane := 0.0
var _path := PackedVector3Array()   # trajet en cours (y ignoré : on suit le sol)
var _cum := PackedFloat32Array()    # longueur cumulée le long du trajet
var _seg := 0
var _len := 0.0
var _t := 0.0                       # temps écoulé sur le trajet (s), négatif pendant le délai de départ
var _dur := 0.0                     # durée du trajet (s réelles)
var _v0 := 0.0                      # vitesse au départ (trajet repris en marchant)
var _vc := 0.0                      # vitesse de croisière
var _ramp_in := 0.5
var _ramp_out := 0.5
var _speed := 0.0                   # vitesse courante (m/s)
var _req_min := 0.0
var _req_hurry := false
var _face := Vector3.ZERO
var _has_face := false
var _talk_t := 0.0
var _sep := Vector2.ZERO            # écart dû aux voisins trop proches (voir separate)
var _sep_goal := Vector2.ZERO
var _anim := ""
var _ap: AnimationPlayer
var _model: Node3D
var _body: MeshInstance3D
var _tag: Label3D
var _scale := 0.0
var _bubble: Label3D
var _ring: MeshInstance3D

static var _mats := {}


func setup(row: Dictionary, home_pos: Vector3) -> void:
	agent_id = row["agent_id"]
	display_name = row["nom"]
	profil = row["profil"]
	role = row["role"]
	type_agent = row["type"]
	modele = str(row["modele"]) if row["modele"] != null else ""
	fournisseur = str(row["fournisseur"]) if row["fournisseur"] != null else ""
	depot_initial = float(row["depot"])
	depot = depot_initial
	connecte = bool(row["connecte"])
	named = profil == "principal" or role != "deposant"
	var h := agent_id.hash()
	offset = Vector2(((h & 0xff) / 255.0 - 0.5) * 3.0, (((h >> 8) & 0xff) / 255.0 - 0.5) * 3.0)
	home = home_pos
	home.y = _ground(home.x, home.z)
	_pace = 0.85 + ((h >> 16) & 0xff) / 255.0 * 0.3
	_lane = (((h >> 24) & 0xff) / 255.0 - 0.5) * 2.0 * LANE
	position = home
	target = home
	_rest = home
	rotation.y = randf() * TAU


func _ready() -> void:
	_model = MODEL.instantiate()
	# Le modèle regarde vers +Z ; un Node3D « avance » vers -Z.
	_model.rotation.y = PI
	add_child(_model)
	_ap = _model.get_node("AnimationPlayer")
	_body = _model.find_children("*", "MeshInstance3D", true, false)[0]
	_body.set_surface_override_material(1, _shared_mat(Color("3a3f44")))
	_play("Idle", 1.0)
	_ap.seek(randf() * _ap.current_animation_length, true)   # tout le monde ne respire pas en même temps
	if named:
		_tag = Label3D.new()
		_tag.text = display_name
		_tag.billboard = BaseMaterial3D.BILLBOARD_ENABLED
		_tag.no_depth_test = true
		_tag.font_size = 40
		_tag.outline_size = 10
		_tag.pixel_size = 0.025
		add_child(_tag)
	_ring = MeshInstance3D.new()
	var tor := TorusMesh.new()
	tor.inner_radius = 0.7
	tor.outer_radius = 0.95
	_ring.mesh = tor
	var rm := StandardMaterial3D.new()
	rm.albedo_color = Color("ffe08a")
	rm.emission_enabled = true
	rm.emission = Color("ffe08a")
	rm.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	_ring.material_override = rm
	_ring.position.y = 0.08
	_ring.visible = false
	add_child(_ring)
	_bubble = Label3D.new()
	_bubble.billboard = BaseMaterial3D.BILLBOARD_ENABLED
	_bubble.no_depth_test = true
	_bubble.font_size = 34
	_bubble.outline_size = 10
	_bubble.pixel_size = 0.022
	_bubble.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_bubble.width = 520.0
	_bubble.modulate = Color("fff6d8")
	_bubble.visible = false
	add_child(_bubble)
	_apply_scale()
	update_color()


func _apply_scale() -> void:
	var s := (1.12 if named else 1.0) * view_scale
	if is_equal_approx(s, _scale):
		return
	_scale = s
	_model.scale = Vector3.ONE * s
	var top := 1.83 * s
	if _tag:
		_tag.position.y = top + 1.6
	_bubble.position.y = top + (3.0 if named else 1.2)


## Un matériau par couleur, partagé par tous les agents de la même couleur.
static func _shared_mat(c: Color) -> StandardMaterial3D:
	var key := c.to_html()
	if not _mats.has(key):
		var m := StandardMaterial3D.new()
		m.albedo_color = c
		m.roughness = 0.75
		_mats[key] = m
	return _mats[key]


static func _ground(x: float, z: float) -> float:
	return nav.ground_y(x, z) if nav else WorldData.height_world(x, z)


func _process(delta: float) -> void:
	_apply_scale()
	if _talk_t > 0.0:
		_talk_t -= delta
		if _talk_t <= 0.0:
			_bubble.visible = false
	# De près, les textes rapetissent : ils ne doivent pas masquer la scène.
	var k := clampf(position.distance_to(cam_pos) / 70.0, 0.22, 1.0)
	_bubble.pixel_size = 0.022 * k
	if _tag:
		_tag.pixel_size = 0.025 * k
	_sep = _sep.lerp(_sep_goal, minf(delta * 4.0, 1.0))
	if _path.size() >= 2 and _t < _dur:
		_walk(delta)
	else:
		_speed = 0.0
		var rest := _with_sep(_rest)
		position = Vector3(rest.x, _ground(rest.x, rest.z), rest.z)
		if _has_face:
			var d := Vector2(_face.x - position.x, _face.z - position.z)
			if d.length() > 0.2:
				rotation.y = lerp_angle(rotation.y, atan2(-d.x, -d.y), minf(delta * 3.0, 1.0))
		_play("Idle_Talking" if _talk_t > 0.0 else "Idle", 1.0)


func _walk(delta: float) -> void:
	_t += delta
	if _t <= 0.0:
		return   # délai de départ : tout le monde ne part pas au même instant
	var t := minf(_t, _dur)
	var s := _distance_at(t)
	_speed = _speed_at(t)
	_seg = _seg_at(s)
	var p := _point_at(s)
	# Écart latéral, nul aux deux bouts du trajet (on arrive pile à sa place).
	var ahead := _point_at(minf(s + 1.5, _len))
	var dir := Vector2(ahead.x - p.x, ahead.z - p.z)
	if dir.length() > 0.01:
		dir = dir.normalized()
		var fade := clampf(minf(s, _len - s) / 3.0, 0.0, 1.0)
		var q := p + Vector3(-dir.y, 0, dir.x) * _lane * fade
		if nav == null or nav.is_free(q.x, q.z):
			p = q
		rotation.y = lerp_angle(rotation.y, atan2(-dir.x, -dir.y), minf(delta * TURN_RATE, 1.0))
	p = _with_sep(p)
	position = Vector3(p.x, _ground(p.x, p.z), p.z)
	if _t >= _dur:
		_path = PackedVector3Array()
		_rest = target
		return
	_animate_gait(_speed)


func _with_sep(p: Vector3) -> Vector3:
	var q := p + Vector3(_sep.x, 0, _sep.y)
	return q if nav == null or nav.is_free(q.x, q.z) else p


## Espace personnel : chaque habitant s'écarte doucement de ceux qui sont trop près de lui.
static func separate(list: Array) -> void:
	var r := PERSONAL * view_scale
	var grid := {}
	for a: AgentView in list:
		var key := Vector2i(floori(a.position.x / 2.0), floori(a.position.z / 2.0))
		if not grid.has(key):
			grid[key] = []
		grid[key].append(a)
	for a: AgentView in list:
		var push := Vector2.ZERO
		var key := Vector2i(floori(a.position.x / 2.0), floori(a.position.z / 2.0))
		for j in range(-1, 2):
			for i in range(-1, 2):
				for b: AgentView in grid.get(key + Vector2i(i, j), []):
					if b == a:
						continue
					var d := Vector2(a.position.x - b.position.x, a.position.z - b.position.z)
					var l := d.length()
					if l < r:
						var away := d / l if l > 0.01 else Vector2.from_angle(float(a.agent_id.hash() % 628) / 100.0)
						push += away * (r - l)
		# L'écart se résorbe de lui-même une fois la place libre (on revient à sa place dans la file).
		a._sep_goal = (a._sep * 0.9 + push).limit_length(1.2)


## Choisit l'allure (marche, petite course, course) et règle la cadence sur la vitesse au sol.
func _animate_gait(v: float) -> void:
	if v < 0.15:
		_play("Idle", 1.0)
		return
	var g := GAIT_WALK
	var hurry := _req_hurry or v > 7.5
	if v > 1.9 or (_anim == GAIT_JOG["anim"] and v > 1.6):
		g = GAIT_JOG
	if hurry and (v > 4.6 or (_anim == GAIT_SPRINT["anim"] and v > 4.0)):
		g = GAIT_SPRINT
	var anim: String = g["anim"]
	if g == GAIT_WALK and role != "deposant":
		anim = "Walk_Formal"   # le personnel de la Caisse marche d'un pas plus posé
	_play(anim, clampf(v / (g["speed"] * _model.scale.x), 0.5, MAX_ANIM_SCALE))


func _play(anim: String, speed: float) -> void:
	if _ap == null:
		return
	_ap.speed_scale = speed
	if anim != _anim:
		_anim = anim
		_ap.play(anim, 0.25)


# ---------------------------------------------------------------- trajets

## Se rendre en p en au moins `duration` secondes réelles. Le temps simulé est très comprimé
## (15 min en 0,5 s) : suivre l'horloge à la lettre ferait traverser la vallée en une fraction de
## seconde. On marche donc à une allure humaine, quitte à arriver un peu après le moteur (au plus
## LAG_STEPS pas) ; le prochain instantané corrige toute dérive. hurry : l'agent se rend à l'agence.
func move_to(p: Vector3, duration: float, hurry := false) -> void:
	var d := Vector2(p.x - position.x, p.z - position.z).length()
	if d < 0.05 and _path.is_empty():
		return
	target = p
	_req_min = duration
	_req_hurry = hurry
	_has_face = false
	if not (self in _pending):
		_pending.append(self)


## Calcule les chemins en attente, dans la limite de budget_ms millisecondes par image.
static func process_pending(budget_ms: float) -> void:
	var t0 := Time.get_ticks_usec()
	while not _pending.is_empty() and (Time.get_ticks_usec() - t0) < budget_ms * 1000.0:
		var a: AgentView = _pending.pop_front()
		if is_instance_valid(a):
			a._start_trip()


static func clear_pending() -> void:
	_pending.clear()


func _start_trip() -> void:
	var walking := _path.size() >= 2 and _t > 0.0 and _t < _dur
	var from := position
	if nav:
		_path = nav.find_path(from, target)
	else:
		_path = PackedVector3Array([from, target])
	_cum = PackedFloat32Array([0.0])
	for i in range(1, _path.size()):
		_cum.append(_cum[i - 1] + Vector2(_path[i].x - _path[i - 1].x, _path[i].z - _path[i - 1].z).length())
	_len = _cum[_cum.size() - 1]
	_seg = 0
	if _len < 0.05:
		_path = PackedVector3Array()
		_rest = target
		return
	var pref := PACE_STROLL
	if _req_hurry:
		pref = PACE_PANIC if peur > 0.6 else PACE_HURRY
	if _len < 6.0:
		pref = minf(pref, PACE_STROLL)   # quelques pas : on ne court pas pour avancer dans la file
	var max_d := maxf(LAG_STEPS * tick_interval, _req_min)
	_dur = clampf(_len / (pref * _pace), maxf(_req_min, MIN_TRIP), max_d)
	_v0 = _speed if walking else 0.0
	_ramp_in = minf(0.2 if walking else 0.6, _dur * 0.3)
	_ramp_out = minf(0.6, _dur * 0.3)
	_vc = (_len - _v0 * _ramp_in * 0.5) / maxf(_dur - _ramp_in * 0.5 - _ramp_out * 0.5, 0.05)
	# Un départ décalé de 0 à 0,4 s, sauf si l'agent marche déjà.
	_t = 0.0 if walking else -randf() * 0.4


## Profil de vitesse en trapèze : accélération, croisière, ralentissement à l'arrivée.
func _speed_at(t: float) -> float:
	if t < _ramp_in:
		return lerpf(_v0, _vc, t / _ramp_in)
	if t > _dur - _ramp_out:
		return _vc * (_dur - t) / _ramp_out
	return _vc


func _distance_at(t: float) -> float:
	var s: float
	if t < _ramp_in:
		s = _v0 * t + 0.5 * (_vc - _v0) * t * t / _ramp_in
	else:
		s = (_v0 + _vc) * 0.5 * _ramp_in + _vc * (minf(t, _dur - _ramp_out) - _ramp_in)
		if t > _dur - _ramp_out:
			var u := t - (_dur - _ramp_out)
			s += _vc * u - 0.5 * _vc * u * u / _ramp_out
	return clampf(s, 0.0, _len)


## Segment du trajet qui contient l'abscisse s (recherche à partir du segment courant : s ne fait que croître).
func _seg_at(s: float) -> int:
	var i := _seg if s >= _cum[_seg] else 0
	while i < _path.size() - 2 and _cum[i + 1] < s:
		i += 1
	return i


func _point_at(s: float) -> Vector3:
	var i := _seg_at(s)
	var l := _cum[i + 1] - _cum[i]
	var f := 0.0 if l <= 0.0 else (s - _cum[i]) / l
	return _path[i].lerp(_path[i + 1], f)


## Une fois arrivé (et immobile), se tourner vers p : le guichet, la personne qui parle…
func face_toward(p: Vector3) -> void:
	_face = p
	_has_face = true


func is_walking() -> bool:
	return _path.size() >= 2 and _t < _dur


# ---------------------------------------------------------------- état

func apply_snapshot(row: Dictionary) -> void:
	etat = row["etat"]
	lieu = row["lieu"]
	peur = float(row["peur"])
	estime = float(row["estime_autres"])
	depot = float(row["depot"])
	retire = bool(row["retire"])
	perdu = float(row["perdu"])
	update_color()


func remember(text: String) -> void:
	conversations.append(text)
	if conversations.size() > 6:
		conversations.pop_front()


func say(text: String) -> void:
	if text == "":
		return
	_bubble.text = text
	_bubble.visible = true
	_talk_t = 4.5


func set_selected(v: bool) -> void:
	if _ring:
		_ring.visible = v


func color_name() -> String:
	if perdu > 0.0:
		return "a perdu de l'argent"
	if retire:
		return "a retiré"
	if role != "deposant":
		return "personnel de la Caisse"
	if etat in ["vers_agence", "en_file", "attente_ouverture"] or intention == "retirer":
		return "va retirer"
	if intention == "attendre" or peur > 0.35:
		return "inquiet"
	return "calme"


func update_color() -> void:
	if _body == null:
		return
	var c := COL_CALM
	match color_name():
		"a perdu de l'argent":
			c = COL_LOST
		"a retiré":
			c = COL_DONE
		"personnel de la Caisse":
			c = COL_STAFF
		"va retirer":
			c = COL_RUN
		"inquiet":
			c = COL_WORRIED
	_body.set_surface_override_material(0, _shared_mat(c))
