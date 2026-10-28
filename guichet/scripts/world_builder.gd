class_name WorldBuilder
extends Node3D
## Construit toute la vallée par script : terrain, rivière, routes, ponts, lampadaires,
## agences, lieux de rencontre, maisons, ateliers, arbres et herbe ; puis la grille de marche
## des habitants (NavGrid), qui reprend l'emprise au sol de tout ce qui a été posé.

const WINDOW_COLOR := "7d98a6"   # couleur des vitres dans les appels à _box : remplacée par du verre
const TERRAIN_X := 240.0          # demi-étendue du terrain (m) : au-delà de la vallée, des collines boisées
const TERRAIN_Z := 200.0

var rng := RandomNumberGenerator.new()
var nav := NavGrid.new()
var obstacles: Array = []      # Vector3(x_plan, z_plan, rayon)
var path_samples: Array = []   # Vector3(x_plan, z_plan, demi-largeur)
var river_samples: Array = []
var road_paths: Array = []     # {samples, w} : routes, pour la grille de marche et les lampadaires
var agencies := {}             # id -> Agency
var places := {}               # id -> Vector3 (monde)
var homes: Array = []          # Vector3 (monde) : portes des maisons
var home_dirs: Array = []      # Vector3 : direction « vers la rue » devant chaque porte
var labels: Array = []         # Label3D des lieux
var _mats := {}
var _footprints: Array = []    # [Transform3D, Vector2 demi-taille] : emprise des bâtiments
var _tree_spots: Array = []    # Vector3(x, z, rayon bloqué) en mètres
var _lamp_spots: Array = []    # Vector2 (monde)
var _bridges: Array = []       # [Transform3D, hauteur du tablier]
var _glass_lit: StandardMaterial3D
var _glass_dark: StandardMaterial3D
var _lamp_mat: StandardMaterial3D
var _lamp_lights: Array = []


func build() -> void:
	rng.seed = 7
	_glass_lit = _glass(Color("ffcf8a"))
	_glass_dark = _glass(Color("000000"))
	_lamp_mat = StandardMaterial3D.new()
	_lamp_mat.albedo_color = Color("f6ecd2")
	_lamp_mat.emission_enabled = true
	_lamp_mat.emission = Color("ffd9a0")
	_lamp_mat.emission_energy_multiplier = 0.0
	_build_terrain()
	river_samples = _ribbon(WorldData.RIVER, WorldData.RIVER_WIDTH, _shader_mat("res://shaders/eau.gdshader"), 0.12, true)
	var road_mat := _shader_mat("res://shaders/route.gdshader")
	for r in WorldData.ROADS:
		var samples := _ribbon(r["pts"], r["w"], road_mat, 0.16, true)
		road_paths.append({"samples": samples, "w": r["w"]})
		if r["bridge"]:
			_bridge_for(samples)
	_build_sites()
	for w in WorldData.WORKSHOPS:
		if _is_free(w.x, w.y, 7.0):
			_workshop(w.x, w.y, w.z)
			obstacles.append(Vector3(w.x, w.y, 8.0))
	for c in WorldData.HOUSE_CLUSTERS:
		_scatter_houses(c[0], c[1], c[2], c[3], c[4])
	_build_lamps()
	_build_trees()
	_build_nav()
	_build_grass()


## Nuit (0 = plein jour, 1 = nuit noire) : fenêtres et lampadaires s'allument.
func set_night(f: float) -> void:
	_glass_lit.emission_energy_multiplier = f * 2.4
	_lamp_mat.emission_energy_multiplier = f * 6.0
	for l in _lamp_lights:
		l.visible = f > 0.02
		l.light_energy = f * 2.2


# ---------------------------------------------------------------- utilitaires

func _mat(c: Color, cull_off := false) -> StandardMaterial3D:
	var key := c.to_html() + str(cull_off)
	if not _mats.has(key):
		var m := StandardMaterial3D.new()
		m.albedo_color = c
		m.roughness = 0.9
		if cull_off:
			m.cull_mode = BaseMaterial3D.CULL_DISABLED
		_mats[key] = m
	return _mats[key]


func _glass(glow: Color) -> StandardMaterial3D:
	var m := StandardMaterial3D.new()
	m.albedo_color = Color("4f6672")
	m.roughness = 0.12
	m.metallic = 0.35
	m.emission_enabled = true
	m.emission = glow
	m.emission_energy_multiplier = 0.0
	return m


func _shader_mat(path: String) -> ShaderMaterial:
	var m := ShaderMaterial.new()
	m.shader = load(path)
	return m


func _box(parent: Node3D, size: Vector3, c: String, pos: Vector3) -> MeshInstance3D:
	var mesh := BoxMesh.new()
	mesh.size = size
	var mi := MeshInstance3D.new()
	mi.mesh = mesh
	mi.material_override = _glass_lit if c == WINDOW_COLOR else _mat(Color(c))
	mi.position = pos
	parent.add_child(mi)
	return mi


