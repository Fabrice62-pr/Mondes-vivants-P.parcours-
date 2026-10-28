class_name NavGrid
extends RefCounted
## Grille de marche des habitants (cases de 1 m) : bâtiments, arbres, lampadaires et rivière
## bloquent ; les routes coûtent moins cher que l'herbe, si bien que les habitants les empruntent
## dès que le détour reste raisonnable. Chemins calculés par A*, puis tendus et arrondis.

const CELL := 1.0
const X0 := -140.0
const Z0 := -114.0
const W := 280
const H := 228
const GRASS_COST := 1.6   # une case d'herbe « coûte » 1,6 case de route
const MARGIN := 0.35      # demi-largeur d'épaules : on ne frôle pas les murs
const CORNER := 1.2       # rayon (m) d'arrondi des virages

var astar := AStarGrid2D.new()
var _road := PackedByteArray()      # 1 = route ou pont
var _deck := PackedFloat32Array()   # hauteur du tablier des ponts (NAN ailleurs)


func _init() -> void:
	astar.region = Rect2i(0, 0, W, H)
	astar.cell_size = Vector2(CELL, CELL)
	astar.diagonal_mode = AStarGrid2D.DIAGONAL_MODE_ONLY_IF_NO_OBSTACLES
	astar.default_compute_heuristic = AStarGrid2D.HEURISTIC_OCTILE
	astar.default_estimate_heuristic = AStarGrid2D.HEURISTIC_OCTILE
	astar.update()
	astar.fill_weight_scale_region(astar.region, GRASS_COST)
	_road.resize(W * H)
	_deck.resize(W * H)
	_deck.fill(NAN)


# ---------------------------------------------------------------- construction

func cell(x: float, z: float) -> Vector2i:
	return Vector2i(clampi(floori((x - X0) / CELL), 0, W - 1), clampi(floori((z - Z0) / CELL), 0, H - 1))


func center(c: Vector2i) -> Vector2:
	return Vector2(X0 + (c.x + 0.5) * CELL, Z0 + (c.y + 0.5) * CELL)


func block_disc(x: float, z: float, r: float) -> void:
	_each_in_disc(x, z, r + MARGIN, func(c: Vector2i): astar.set_point_solid(c, true))


## Rectangle orienté : xf place le centre du rectangle (rotation et échelle comprises), half en mètres locaux.
func block_rect(xf: Transform3D, half: Vector2) -> void:
	_each_in_rect(xf, half, MARGIN, func(c: Vector2i): astar.set_point_solid(c, true))


func mark_road(x: float, z: float, r: float) -> void:
	_each_in_disc(x, z, r, func(c: Vector2i):
		astar.set_point_solid(c, false)
		astar.set_point_weight_scale(c, 1.0)
		_road[c.y * W + c.x] = 1)


## Tablier d'un pont : praticable, à la hauteur y. Les cases voisines restent bloquées par la rivière.
func mark_deck(xf: Transform3D, half: Vector2, y: float) -> void:
	_each_in_rect(xf, half, -MARGIN, func(c: Vector2i):
		astar.set_point_solid(c, false)
		astar.set_point_weight_scale(c, 1.0)
		_road[c.y * W + c.x] = 1
		_deck[c.y * W + c.x] = y)


func _each_in_disc(x: float, z: float, r: float, f: Callable) -> void:
	var a := cell(x - r, z - r)
	var b := cell(x + r, z + r)
	for j in range(a.y, b.y + 1):
		for i in range(a.x, b.x + 1):
			var c := Vector2i(i, j)
			if center(c).distance_to(Vector2(x, z)) <= r:
				f.call(c)


func _each_in_rect(xf: Transform3D, half: Vector2, margin: float, f: Callable) -> void:
	var inv := xf.affine_inverse()
	var s := xf.basis.get_scale().x
	var hx := half.x + margin / s
	var hz := half.y + margin / s
	var lo := Vector2(INF, INF)
	var hi := Vector2(-INF, -INF)
	for k in [Vector3(-hx, 0, -hz), Vector3(hx, 0, -hz), Vector3(-hx, 0, hz), Vector3(hx, 0, hz)]:
		var w: Vector3 = xf * k
		lo = Vector2(minf(lo.x, w.x), minf(lo.y, w.z))
		hi = Vector2(maxf(hi.x, w.x), maxf(hi.y, w.z))
	var a := cell(lo.x, lo.y)
	var b := cell(hi.x, hi.y)
	for j in range(a.y, b.y + 1):
		for i in range(a.x, b.x + 1):
			var c := Vector2i(i, j)
			var p := center(c)
			var l := inv * Vector3(p.x, xf.origin.y, p.y)
			if absf(l.x) <= hx and absf(l.z) <= hz:
				f.call(c)


