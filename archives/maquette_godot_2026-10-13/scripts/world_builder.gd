class_name WorldBuilder
extends Node3D
## Construit toute la vallée par script : terrain, rivière, routes, ponts,
## agences, lieux de rencontre, maisons, ateliers et arbres.

var rng := RandomNumberGenerator.new()
var obstacles: Array = []      # Vector3(x_plan, z_plan, rayon)
var path_samples: Array = []   # Vector3(x_plan, z_plan, demi-largeur)
var river_samples: Array = []
var agencies := {}             # id -> Agency
var places := {}               # id -> Vector3 (monde)
var homes: Array = []          # Vector3 (monde) : portes des maisons
var labels: Array = []         # Label3D des lieux
var _mats := {}


func build() -> void:
	rng.seed = 7
	_build_terrain()
	river_samples = _ribbon(WorldData.RIVER, WorldData.RIVER_WIDTH, Color("6e9db2"), 0.12, true)
	for r in WorldData.ROADS:
		var samples := _ribbon(r["pts"], r["w"], Color("cfc4ac"), 0.16, true)
		if r["bridge"]:
			_bridge_for(samples)
	_build_sites()
	for w in WorldData.WORKSHOPS:
		if _is_free(w.x, w.y, 7.0):
			_workshop(w.x, w.y, w.z)
			obstacles.append(Vector3(w.x, w.y, 8.0))
	for c in WorldData.HOUSE_CLUSTERS:
		_scatter_houses(c[0], c[1], c[2], c[3], c[4])
	_build_trees()


# ---------------------------------------------------------------- utilitaires

func _mat(c: Color, cull_off := false) -> StandardMaterial3D:
	var key := c.to_html() + str(cull_off)
	if not _mats.has(key):
		var m := StandardMaterial3D.new()
		m.albedo_color = c
		m.roughness = 0.95
		if cull_off:
			m.cull_mode = BaseMaterial3D.CULL_DISABLED
		_mats[key] = m
	return _mats[key]


func _box(parent: Node3D, size: Vector3, c: String, pos: Vector3) -> MeshInstance3D:
	var mesh := BoxMesh.new()
	mesh.size = size
	var mi := MeshInstance3D.new()
	mi.mesh = mesh
	mi.material_override = _mat(Color(c))
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


func _is_free(x: float, z: float, r: float) -> bool:
	for o in obstacles:
		if Vector2(x - o.x, z - o.y).length() < o.z + r:
			return false
	for p in path_samples:
		if Vector2(x - p.x, z - p.y).length() < p.z + r:
			return false
	return true


func _tri(st: SurfaceTool, a: Vector3, b: Vector3, c: Vector3, col: Color) -> void:
	var n := (b - a).cross(c - a).normalized()
	if n.y < 0.0:
		n = -n
	st.set_color(col)
	st.set_normal(n)
	st.add_vertex(a)
	st.add_vertex(b)
	st.add_vertex(c)


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

func _build_terrain() -> void:
	var k := WorldData.S
	var w := WorldData.TW * k
	var d := WorldData.TD * k
	var nx := int(round(96 * k))
	var nz := int(round(78 * k))
	var grass := [Color("93ae6e"), Color("88a566"), Color("9bb474")]
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	for iz in nz:
		for ix in nx:
			var x0 := -w * 0.5 + ix * w / nx
			var x1 := -w * 0.5 + (ix + 1) * w / nx
			var z0 := -d * 0.5 + iz * d / nz
			var z1 := -d * 0.5 + (iz + 1) * d / nz
			var a := Vector3(x0, WorldData.height_world(x0, z0), z0)
			var b := Vector3(x1, WorldData.height_world(x1, z0), z0)
			var c := Vector3(x0, WorldData.height_world(x0, z1), z1)
			var e := Vector3(x1, WorldData.height_world(x1, z1), z1)
			_tri(st, a, b, c, _terrain_color((a.y + b.y + c.y) / 3.0, grass))
			_tri(st, b, e, c, _terrain_color((b.y + e.y + c.y) / 3.0, grass))
	var mat := StandardMaterial3D.new()
	mat.vertex_color_use_as_albedo = true
	mat.roughness = 1.0
	mat.cull_mode = BaseMaterial3D.CULL_DISABLED
	var mi := MeshInstance3D.new()
	mi.name = "Terrain"
	mi.mesh = st.commit()
	mi.material_override = mat
	add_child(mi)