## Toit à deux pans. base = position de la base du toit. Faîtage le long de X (along_x) ou de Z.
func _roof(parent: Node3D, length: float, width: float, h: float, c: String, base: Vector3, along_x := true) -> MeshInstance3D:
	var mesh := PrismMesh.new()
	mesh.size = Vector3(width, h, length)
	var mi := MeshInstance3D.new()
	mi.mesh = mesh
	mi.material_override = _mat(Color(c))
	mi.position = base + Vector3(0, h * 0.5, 0)
	if along_x:
		mi.rotation.y = PI * 0.5
	parent.add_child(mi)
	return mi


func _cyl(parent: Node3D, top: float, bottom: float, h: float, segs: int, c: String, pos: Vector3) -> MeshInstance3D:
	var mesh := CylinderMesh.new()
	mesh.top_radius = top
	mesh.bottom_radius = bottom
	mesh.height = h
	mesh.radial_segments = segs
	mesh.rings = 1
	var mi := MeshInstance3D.new()
	mi.mesh = mesh
	mi.material_override = _mat(Color(c))
	mi.position = pos
	parent.add_child(mi)
	return mi


func _group(x: float, z: float, rot := 0.0) -> Node3D:
	var g := Node3D.new()
	g.position = WorldData.to_world(x, z)
	g.rotation.y = rot
	add_child(g)
	return g


## Emprise au sol d'un élément (rectangle de taille `size` centré en `center`, repère de `parent`) :
## les habitants le contournent.
func _foot(parent: Node3D, size: Vector2, center := Vector3.ZERO) -> void:
	_footprints.append([parent.transform * Transform3D(Basis(), center), size * 0.5])


func _is_free(x: float, z: float, r: float) -> bool:
	for o in obstacles:
		if Vector2(x - o.x, z - o.y).length() < o.z + r:
			return false
	for p in path_samples:
		if Vector2(x - p.x, z - p.y).length() < p.z + r:
			return false
	return true


func _add_label(text: String, world_pos: Vector3, bank: bool) -> void:
	var l := Label3D.new()
	l.text = text
	l.billboard = BaseMaterial3D.BILLBOARD_ENABLED
	l.no_depth_test = true
	l.font_size = 56
	l.outline_size = 14
	l.pixel_size = 0.03
	l.modulate = Color("ffffff")
	l.outline_modulate = Color("b03c1c") if bank else Color("14251f")
	l.position = world_pos
	add_child(l)
	labels.append(l)


# ---------------------------------------------------------------- terrain

## Terrain lisse (normales calculées sur la fonction de hauteur), coloré par le shader terrain.
func _build_terrain() -> void:
	var step := 2.0
	var nx := int(TERRAIN_X * 2.0 / step)
	var nz := int(TERRAIN_Z * 2.0 / step)
	var verts := PackedVector3Array()
	var norms := PackedVector3Array()
	var idx := PackedInt32Array()
	verts.resize((nx + 1) * (nz + 1))
	norms.resize((nx + 1) * (nz + 1))
	for iz in nz + 1:
		for ix in nx + 1:
			var x := -TERRAIN_X + ix * step
			var z := -TERRAIN_Z + iz * step
			var y := WorldData.height_world(x, z)
			var dx := WorldData.height_world(x + 0.5, z) - WorldData.height_world(x - 0.5, z)
			var dz := WorldData.height_world(x, z + 0.5) - WorldData.height_world(x, z - 0.5)
			verts[iz * (nx + 1) + ix] = Vector3(x, y, z)
			norms[iz * (nx + 1) + ix] = Vector3(-dx, 1.0, -dz).normalized()
	for iz in nz:
		for ix in nx:
			var a := iz * (nx + 1) + ix
			var b := a + 1
			var c := a + nx + 1
			var d := c + 1
			idx.append_array([a, b, c, b, d, c])
	var arrays := []
	arrays.resize(Mesh.ARRAY_MAX)
	arrays[Mesh.ARRAY_VERTEX] = verts
	arrays[Mesh.ARRAY_NORMAL] = norms
	arrays[Mesh.ARRAY_INDEX] = idx
	var mesh := ArrayMesh.new()
	mesh.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arrays)
	var mi := MeshInstance3D.new()
	mi.name = "Terrain"
	mi.mesh = mesh
	mi.material_override = _shader_mat("res://shaders/terrain.gdshader")
	add_child(mi)


# ---------------------------------------------------------------- rivière, routes, ponts

