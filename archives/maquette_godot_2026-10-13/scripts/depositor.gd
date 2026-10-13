class_name Depositor
extends Node3D
## Un déposant de la vallée. Il vit, sort au café ou au marché, discute,
## observe les files d'attente et décide (ou non) de retirer son argent.
##
## Croyances :
##   fear       : croyance personnelle que la Caisse est en danger (0..1)
##   est_others : estimation de la part des AUTRES qui vont retirer (0..1)
##                -> c'est le raisonnement de second ordre du bank run.

enum State { HOME, WALKING, CHATTING, TO_BANK, QUEUING }

const COL_CALM := Color("4e8a5e")
const COL_WORRIED := Color("d9822b")
const COL_RUN := Color("b03c1c")
const COL_DONE := Color("8a8f93")
const COL_LOST := Color("1d1d1d")

var sim: Node
var display_name := ""
var archetype := ""
var named := false
var deposit := 0.0
var initial_deposit := 0.0
var home := Vector3.ZERO
var state := State.HOME
var target := Vector3.ZERO
var target_place := ""
var speed := 5.5

var fear := 0.0
var est_others := 0.0
var trust := 0.7
var loyalty := 0.5
var threshold := 0.6
var connected := false
var informed := false
var withdrawn := false
var lost := 0.0
var last_talk := -100.0

var agency: Agency = null
var queue_index := -1

var _wait := 0.0
var _think := 0.0
var _mat: StandardMaterial3D
var _bubble: Label3D
var _bubble_t := 0.0


func _ready() -> void:
	_mat = StandardMaterial3D.new()
	_mat.roughness = 0.9
	var body := MeshInstance3D.new()
	var cap := CapsuleMesh.new()
	cap.radius = 0.38 if not named else 0.5
	cap.height = 1.7 if not named else 2.2
	body.mesh = cap
	body.position.y = cap.height * 0.5
	body.material_override = _mat
	add_child(body)
	var head := MeshInstance3D.new()
	var hs := SphereMesh.new()
	hs.radius = 0.3 if not named else 0.4
	hs.height = hs.radius * 2.0
	head.mesh = hs
	var hm := StandardMaterial3D.new()
	hm.albedo_color = Color("e2c3a4")
	head.material_override = hm
	head.position.y = cap.height + hs.radius * 0.7
	add_child(head)
	if named:
		var tag := Label3D.new()
		tag.text = display_name
		tag.billboard = BaseMaterial3D.BILLBOARD_ENABLED
		tag.no_depth_test = true
		tag.font_size = 40
		tag.outline_size = 10
		tag.pixel_size = 0.025
		tag.position.y = 4.2
		add_child(tag)
	_bubble = Label3D.new()
	_bubble.billboard = BaseMaterial3D.BILLBOARD_ENABLED
	_bubble.no_depth_test = true
	_bubble.font_size = 34
	_bubble.outline_size = 10
	_bubble.pixel_size = 0.022
	_bubble.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_bubble.width = 520.0
	_bubble.position.y = 6.0 if named else 3.6
	_bubble.modulate = Color("fff6d8")
	_bubble.visible = false
	add_child(_bubble)
	global_position = home
	_wait = sim.rng.randf_range(0.5, 8.0)
	_think = sim.rng.randf_range(0.0, 1.0)
	_update_color()


func _process(delta: float) -> void:
	if _bubble_t > 0.0:
		_bubble_t -= delta
		if _bubble_t <= 0.0:
			_bubble.visible = false
	match state:
		State.HOME:
			_wait -= delta
			if _wait <= 0.0:
				_pick_outing()
		State.CHATTING:
			_wait -= delta
			if _wait <= 0.0:
				_go_home()
		_:
			_move(delta)
	_think -= delta
	if _think <= 0.0:
		_think = 1.0
		_perceive()
		_decide()
		_update_color()


# ---------------------------------------------------------------- déplacement

func _move(delta: float) -> void:
	var here := global_position
	var to := Vector2(target.x - here.x, target.z - here.z)
	var dist := to.length()
	if dist < 0.35:
		_arrive()
		return
	var step := minf(speed * delta, dist)
	var dir := to / dist
	var nx := here.x + dir.x * step
	var nz := here.z + dir.y * step
	global_position = Vector3(nx, WorldData.height_world(nx, nz), nz)
	rotation.y = atan2(-dir.x, -dir.y)


func _arrive() -> void:
	match state:
		State.WALKING:
			if target_place == "home":
				state = State.HOME
				_wait = sim.rng.randf_range(4.0, 14.0)
			else:
				state = State.CHATTING
				_wait = sim.rng.randf_range(5.0, 11.0)
				sim.on_arrive_place(self, target_place)
		State.TO_BANK:
			if agency == null or not agency.is_open:
				go_to_bank()
				return
			queue_index = agency.join(self)
			state = State.QUEUING
			target = agency.queue_slot(queue_index)
		_:
			pass