func _terrain_color(y: float, grass: Array) -> Color:
	if y < 1.2:
		return grass[rng.randi_range(0, 2)]
	if y < 8.0:
		return Color("76925a").lerp(grass[0], rng.randf() * 0.3)
	if y < 14.0:
		return Color("9a988b").lerp(Color("76925a"), rng.randf() * 0.35)
	return Color("bebbae").lerp(Color("9a988b"), rng.randf() * 0.4)


# ---------------------------------------------------------------- rivière, routes, ponts

func _ribbon(pts: Array, width: float, col: Color, y_off: float, register: bool) -> Array:
	var k := WorldData.S
	var samples := WorldData.sample_path(pts, 240)
	var left: Array = []
	var right: Array = []
	for i in samples.size():
		var p: Vector2 = samples[i]["p"]
		var t: Vector2 = samples[i]["t"]
		var n := Vector2(-t.y, t.x) * (width * 0.5 / k)
		var a := p + n
		var b := p - n
		left.append(Vector3(a.x * k, WorldData.height(a.x, a.y) + y_off, a.y * k))
		right.append(Vector3(b.x * k, WorldData.height(b.x, b.y) + y_off, b.y * k))
		if register and i % 3 == 0:
			path_samples.append(Vector3(p.x, p.y, width * 0.5 / k + 1.2))
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	for i in samples.size() - 1:
		_tri(st, left[i], left[i + 1], right[i], col)
		_tri(st, right[i], left[i + 1], right[i + 1], col)
	var mi := MeshInstance3D.new()
	mi.mesh = st.commit()
	mi.material_override = _mat(Color.WHITE, true).duplicate()
	(mi.material_override as StandardMaterial3D).vertex_color_use_as_albedo = true
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
	_roof(ag, 11.8, 8.6, 3, "2c4a3f", Vector3(0, 5.5, 0))
	_box(ag, Vector3(3, 3.2, 0.3), "14251f", Vector3(0, 2.1, 3.85))
	_box(ag, Vector3(7, 1.1, 0.3), "b03c1c", Vector3(0, 4.6, 3.9))
	for px in [-3.8, 3.8]:
		_box(ag, Vector3(2, 1.8, 0.25), "7d98a6", Vector3(px, 2.8, 3.8))
	for px in [-1.9, 1.9]:
		_box(ag, Vector3(0.5, 3.6, 0.5), "e8e0cf", Vector3(px, 2.3, 4.4))
	obstacles.append(Vector3(x, z, 8.0))
	return ag


func _cafe(x: float, z: float) -> void:
	var g := _group(x, z)
	_box(g, Vector3(9, 4, 6.5), "f2e6cf", Vector3(0, 2, 0))
	_roof(g, 9.6, 7.4, 2.2, "7a3b2e", Vector3(0, 4, 0))
	_box(g, Vector3(9.4, 0.25, 2.2), "b03c1c", Vector3(0, 3.2, 4.2))
	_box(g, Vector3(2, 2.6, 0.2), "3d2a20", Vector3(0, 1.3, 3.3))
	var ums := ["b03c1c", "2f6f5e", "c59b3a", "b03c1c"]
	var spots := [Vector2(-3, 7.5), Vector2(0, 8.2), Vector2(3, 7.5), Vector2(-1.5, 10.5)]
	for i in spots.size():
		var p: Vector2 = spots[i]
		_box(g, Vector3(1.4, 0.1, 1.4), "ede8dd", Vector3(p.x, 0.95, p.y))
		_box(g, Vector3(0.15, 0.9, 0.15), "6d6a62", Vector3(p.x, 0.45, p.y))
		_cyl(g, 0.0, 1.6, 0.8, 8, ums[i], Vector3(p.x, 2.6, p.y))
		_box(g, Vector3(0.08, 2.2, 0.08), "6d6a62", Vector3(p.x, 1.6, p.y))
	obstacles.append(Vector3(x, z, 9.0))