## Ruban qui épouse le terrain (rivière ou route). UV.x : d'un bord à l'autre ; UV.y : longueur (m).
func _ribbon(pts: Array, width: float, mat: Material, y_off: float, register: bool) -> Array:
	var k := WorldData.S
	var samples := WorldData.sample_path(pts, 240)
	var verts := PackedVector3Array()
	var uvs := PackedVector2Array()
	var norms := PackedVector3Array()
	var idx := PackedInt32Array()
	var along := 0.0
	for i in samples.size():
		var p: Vector2 = samples[i]["p"]
		var t: Vector2 = samples[i]["t"]
		if i > 0:
			along += (p - (samples[i - 1]["p"] as Vector2)).length() * k
		var n := Vector2(-t.y, t.x) * (width * 0.5 / k)
		var a := p + n
		var b := p - n
		verts.append(Vector3(a.x * k, WorldData.height(a.x, a.y) + y_off, a.y * k))
		verts.append(Vector3(b.x * k, WorldData.height(b.x, b.y) + y_off, b.y * k))
		uvs.append(Vector2(0.0, along))
		uvs.append(Vector2(1.0, along))
		norms.append(Vector3.UP)
		norms.append(Vector3.UP)
		if i > 0:
			var j := i * 2
			idx.append_array([j - 2, j - 1, j, j - 1, j + 1, j])
		if register and i % 3 == 0:
			path_samples.append(Vector3(p.x, p.y, width * 0.5 / k + 1.2))
	var arrays := []
	arrays.resize(Mesh.ARRAY_MAX)
	arrays[Mesh.ARRAY_VERTEX] = verts
	arrays[Mesh.ARRAY_NORMAL] = norms
	arrays[Mesh.ARRAY_TEX_UV] = uvs
	arrays[Mesh.ARRAY_INDEX] = idx
	var mesh := ArrayMesh.new()
	mesh.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arrays)
	var mi := MeshInstance3D.new()
	mi.mesh = mesh
	mi.material_override = mat
	mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	add_child(mi)
	return samples


func _bridge_for(road: Array) -> void:
	var best_d := INF
	var best: Dictionary = {}
	for s in road:
		for r in river_samples:
			var dd: float = (s["p"] as Vector2).distance_to(r["p"])
			if dd < best_d:
				best_d = dd
				best = s
	if best_d >= 3.5:
		return
	var p: Vector2 = best["p"]
	var t: Vector2 = best["t"]
	var g := _group(p.x, p.y, -atan2(t.y, t.x))
	g.position.y = maxf(WorldData.height(p.x, p.y), 0.0) + 0.1
	_box(g, Vector3(11, 0.6, 4.6), "a79f92", Vector3(0, 0.35, 0))
	_box(g, Vector3(11, 0.9, 0.4), "8e877b", Vector3(0, 1.05, 2.1))
	_box(g, Vector3(11, 0.9, 0.4), "8e877b", Vector3(0, 1.05, -2.1))
	for px in [-5.3, 5.3]:
		for pz in [2.1, -2.1]:
			_box(g, Vector3(0.6, 1.3, 0.6), "7e776b", Vector3(px, 1.0, pz))
	_bridges.append([g.transform, g.position.y + 0.65])


## Lampadaires le long des routes, en quinconce, tous les 24 m environ.
func _build_lamps() -> void:
	var k := WorldData.S
	var side := 1.0
	for rp in road_paths:
		var samples: Array = rp["samples"]
		var w: float = rp["w"]
		var next := 6.0
		var along := 0.0
		for i in range(1, samples.size()):
			along += ((samples[i]["p"] as Vector2) - (samples[i - 1]["p"] as Vector2)).length() * k
			if along < next:
				continue
			next = along + 24.0
			var p: Vector2 = samples[i]["p"] * k
			var t: Vector2 = samples[i]["t"]
			var n := Vector2(-t.y, t.x) * side
			side = -side
			var q := p + n * (w * 0.5 + 0.8)
			if not _lamp_ok(q):
				continue
			_lamp(q, n)


func _lamp_ok(q: Vector2) -> bool:
	var k := WorldData.S
	for o in obstacles:
		if Vector2(q.x / k - o.x, q.y / k - o.y).length() < o.z + 0.8:
			return false
	for r in river_samples:
		if (q / k).distance_to(r["p"]) < 6.0:
			return false
	for l in _lamp_spots:
		if q.distance_to(l) < 12.0:
			return false
	return true


func _lamp(q: Vector2, from_road: Vector2) -> void:
	var g := Node3D.new()
	g.position = Vector3(q.x, WorldData.height_world(q.x, q.y), q.y)
	g.rotation.y = atan2(from_road.x, from_road.y)   # le bras se tend vers la route
	add_child(g)
	_cyl(g, 0.06, 0.09, 4.4, 6, "3b3f42", Vector3(0, 2.2, 0))
	_box(g, Vector3(0.08, 0.08, 0.9), "3b3f42", Vector3(0, 4.35, -0.4))
	var head := MeshInstance3D.new()
	var sm := SphereMesh.new()
	sm.radius = 0.2
	sm.height = 0.34
	sm.radial_segments = 10
	sm.rings = 5
	head.mesh = sm
	head.material_override = _lamp_mat
	head.position = Vector3(0, 4.2, -0.8)
	g.add_child(head)
	var light := OmniLight3D.new()
	light.light_color = Color("ffcf94")
	light.omni_range = 11.0
	light.omni_attenuation = 1.4
	light.light_energy = 0.0
	light.visible = false
	light.position = Vector3(0, 3.9, -0.8)
	g.add_child(light)
	_lamp_lights.append(light)
	_lamp_spots.append(q)
	obstacles.append(Vector3(q.x / WorldData.S, q.y / WorldData.S, 0.6))


# ---------------------------------------------------------------- lieux