func _pick_outing() -> void:
	target_place = sim.pick_place(self)
	target = sim.place_point(target_place)
	state = State.WALKING


func _go_home() -> void:
	target_place = "home"
	target = home
	state = State.WALKING


# ---------------------------------------------------------------- croyances et décision

func _perceive() -> void:
	# 1. Les files devant les agences proches.
	for ag in sim.agency_list:
		if ag.global_position.distance_to(global_position) < 35.0:
			var seen := clampf(ag.queue.size() / 8.0, 0.0, 1.0)
			# Une file qui s'allonge inquiète vite ; une agence calme rassure lentement.
			est_others = lerpf(est_others, seen, 0.3 if seen > est_others else 0.03)
	# 2. L'humeur des gens autour de moi (café, marché, rue).
	var around := 0
	var anxious := 0
	for o in sim.agents:
		if o == self:
			continue
		if o.global_position.distance_squared_to(global_position) < 225.0:
			around += 1
			if o.withdrawn or o.state == State.TO_BANK or o.state == State.QUEUING:
				anxious += 2
			elif o.fear > 0.35:
				anxious += 1
	if around >= 2:
		var mood := clampf(float(anxious) / (around * 2.0), 0.0, 1.0)
		if mood > est_others:
			est_others = lerpf(est_others, mood, 0.15)
	sim.press_check(self)
	# L'inquiétude retombe lentement si rien ne l'entretient.
	fear = maxf(0.0, fear - 0.0015)
	est_others = maxf(0.0, est_others - 0.002)


## Pression à retirer, entre 0 et 1.
func pressure() -> float:
	var cover: float = sim.guarantee_cover(deposit)
	var exposure := 1.0 - 0.85 * cover
	var base := 0.4 * fear + 0.6 * est_others
	if est_others > 0.25:
		base += 0.15 * est_others   # peur d'être le dernier servi
	return clampf(base * exposure * (1.1 - 0.35 * loyalty), 0.0, 1.0)


func _decide() -> void:
	if withdrawn or sim.caisse.failed:
		return
	if state == State.TO_BANK or state == State.QUEUING:
		return
	if pressure() > threshold:
		go_to_bank()


func go_to_bank() -> void:
	agency = sim.nearest_open_agency(global_position)
	if agency == null:
		_go_home()
		return
	state = State.TO_BANK
	target = agency.queue_slot(agency.queue.size())
	sim.on_decision(self)


func inform(strength: float) -> void:
	informed = true
	fear = clampf(fear + strength, 0.0, 1.0)
	# Second ordre : « si moi je l'ai lu, les autres aussi ».
	est_others = clampf(est_others + strength * 0.4, 0.0, 1.0)


func is_at_counter() -> bool:
	return state == State.QUEUING and queue_index == 0 and \
		Vector2(global_position.x - target.x, global_position.z - target.z).length() < 0.6


func set_queue_index(i: int) -> void:
	queue_index = i
	if agency:
		target = agency.queue_slot(i)


func on_served(paid: float, requested: float, p_lost: float) -> void:
	deposit = maxf(0.0, deposit - paid - p_lost)
	lost += p_lost
	agency = null
	queue_index = -1
	if deposit <= 0.01 or p_lost > 0.0:
		withdrawn = true
	else:
		# Retrait plafonné : il reviendra, et la limite l'inquiète.
		fear = clampf(fear + 0.1, 0.0, 1.0)
	if paid + p_lost < requested - 0.01:
		withdrawn = true
	_go_home()
	_update_color()


func on_agency_closed(_ag: Agency) -> void:
	fear = clampf(fear + 0.2, 0.0, 1.0)
	est_others = clampf(est_others + 0.15, 0.0, 1.0)
	agency = null
	queue_index = -1
	go_to_bank()


## Défaut de la Caisse : ce qui n'est pas garanti est perdu.
func finalize_default(covered: float) -> void:
	if withdrawn:
		return
	lost = maxf(0.0, deposit - covered)
	deposit = 0.0
	withdrawn = true
	agency = null
	queue_index = -1
	_go_home()
	_update_color()


func say(text: String) -> void:
	_bubble.text = text
	_bubble.visible = true
	_bubble_t = 4.5


func profile() -> Dictionary:
	return {
		"nom": display_name,
		"profil": archetype,
		"depot": int(deposit),
		"peur": snappedf(fear, 0.01),
		"estime_que_les_autres_retirent": snappedf(est_others, 0.01),
		"loyaute": snappedf(loyalty, 0.01),
		"confiance": snappedf(trust, 0.01),
		"deja_retire": withdrawn,
		"en_route_vers_agence": state == State.TO_BANK or state == State.QUEUING,
	}


func _update_color() -> void:
	var c := COL_CALM
	if lost > 0.0:
		c = COL_LOST
	elif withdrawn:
		c = COL_DONE
	elif state == State.TO_BANK or state == State.QUEUING:
		c = COL_RUN
	elif pressure() > threshold * 0.6:
		c = COL_WORRIED
	_mat.albedo_color = c