func _market(x: float, z: float) -> void:
	var g := _group(x, z)
	_cyl(g, 12, 12, 0.25, 32, "d9cfb9", Vector3(0, 0.12, 0))
	var tops := ["d9822b", "2f6f5e", "e3c14b", "b03c1c", "3b5b8c", "d9822b"]
	for i in 6:
		var a := i / 6.0 * TAU
		var st := Node3D.new()
		st.position = Vector3(cos(a) * 7.5, 0, sin(a) * 7.5)
		st.rotation.y = -a + PI * 0.5
		g.add_child(st)
		_box(st, Vector3(3.6, 1, 1.8), "a4865d", Vector3(0, 0.5, 0))
		for c in [Vector2(-1.7, -0.8), Vector2(1.7, -0.8), Vector2(-1.7, 0.8), Vector2(1.7, 0.8)]:
			_box(st, Vector3(0.12, 2.6, 0.12), "6d6a62", Vector3(c.x, 1.3, c.y))
		_box(st, Vector3(4, 0.2, 2.4), tops[i], Vector3(0, 2.7, 0))
	_cyl(g, 1.4, 1.6, 0.9, 12, "b5ae9f", Vector3(0, 0.6, 0))
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
		_box(g, Vector3(1.4, 1.6, 0.2), "7d98a6", Vector3(-7 + i * 2.4, 2.6, 3.55))
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
	obstacles.append(Vector3(x, z, 7.0))


func _press(x: float, z: float) -> void:
	var g := _group(x, z)
	_box(g, Vector3(8, 0.4, 7), "b9b2a3", Vector3(0, 0.2, 0))
	_box(g, Vector3(7, 7, 6), "d7dee2", Vector3(0, 3.9, 0))
	_roof(g, 7.6, 6.8, 2.2, "3e4a54", Vector3(0, 7.4, 0))
	_box(g, Vector3(6.4, 1, 0.3), "3b5b8c", Vector3(0, 3.9, 3.2))
	_box(g, Vector3(3, 2.2, 0.2), "7d98a6", Vector3(-1.4, 2, 3.15))
	_box(g, Vector3(1.4, 2.6, 0.2), "2e3a42", Vector3(2.2, 1.7, 3.15))
	for px in [-2.0, 0.0, 2.0]:
		_box(g, Vector3(1.2, 1.4, 0.2), "7d98a6", Vector3(px, 5.8, 3.1))
	_cyl(g, 1.5, 1.5, 2.8, 6, "2f6f5e", Vector3(-5.5, 1.4, 6))
	_cyl(g, 0.0, 2.1, 1.2, 6, "22533f", Vector3(-5.5, 3.4, 6))
	for px in [1.4, 3.0]:
		_box(g, Vector3(1.3, 1.5, 0.4), "6d6a62", Vector3(px, 0.75, 5.2))
		for i in 3:
			var sh := _box(g, Vector3(1.1, 0.05, 0.5), "f5f2ea", Vector3(px, 0.45 + i * 0.42, 5.35))
			sh.rotation.x = -0.5
	_box(g, Vector3(1.6, 0.3, 1), "f5f2ea", Vector3(-1.5, 0.55, 5.6))
	obstacles.append(Vector3(x, z, 9.0))


# ---------------------------------------------------------------- habitat et végétation