func _build_sites() -> void:
	for s in WorldData.SITES:
		var x: float = s["x"]
		var z: float = s["z"]
		var id: String = s["id"]
		var label_h := 11.0
		match id:
			"bourg", "ag_marche", "zone":
				agencies[id] = _agency(id, s["name"], x, z)
			"cafe":
				_cafe(x, z)
				label_h = 9.0
			"marche":
				_market(x, z)
				label_h = 6.0
			"ecole":
				_school(x, z)
				label_h = 10.0
			"boulangerie":
				_bakery(x, z)
				label_h = 9.0
			"presse":
				_press(x, z)
				label_h = 12.0
		var wp := WorldData.to_world(x, z)
		places[id] = wp + Vector3(0, 0, 9)
		_add_label(s["name"], wp + Vector3(0, label_h, 0), s["kind"] == "agency")
	_add_label("Rivière", WorldData.to_world(-48, 2) + Vector3(0, 3, 0), false)
	_add_label("N", WorldData.to_world(0, -64) + Vector3(0, 5, 0), false)


func _agency(id: String, display: String, x: float, z: float) -> Agency:
	var ag := Agency.new()
	ag.name = "Agence_" + id
	ag.agency_id = id
	ag.display_name = display
	ag.position = WorldData.to_world(x, z)
	add_child(ag)
	_box(ag, Vector3(12, 0.5, 8.5), "b9b2a3", Vector3(0, 0.25, 0))
	_box(ag, Vector3(11, 5, 7.5), "efe7d6", Vector3(0, 3, 0))
	_box(ag, Vector3(11.2, 0.35, 7.7), "d8cfbd", Vector3(0, 5.45, 0))
	_roof(ag, 11.8, 8.6, 3, "2c4a3f", Vector3(0, 5.5, 0))
	_box(ag, Vector3(3, 3.2, 0.3), "14251f", Vector3(0, 2.1, 3.85))
	_box(ag, Vector3(7, 1.1, 0.3), "b03c1c", Vector3(0, 4.6, 3.9))
	for px in [-3.8, 3.8]:
		_box(ag, Vector3(2, 1.8, 0.25), WINDOW_COLOR, Vector3(px, 2.8, 3.8))
		_box(ag, Vector3(2.3, 0.2, 0.4), "d8cfbd", Vector3(px, 1.8, 3.9))
	for pz in [-1.8, 1.8]:
		for px in [-5.55, 5.55]:
			_box(ag, Vector3(0.25, 1.8, 1.6), WINDOW_COLOR, Vector3(px, 2.8, pz))
	for px in [-1.9, 1.9]:
		_box(ag, Vector3(0.5, 3.6, 0.5), "e8e0cf", Vector3(px, 2.3, 4.4))
	_foot(ag, Vector2(12, 8.5))
	for px in [-1.9, 1.9]:
		_foot(ag, Vector2(0.5, 0.5), Vector3(px, 0, 4.4))
	obstacles.append(Vector3(x, z, 8.0))
	return ag


func _cafe(x: float, z: float) -> void:
	var g := _group(x, z)
	_box(g, Vector3(9, 4, 6.5), "f2e6cf", Vector3(0, 2, 0))
	_roof(g, 9.6, 7.4, 2.2, "7a3b2e", Vector3(0, 4, 0))
	_box(g, Vector3(9.4, 0.25, 2.2), "b03c1c", Vector3(0, 3.2, 4.2))
	_box(g, Vector3(2, 2.6, 0.2), "3d2a20", Vector3(0, 1.3, 3.3))
	for px in [-3.0, 3.0]:
		_box(g, Vector3(2.2, 1.6, 0.2), WINDOW_COLOR, Vector3(px, 1.9, 3.28))
	var ums := ["b03c1c", "2f6f5e", "c59b3a", "b03c1c"]
	var spots := [Vector2(-3, 7.5), Vector2(0, 8.2), Vector2(3, 7.5), Vector2(-1.5, 10.5)]
	for i in spots.size():
		var p: Vector2 = spots[i]
		_box(g, Vector3(1.4, 0.1, 1.4), "ede8dd", Vector3(p.x, 0.95, p.y))
		_box(g, Vector3(0.15, 0.9, 0.15), "6d6a62", Vector3(p.x, 0.45, p.y))
		_cyl(g, 0.0, 1.6, 0.8, 8, ums[i], Vector3(p.x, 2.6, p.y))
		_box(g, Vector3(0.08, 2.2, 0.08), "6d6a62", Vector3(p.x, 1.6, p.y))
		for c in [Vector2(-0.95, 0), Vector2(0.95, 0)]:
			_box(g, Vector3(0.45, 0.06, 0.45), "8a5a3b", Vector3(p.x + c.x, 0.5, p.y + c.y))
			_box(g, Vector3(0.06, 0.5, 0.06), "6d6a62", Vector3(p.x + c.x, 0.25, p.y + c.y))
		_foot(g, Vector2(1.0, 1.0), Vector3(p.x, 0, p.y))
	_foot(g, Vector2(9, 6.5))
	obstacles.append(Vector3(x, z, 9.0))