# ---------------------------------------------------------------- requêtes

func is_free(x: float, z: float) -> bool:
	return not astar.is_point_solid(cell(x, z))


func is_road(x: float, z: float) -> bool:
	var c := cell(x, z)
	return _road[c.y * W + c.x] == 1


## Hauteur du sol sous un piéton : le terrain, ou le tablier d'un pont.
func ground_y(x: float, z: float) -> float:
	var c := cell(x, z)
	var d := _deck[c.y * W + c.x]
	return WorldData.height_world(x, z) if is_nan(d) else d


## Point praticable le plus proche de p (p lui-même s'il est libre), cherché jusqu'à 10 m.
func nearest_free(p: Vector3) -> Vector3:
	var c0 := cell(p.x, p.z)
	if not astar.is_point_solid(c0):
		return p
	var c := _nearest_free_cell(c0)
	var q := center(c)
	return Vector3(q.x, ground_y(q.x, q.y), q.y)


func _nearest_free_cell(c0: Vector2i) -> Vector2i:
	for r in range(1, 11):
		var best := Vector2i(-1, -1)
		var best_d := INF
		for j in range(-r, r + 1):
			for i in range(-r, r + 1):
				if maxi(absi(i), absi(j)) != r:
					continue
				var c := c0 + Vector2i(i, j)
				if astar.is_in_boundsv(c) and not astar.is_point_solid(c):
					var d := Vector2(i, j).length()
					if d < best_d:
						best_d = d
						best = c
		if best_d < INF:
			return best
	return c0


## Chemin de from à to (positions monde) : liste de points (y = 0) qui commence en from et finit en to.
func find_path(from: Vector3, to: Vector3) -> PackedVector3Array:
	var a := cell(from.x, from.z)
	var b := cell(to.x, to.z)
	if astar.is_point_solid(a):
		a = _nearest_free_cell(a)
	if astar.is_point_solid(b):
		b = _nearest_free_cell(b)
	var out := PackedVector3Array([Vector3(from.x, 0, from.z)])
	# Trajet court et dégagé : tout droit, même à travers l'herbe.
	if a == b or (Vector2(a).distance_to(Vector2(b)) < 25.0 and _clear(a, b)):
		out.append(Vector3(to.x, 0, to.z))
		return out
	var ids := astar.get_id_path(a, b, true)
	if ids.size() < 2:
		out.append(Vector3(to.x, 0, to.z))
		return out
	# Chemin tendu : depuis chaque point d'ancrage, on va le plus loin possible en ligne droite.
	var corners: Array[Vector2] = []
	var anchor := 0
	var k := 1
	while k < ids.size():
		if not _clear(ids[anchor], ids[k]):
			anchor = k - 1
			corners.append(center(ids[anchor]))
		k += 1
	# Virages arrondis (petite courbe de Bézier à chaque coin).
	var pts: Array[Vector2] = [Vector2(from.x, from.z)]
	pts.append_array(corners)
	pts.append(Vector2(to.x, to.z))
	for i in range(1, pts.size() - 1):
		var p: Vector2 = pts[i]
		var cin := minf(CORNER, p.distance_to(pts[i - 1]) * 0.45)
		var cout := minf(CORNER, p.distance_to(pts[i + 1]) * 0.45)
		var p0: Vector2 = p + (pts[i - 1] - p).normalized() * cin
		var p2: Vector2 = p + (pts[i + 1] - p).normalized() * cout
		for t in [0.0, 0.33, 0.67, 1.0]:
			var q: Vector2 = p0.lerp(p, t).lerp(p.lerp(p2, t), t)
			out.append(Vector3(q.x, 0, q.y))
	out.append(Vector3(to.x, 0, to.z))
	return out


## Ligne droite praticable entre deux cases ? Un piéton déjà sur la route ne la quitte pas pour couper.
func _clear(a: Vector2i, b: Vector2i) -> bool:
	var on_road := _road[a.y * W + a.x] == 1 and _road[b.y * W + b.x] == 1
	var n := maxi(absi(b.x - a.x), absi(b.y - a.y)) * 2
	for s in range(1, n):
		var f := float(s) / n
		var c := Vector2i(roundi(lerpf(a.x, b.x, f)), roundi(lerpf(a.y, b.y, f)))
		if astar.is_point_solid(c):
			return false
		if on_road and _road[c.y * W + c.x] == 0:
			return false
	return true