func _house(x: float, z: float, rot: float, s: float) -> void:
	var g := _group(x, z, rot)
	g.scale = Vector3.ONE * s
	var roofs := ["a65a3e", "9c4f36", "8a5a44", "b06a48"]
	var walls := ["ede4d2", "e6dcc5", "f1ebdd", "dcd3bf"]
	_box(g, Vector3(6, 3.6, 5), walls[rng.randi_range(0, 3)], Vector3(0, 1.8, 0))
	_roof(g, 6.6, 5.8, 2.4, roofs[rng.randi_range(0, 3)], Vector3(0, 3.6, 0))
	_box(g, Vector3(1.2, 2, 0.2), "5b4636", Vector3(0, 1, 2.55))
	# La porte de la maison sert de domicile aux déposants.
	homes.append(g.transform * Vector3(0, 0, 4.0))


func _workshop(x: float, z: float, rot: float) -> void:
	var g := _group(x, z, rot)
	_box(g, Vector3(14, 4.5, 8), "b7b9b2", Vector3(0, 2.25, 0))
	for i in 3:
		_roof(g, 8.2, 4.4, 1.8, "77807c", Vector3(-4.6 + i * 4.6, 4.5, 0), false)
	_box(g, Vector3(4, 3.5, 0.25), "5d6764", Vector3(-3, 1.75, 4.05))
	_box(g, Vector3(1.6, 1.6, 1.6), "a7864f", Vector3(4, 0.8, 5.5))
	_box(g, Vector3(1.6, 1.6, 1.6), "9a7944", Vector3(5.8, 0.8, 5.2))
	homes.append(g.position + Vector3(0, 0, 7))


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


func _build_trees() -> void:
	var trunks: Array = []
	var crowns: Array = []
	var greens := [Color("5e7d44"), Color("4f6e3b"), Color("6e8c4e")]
	for i in 1150:
		var x := (rng.randf() - 0.5) * 152.0
		var z := (rng.randf() - 0.5) * 124.0
		var dense := 0.85 if WorldData.height(x, z) > 1.5 else 0.32
		if rng.randf() > dense:
			continue
		if not _is_free(x, z, 1.8):
			continue
		var s := 0.75 + rng.randf() * 0.6
		var base := WorldData.to_world(x, z)
		var basis := Basis(Vector3.UP, rng.randf() * TAU).scaled(Vector3.ONE * s)
		trunks.append(Transform3D(basis, base + Vector3(0, 0.8 * s, 0)))
		crowns.append([Transform3D(basis, base + Vector3(0, 3.4 * s, 0)), greens[rng.randi_range(0, 2)]])
		obstacles.append(Vector3(x, z, 1.6))
	var trunk_mesh := BoxMesh.new()
	trunk_mesh.size = Vector3(0.5, 1.6, 0.5)
	var crown_mesh := CylinderMesh.new()
	crown_mesh.top_radius = 0.0
	crown_mesh.bottom_radius = 1.8
	crown_mesh.height = 4.2
	crown_mesh.radial_segments = 7
	crown_mesh.rings = 1
	var mm_t := MultiMesh.new()
	mm_t.transform_format = MultiMesh.TRANSFORM_3D
	mm_t.mesh = trunk_mesh
	mm_t.instance_count = trunks.size()
	for i in trunks.size():
		mm_t.set_instance_transform(i, trunks[i])
	var mmi_t := MultiMeshInstance3D.new()
	mmi_t.name = "Troncs"
	mmi_t.multimesh = mm_t
	mmi_t.material_override = _mat(Color("6b5240"))
	add_child(mmi_t)
	var mm_c := MultiMesh.new()
	mm_c.transform_format = MultiMesh.TRANSFORM_3D
	mm_c.use_colors = true
	mm_c.mesh = crown_mesh
	mm_c.instance_count = crowns.size()
	for i in crowns.size():
		mm_c.set_instance_transform(i, crowns[i][0])
		mm_c.set_instance_color(i, crowns[i][1])
	var crown_mat := StandardMaterial3D.new()
	crown_mat.vertex_color_use_as_albedo = true
	crown_mat.roughness = 0.95
	var mmi_c := MultiMeshInstance3D.new()
	mmi_c.name = "Feuillages"
	mmi_c.multimesh = mm_c
	mmi_c.material_override = crown_mat
	add_child(mmi_c)