func _market(x: float, z: float) -> void:
	var g := _group(x, z)
	_cyl(g, 12, 12, 0.25, 32, "d9cfb9", Vector3(0, 0.12, 0))
	var tops := ["d9822b", "2f6f5e", "e3c14b", "b03c1c", "3b5b8c", "d9822b"]
	var goods := ["d94f2b", "7fa83a", "e8c547", "c0392b", "f0e0c0", "a0522d"]
	for i in 6:
		var a := i / 6.0 * TAU
		var st := Node3D.new()
		st.position = Vector3(cos(a) * 7.5, 0, sin(a) * 7.5)
		st.rotation.y = -a + PI * 0.5
		g.add_child(st)
		_box(st, Vector3(3.6, 1, 1.8), "a4865d", Vector3(0, 0.5, 0))
		for j in 3:
			_box(st, Vector3(0.9, 0.25, 0.6), goods[(i + j) % 6], Vector3(-1.1 + j * 1.1, 1.12, 0.35))
		for c in [Vector2(-1.7, -0.8), Vector2(1.7, -0.8), Vector2(-1.7, 0.8), Vector2(1.7, 0.8)]:
			_box(st, Vector3(0.12, 2.6, 0.12), "6d6a62", Vector3(c.x, 1.3, c.y))
		_box(st, Vector3(4, 0.2, 2.4), tops[i], Vector3(0, 2.7, 0))
		_footprints.append([g.transform * st.transform, Vector2(1.9, 1.0)])
	_cyl(g, 1.4, 1.6, 0.9, 12, "b5ae9f", Vector3(0, 0.6, 0))
	_cyl(g, 0.15, 0.2, 1.4, 8, "9a9488", Vector3(0, 1.5, 0))
	_foot(g, Vector2(3.4, 3.4))
	obstacles.append(Vector3(x, z, 13.0))


func _school(x: float, z: float) -> void:
	var g := _group(x, z)
	_box(g, Vector3(18, 5, 7), "e9dfc8", Vector3(0, 2.5, 0))
	_roof(g, 18.6, 8, 2.6, "5e6f7c", Vector3(0, 5, 0))
	_box(g, Vector3(7, 5, 10), "e9dfc8", Vector3(-5.5, 2.5, 8.5))
	_roof(g, 10.6, 7.6, 2.6, "5e6f7c", Vector3(-5.5, 5, 8.5), false)
	_box(g, Vector3(12, 0.12, 10), "c9b98f", Vector3(5, 0.07, 9))
	_box(g, Vector3(0.18, 9, 0.18), "8a8f93", Vector3(9.5, 4.5, 4.5))
	_box(g, Vector3(2.2, 1.3, 0.06), "3b5b8c", Vector3(10.6, 8.2, 4.5))
	for i in 7:
		_box(g, Vector3(1.4, 1.6, 0.2), WINDOW_COLOR, Vector3(-7 + i * 2.4, 2.6, 3.55))
	_foot(g, Vector2(18, 7))
	_foot(g, Vector2(7, 10), Vector3(-5.5, 0, 8.5))
	_foot(g, Vector2(0.3, 0.3), Vector3(9.5, 0, 4.5))
	obstacles.append(Vector3(x, z, 14.0))


func _bakery(x: float, z: float) -> void:
	var g := _group(x, z)
	_box(g, Vector3(9, 0.4, 7.4), "b9b2a3", Vector3(0, 0.2, 0))
	_box(g, Vector3(8, 4.2, 6.4), "f3ddb0", Vector3(0, 2.5, 0))
	_roof(g, 8.6, 7.4, 2.6, "9c4f36", Vector3(0, 4.6, 0))
	_box(g, Vector3(1, 2.4, 1), "8a5a44", Vector3(2.6, 6, -1.2))
	for i in 6:
		_box(g, Vector3(1.35, 0.25, 2), "f5ebdd" if i % 2 else "8a5a3b", Vector3(-3.4 + i * 1.36, 3.6, 4.1))
	_box(g, Vector3(4.2, 1.9, 0.2), "e9c77b", Vector3(-1.4, 1.9, 3.25))
	_box(g, Vector3(1.4, 2.6, 0.2), "5b4636", Vector3(2.6, 1.7, 3.25))
	_box(g, Vector3(5.2, 0.9, 0.3), "c59b3a", Vector3(0, 4.3, 3.3))
	var loaf := MeshInstance3D.new()
	var sm := SphereMesh.new()
	sm.radius = 1.0
	sm.height = 2.0
	loaf.mesh = sm
	loaf.material_override = _mat(Color("c98b45"))
	loaf.scale = Vector3(1.6, 0.6, 0.75)
	loaf.position = Vector3(0, 5.2, 3.3)
	g.add_child(loaf)
	_box(g, Vector3(2.4, 0.2, 0.7), "7a5a3e", Vector3(-2.2, 0.7, 5.4))
	_foot(g, Vector2(9, 7.4))
	_foot(g, Vector2(2.4, 0.7), Vector3(-2.2, 0, 5.4))
	obstacles.append(Vector3(x, z, 7.0))


