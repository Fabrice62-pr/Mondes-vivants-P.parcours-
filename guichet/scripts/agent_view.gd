class_name AgentView
extends Node3D
## Représentation d'un agent dans la vallée. Toute la logique (croyances, décisions) vit dans le moteur
## Python : cette vue se contente d'afficher l'état reçu et d'animer les déplacements.

const COL_CALM := Color("4e8a5e")
const COL_WORRIED := Color("d9822b")
const COL_RUN := Color("b03c1c")
const COL_DONE := Color("8a8f93")
const COL_LOST := Color("1d1d1d")
const COL_STAFF := Color("2f5d8a")

# Déplacements à l'écran (voir move_to)
const WALK_SPEED := 28.0   # m/s à l'écran (45 avant le 13/10 : jugé trop rapide)
const MIN_TRIP := 1.2      # s
const MAX_TRIP := 6.0      # s, au-delà l'agent accélère pour ne pas trop décrocher du moteur

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
var target := Vector3.ZERO
var _from := Vector3.ZERO           # départ du trajet en cours
var _move_t := 0.0                  # temps écoulé sur le trajet (s), négatif pendant le délai de départ
var _move_d := 0.0                  # durée du trajet (s réelles)
var _pace := 1.0                    # allure propre à l'agent (±15 %)
var _step_phase := 0.0
var _mat: StandardMaterial3D
var _bubble: Label3D
var _bubble_t := 0.0
var _ring: MeshInstance3D


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
	home = home_pos + Vector3(offset.x, 0, offset.y)
	home.y = WorldData.height_world(home.x, home.z)
	_pace = 0.85 + ((h >> 16) & 0xff) / 255.0 * 0.3
	position = home
	target = home
	_from = home


func _ready() -> void:
	_mat = StandardMaterial3D.new()
	_mat.roughness = 0.9
	var body := MeshInstance3D.new()
	var cap := CapsuleMesh.new()
	cap.radius = 0.5 if named else 0.38
	cap.height = 2.2 if named else 1.7
	body.mesh = cap
	body.position.y = cap.height * 0.5
	body.material_override = _mat
	add_child(body)
	var head := MeshInstance3D.new()
	var hs := SphereMesh.new()
	hs.radius = 0.4 if named else 0.3
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
	_ring = MeshInstance3D.new()
	var tor := TorusMesh.new()
	tor.inner_radius = 0.9
	tor.outer_radius = 1.25
	_ring.mesh = tor
	var rm := StandardMaterial3D.new()
	rm.albedo_color = Color("ffe08a")
	rm.emission_enabled = true
	rm.emission = Color("ffe08a")
	rm.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	_ring.material_override = rm
	_ring.position.y = 0.1
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
	_bubble.position.y = 6.0 if named else 3.6
	_bubble.modulate = Color("fff6d8")
	_bubble.visible = false
	add_child(_bubble)
	update_color()


func _process(delta: float) -> void:
	if _bubble_t > 0.0:
		_bubble_t -= delta
		if _bubble_t <= 0.0:
			_bubble.visible = false
	if _move_t >= _move_d:
		return
	_move_t += delta
	if _move_t <= 0.0:
		return   # délai de départ : tout le monde ne part pas au même instant
	var k := clampf(_move_t / _move_d, 0.0, 1.0)
	var e := k * k * (3.0 - 2.0 * k)   # accélère au départ, ralentit à l'arrivée
	var nx := lerpf(_from.x, target.x, e)
	var nz := lerpf(_from.z, target.z, e)
	# Petit balancement de marche, qui s'éteint à l'arrêt.
	_step_phase += delta * 9.0 * _pace
	var bob := absf(sin(_step_phase)) * 0.12 * sin(k * PI)
	position = Vector3(nx, WorldData.height_world(nx, nz) + bob, nz)
	var dir := Vector2(target.x - _from.x, target.z - _from.z)
	if dir.length() > 0.01:
		rotation.y = lerp_angle(rotation.y, atan2(-dir.x, -dir.y), minf(delta * 8.0, 1.0))


## Se rendre en p en au moins `duration` secondes réelles. Le temps simulé est très comprimé
## (15 min en 0,5 s) : suivre l'horloge à la lettre ferait traverser la vallée en une fraction de
## seconde. On impose donc une allure visible, quitte à arriver un peu après le moteur ; le prochain
## instantané corrige toute dérive.
func move_to(p: Vector3, duration: float) -> void:
	var d := Vector2(p.x - position.x, p.z - position.z).length()
	if d < 0.05:
		return
	var moving := _move_t < _move_d and _move_t > 0.0
	_from = position
	target = p
	_move_d = maxf(duration, clampf(d / (WALK_SPEED * _pace), MIN_TRIP, MAX_TRIP))
	# Un départ décalé de 0 à 0,4 s, sauf si l'agent marche déjà.
	_move_t = 0.0 if moving else -randf() * 0.4


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
	_bubble_t = 4.5


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
	if _mat == null:
		return
	match color_name():
		"a perdu de l'argent":
			_mat.albedo_color = COL_LOST
		"a retiré":
			_mat.albedo_color = COL_DONE
		"personnel de la Caisse":
			_mat.albedo_color = COL_STAFF
		"va retirer":
			_mat.albedo_color = COL_RUN
		"inquiet":
			_mat.albedo_color = COL_WORRIED
		_:
			_mat.albedo_color = COL_CALM