func _press(x: float, z: float) -> void:
	var g := _group(x, z)
	_box(g, Vector3(8, 0.4, 7), "b9b2a3", Vector3(0, 0.2, 0))
	_box(g, Vector3(7, 7, 6), "d7dee2", Vector3(0, 3.9, 0))
	_roof(g, 7.6, 6.8, 2.2, "3e4a54", Vector3(0, 7.4, 0))
	_box(g, Vector3(6.4, 1, 0.3), "3b5b8c", Vector3(0, 3.9, 3.2))
	_box(g, Vector3(3, 2.2, 0.2), WINDOW_COLOR, Vector3(-1.4, 2, 3.15))
	_box(g, Vector3(1.4, 2.6, 0.2), "2e3a42", Vector3(2.2, 1.7, 3.15))
	for px in [-2.0, 0.0, 2.0]:
		_box(g, Vector3(1.2, 1.4, 0.2), WINDOW_COLOR, Vector3(px, 5.8, 3.1))
	_cyl(g, 1.5, 1.5, 2.8, 6, "2f6f5e", Vector3(-5.5, 1.4, 6))
	_cyl(g, 0.0, 2.1, 1.2, 6, "22533f", Vector3(-5.5, 3.4, 6))
	for px in [1.4, 3.0]:
		_box(g, Vector3(1.3, 1.5, 0.4), "6d6a62", Vector3(px, 0.75, 5.2))
		for i in 3:
			var sh := _box(g, Vector3(1.1, 0.05, 0.5), "f5f2ea", Vector3(px, 0.45 + i * 0.42, 5.35))
			sh.rotation.x = -0.5
		_foot(g, Vector2(1.3, 0.5), Vector3(px, 0, 5.2))
	_box(g, Vector3(1.6, 0.3, 1), "f5f2ea", Vector3(-1.5, 0.55, 5.6))
	_foot(g, Vector2(8, 7))
	_foot(g, Vector2(3.0, 3.0), Vector3(-5.5, 0, 6))
	_foot(g, Vector2(1.6, 1.0), Vector3(-1.5, 0, 5.6))
	obstacles.append(Vector3(x, z, 9.0))


# ---------------------------------------------------------------- habitat et végétation

func _house(x: float, z: float, rot: float, s: float) -> void:
	var g := _group(x, z, rot)
	g.scale = Vector3.ONE * s
	var roofs := ["a65a3e", "9c4f36", "8a5a44", "b06a48"]
	var walls := ["ede4d2", "e6dcc5", "f1ebdd", "dcd3bf"]
	var shutters := ["4f6f5a", "5a6f8a", "8a5a44", "6d7f86", "a0523a"]
	var shut: String = shutters[rng.randi_range(0, 4)]
	var glass: StandardMaterial3D = _glass_lit if rng.randf() < 0.65 else _glass_dark
	_box(g, Vector3(6.3, 0.45, 5.3), "a8a093", Vector3(0, 0.2, 0))
	_box(g, Vector3(6, 3.6, 5), walls[rng.randi_range(0, 3)], Vector3(0, 1.8, 0))
	_roof(g, 6.6, 5.8, 2.4, roofs[rng.randi_range(0, 3)], Vector3(0, 3.6, 0))
	_box(g, Vector3(1.2, 2, 0.2), "5b4636", Vector3(0, 1, 2.55))
	_box(g, Vector3(1.5, 0.18, 0.3), "8e877b", Vector3(0, 2.1, 2.6))
	_box(g, Vector3(1.6, 0.15, 0.9), "a8a093", Vector3(0, 0.08, 2.9))
	# Fenêtres (verre allumé la nuit dans deux maisons sur trois) et volets.
	for px in [-1.9, 1.9]:
		_window(g, glass, Vector3(px, 2.0, 2.52), false)
		_box(g, Vector3(0.38, 1.15, 0.08), shut, Vector3(px - 0.72, 2.0, 2.56))
		_box(g, Vector3(0.38, 1.15, 0.08), shut, Vector3(px + 0.72, 2.0, 2.56))
		_window(g, glass, Vector3(px * 0.8, 2.0, -2.52), false)
	for px in [-3.02, 3.02]:
		_window(g, glass, Vector3(px, 2.0, 0.0), true)
	if rng.randf() < 0.6:
		_box(g, Vector3(0.6, 1.8, 0.6), "8a6a58", Vector3(1.6, 4.6, -0.9))
	_foot(g, Vector2(6.3, 5.3))
	# La porte de la maison sert de domicile aux déposants.
	var door := g.transform * Vector3(0, 0, 4.0)
	homes.append(door)
	home_dirs.append(Vector3(door.x - g.position.x, 0, door.z - g.position.z).normalized())


func _window(g: Node3D, glass: Material, pos: Vector3, side: bool) -> void:
	var size := Vector3(0.08, 1.1, 1.0) if side else Vector3(1.0, 1.1, 0.08)
	var w := _box(g, size, "000000", pos)
	w.material_override = glass
	var sill := Vector3(0.25, 0.08, 1.2) if side else Vector3(1.2, 0.08, 0.25)
	_box(g, sill, "d8d0c0", pos + Vector3(0, -0.6, 0))


func _workshop(x: float, z: float, rot: float) -> void:
	var g := _group(x, z, rot)
	_box(g, Vector3(14, 4.5, 8), "b7b9b2", Vector3(0, 2.25, 0))
	for i in 3:
		_roof(g, 8.2, 4.4, 1.8, "77807c", Vector3(-4.6 + i * 4.6, 4.5, 0), false)
	_box(g, Vector3(4, 3.5, 0.25), "5d6764", Vector3(-3, 1.75, 4.05))
	for px in [2.0, 5.0]:
		_box(g, Vector3(1.8, 1.2, 0.2), WINDOW_COLOR, Vector3(px, 2.8, 4.05))
	_box(g, Vector3(1.6, 1.6, 1.6), "a7864f", Vector3(4, 0.8, 5.5))
	_box(g, Vector3(1.6, 1.6, 1.6), "9a7944", Vector3(5.8, 0.8, 5.2))
	_foot(g, Vector2(14, 8))
	_foot(g, Vector2(3.6, 2.0), Vector3(4.9, 0, 5.35))
	var door := g.position + Vector3(0, 0, 7)
	homes.append(door)
	home_dirs.append(Vector3(0, 0, 1))


func _scatter_houses(cx: float, cz: float, n: int, r_min: float, r_max: float) -> void:
	var placed := 0
	var tries := 0
	while placed < n and tries < 400:
		tries += 1
		var a := rng.randf() * TAU
		var r := r_min + rng.randf() * (r_max - r_min)
		var x := cx + cos(a) * r
		var z := cz + sin(a) * r
		if absf(x) > 70.0 or z < -36.0 or z > 58.0:
			continue
		if not _is_free(x, z, 3.2):
			continue
		_house(x, z, round(rng.randf() * 4.0) * PI * 0.5 + (rng.randf() - 0.5) * 0.3, 0.8 + rng.randf() * 0.35)
		obstacles.append(Vector3(x, z, 3.2))
		placed += 1


## Arbres : feuillus en fond de vallée, conifères sur les hauteurs, et une forêt tout autour.
## Trois MultiMesh (troncs, houppiers ronds, cônes) : des milliers d'arbres pour quelques appels de rendu.
func _build_trees() -> void:
	var trunks: Array = []
	var crowns: Array = []
	var cones: Array = []
	var greens := [Color("5b7d3e"), Color("4d6e35"), Color("6a8c47"), Color("7a9a4a"), Color("587a3a")]
	var autumn := [Color("b8862e"), Color("a5612a"), Color("c49a3a")]
	var firs := [Color("3e5e3a"), Color("35543a"), Color("466a40")]
	var spots: Array = []
	for i in 1150:
		var x := (rng.randf() - 0.5) * 152.0
		var z := (rng.randf() - 0.5) * 124.0
		var dense := 0.85 if WorldData.height(x, z) > 1.5 else 0.32
		if rng.randf() > dense:
			continue
		if not _is_free(x, z, 1.8):
			continue
		obstacles.append(Vector3(x, z, 1.6))
		spots.append(Vector2(x * WorldData.S, z * WorldData.S))
	for i in 2600:
		var wx := (rng.randf() - 0.5) * TERRAIN_X * 2.0
		var wz := (rng.randf() - 0.5) * TERRAIN_Z * 2.0
		if absf(wx) < 135.0 and absf(wz) < 110.0:
			continue
		spots.append(Vector2(wx, wz))
	for p in spots:
		var s := 0.75 + rng.randf() * 0.6
		var base := Vector3(p.x, WorldData.height_world(p.x, p.y), p.y)
		var rot := Basis(Vector3.UP, rng.randf() * TAU)
		var fir := base.y > 6.0 and rng.randf() < 0.75
		var inside := absf(p.x) < 140.0 and absf(p.y) < 114.0
		if fir:
			trunks.append(Transform3D(rot.scaled(Vector3(s, s * 0.6, s)), base + Vector3(0, 0.9 * s * 0.6, 0)))
			var col: Color = firs[rng.randi_range(0, 2)]
			for k in 3:
				var cs := s * (1.0 - k * 0.25)
				cones.append([Transform3D(rot.scaled(Vector3(cs, s, cs)), base + Vector3(0, (1.9 + k * 1.25) * s, 0)), col.darkened(k * -0.06)])
			if inside:
				_tree_spots.append(Vector3(p.x, p.y, 1.3 * s))
		else:
			trunks.append(Transform3D(rot.scaled(Vector3(s, s, s)), base + Vector3(0, 1.4 * s, 0)))
			var col: Color = greens[rng.randi_range(0, 4)]
			if rng.randf() < 0.12:
				col = autumn[rng.randi_range(0, 2)]
			var parts := [[Vector3(0, 4.0, 0), 1.6], [Vector3(0.75, 3.55, 0.3), 1.15], [Vector3(-0.6, 3.6, -0.45), 1.2],
				[Vector3(0.1, 3.4, 0.8), 1.0]]
			for part in parts:
				var o: Vector3 = rot * (part[0] as Vector3) * s
				var r: float = part[1] * s * (0.9 + rng.randf() * 0.2)
				crowns.append([Transform3D(rot.scaled(Vector3(r, r * 0.85, r)), base + o), col.lightened(rng.randf() * 0.08)])
			if inside:
				_tree_spots.append(Vector3(p.x, p.y, 0.45 * s))
	var trunk_mesh := CylinderMesh.new()
	trunk_mesh.top_radius = 0.13
	trunk_mesh.bottom_radius = 0.22
	trunk_mesh.height = 2.8
	trunk_mesh.radial_segments = 6
	trunk_mesh.rings = 1
	var crown_mesh := SphereMesh.new()
	crown_mesh.radius = 1.0
	crown_mesh.height = 2.0
	crown_mesh.radial_segments = 10
	crown_mesh.rings = 6
	var cone_mesh := CylinderMesh.new()
	cone_mesh.top_radius = 0.0
	cone_mesh.bottom_radius = 1.7
	cone_mesh.height = 2.6
	cone_mesh.radial_segments = 8
	cone_mesh.rings = 1
	var trunk_tf: Array = []
	for t in trunks:
		trunk_tf.append([t, Color("6b5240")])
	_multimesh("Troncs", trunk_mesh, trunk_tf, _mat(Color("6b5240")))
	var leaf := _shader_mat("res://shaders/feuillage.gdshader")
	leaf.set_shader_parameter("shift", 0.06)
	leaf.set_shader_parameter("sway", 0.03)
	_multimesh("Feuillages", crown_mesh, crowns, leaf)
	var needles := _shader_mat("res://shaders/feuillage.gdshader")
	needles.set_shader_parameter("sway", 0.03)
	needles.set_shader_parameter("backlight", 0.1)
	_multimesh("Coniferes", cone_mesh, cones, needles)


func _multimesh(name_: String, mesh: Mesh, items: Array, mat: Material) -> MultiMeshInstance3D:
	var mm := MultiMesh.new()
	mm.transform_format = MultiMesh.TRANSFORM_3D
	mm.use_colors = true
	mm.mesh = mesh
	mm.instance_count = items.size()
	for i in items.size():
		mm.set_instance_transform(i, items[i][0])
		mm.set_instance_color(i, items[i][1])
	var mmi := MultiMeshInstance3D.new()
	mmi.name = name_
	mmi.multimesh = mm
	mmi.material_override = mat
	add_child(mmi)
	return mmi


## Touffes d'herbe et fleurs, par carrés de 20 m qui ne s'affichent que de près.
func _build_grass() -> void:
	var tuft := _tuft_mesh()
	var mat := _shader_mat("res://shaders/feuillage.gdshader")
	mat.set_shader_parameter("sway", 0.35)
	mat.set_shader_parameter("backlight", 0.4)
	var greens := [Color("6f8f43"), Color("5f8139"), Color("7f9c4c"), Color("8aa257")]
	var flowers := [Color("f2efe4"), Color("e9cf4a"), Color("b58ad1"), Color("e77b6a")]
	var chunk := 20.0
	var cx := -140.0
	while cx < 140.0:
		var cz := -114.0
		while cz < 114.0:
			var items: Array = []
			for i in 260:
				var x := cx + rng.randf() * chunk
				var z := cz + rng.randf() * chunk
				if not nav.is_free(x, z) or nav.is_road(x, z):
					continue
				var y := WorldData.height_world(x, z)
				if y > 12.0:
					continue
				var s := 0.7 + rng.randf() * 0.7
				var b := Basis(Vector3.UP, rng.randf() * TAU).scaled(Vector3(s, s, s))
				var col: Color = greens[rng.randi_range(0, 3)]
				if rng.randf() < 0.05:
					col = flowers[rng.randi_range(0, 3)]
				items.append([Transform3D(b, Vector3(x, y, z)), col])
			if not items.is_empty():
				var mmi := _multimesh("Herbe", tuft, items, mat)
				mmi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
				mmi.visibility_range_end = 110.0
				mmi.visibility_range_end_margin = 15.0
				mmi.visibility_range_fade_mode = GeometryInstance3D.VISIBILITY_RANGE_FADE_SELF
			cz += chunk
		cx += chunk


func _tuft_mesh() -> ArrayMesh:
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	for k in 5:
		var a := k * TAU / 5.0 + 0.3
		var d := Vector3(cos(a), 0, sin(a))
		var side := Vector3(-d.z, 0, d.x) * 0.05
		var lean := d * 0.12
		var h := 0.42 + 0.1 * (k % 2)
		var n := (d + Vector3.UP * 0.6).normalized()
		st.set_normal(n)
		st.add_vertex(-side + d * 0.03)
		st.set_normal(n)
		st.add_vertex(side + d * 0.03)
		st.set_normal(n)
		st.add_vertex(lean + Vector3(0, h, 0))
	return st.commit()


# ---------------------------------------------------------------- grille de marche

func _build_nav() -> void:
	var k := WorldData.S
	for s in river_samples:
		var p: Vector2 = s["p"]
		nav.block_disc(p.x * k, p.y * k, WorldData.RIVER_WIDTH * 0.5)
	for rp in road_paths:
		for s in rp["samples"]:
			var p: Vector2 = s["p"]
			nav.mark_road(p.x * k, p.y * k, rp["w"] * 0.5)
	for b in _bridges:
		nav.mark_deck(b[0], Vector2(5.5, 2.3), b[1])
	for f in _footprints:
		nav.block_rect(f[0], f[1])
	for t in _tree_spots:
		nav.block_disc(t.x, t.y, t.z)
	for l in _lamp_spots:
		nav.block_disc(l.x, l.y, 0.12)
