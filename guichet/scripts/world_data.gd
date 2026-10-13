class_name WorldData
extends RefCounted
## Données de la vallée de Brévanne.
## Les coordonnées ci-dessous sont celles du "plan" ; dans le monde 3D,
## position = plan × S (1 unité Godot = 1 mètre, Y vers le haut, nord = -Z).

const S := 1.75
const TW := 160.0   # largeur du plan (x)
const TD := 130.0   # profondeur du plan (z)

const RIVER := [
	Vector2(-60, -66), Vector2(-58, -40), Vector2(-54, -14), Vector2(-44, 8),
	Vector2(-22, 17), Vector2(4, 20), Vector2(24, 28), Vector2(38, 42), Vector2(48, 66),
]
const RIVER_WIDTH := 6.5

const ROADS := [
	{"pts": [Vector2(-66, -8), Vector2(-45, -8.5), Vector2(-24, -7), Vector2(-6, -1), Vector2(8, 10),
		Vector2(26, 12.5), Vector2(44, 17), Vector2(58, 27), Vector2(70, 30)], "w": 3.6, "bridge": true},
	{"pts": [Vector2(8, 10), Vector2(2, 16), Vector2(-3, 24), Vector2(-8, 30)], "w": 3.0, "bridge": true},
	{"pts": [Vector2(-22, -7), Vector2(-21, -14), Vector2(-20, -21)], "w": 2.6, "bridge": false},
	{"pts": [Vector2(22, 11.5), Vector2(25, 2), Vector2(27, -6)], "w": 2.6, "bridge": false},
]

## kind : "agency" (agence de la Caisse) ou "place" (lieu de rencontre)
const SITES := [
	{"id": "bourg", "name": "Agence du Bourg", "x": -45.0, "z": -16.0, "kind": "agency"},
	{"id": "ag_marche", "name": "Agence du Marché", "x": 8.0, "z": 2.0, "kind": "agency"},
	{"id": "zone", "name": "Agence Zone artisanale", "x": 58.0, "z": 20.0, "kind": "agency"},
	{"id": "cafe", "name": "Café", "x": -20.0, "z": -27.0, "kind": "place"},
	{"id": "marche", "name": "Place du marché", "x": 28.0, "z": -14.0, "kind": "place"},
	{"id": "ecole", "name": "École", "x": -10.0, "z": 38.0, "kind": "place"},
	{"id": "boulangerie", "name": "Boulangerie", "x": -32.0, "z": -18.0, "kind": "place"},
	{"id": "presse", "name": "Maison de la presse", "x": -5.0, "z": -14.0, "kind": "place"},
]

## x, z, rotation (radians)
const WORKSHOPS := [Vector3(44, 30, 0.05), Vector3(70, 8, -0.3), Vector3(66, 36, 0.2), Vector3(48, 4, 0.1)]

## centre x, centre z, nombre, rayon min, rayon max
const HOUSE_CLUSTERS := [
	[-45.0, -16.0, 20, 9.0, 22.0], [-20.0, -24.0, 7, 8.0, 15.0], [12.0, -4.0, 10, 9.0, 19.0],
	[-10.0, 38.0, 7, 12.0, 19.0], [40.0, -2.0, 6, 8.0, 17.0],
]


static func ss(e0: float, e1: float, x: float) -> float:
	var t := clampf((x - e0) / (e1 - e0), 0.0, 1.0)
	return t * t * (3.0 - 2.0 * t)


## Hauteur du terrain en coordonnées plan.
static func height(x: float, z: float) -> float:
	var north := ss(-34.0, -62.0, z) * (13.0 + 5.0 * sin(x * 0.07) + 3.0 * sin(x * 0.19 + 1.3))
	var sides := ss(60.0, 78.0, absf(x)) * (8.0 + 3.0 * sin(z * 0.1 + x * 0.02))
	var south := ss(50.0, 66.0, z) * (3.0 + 1.5 * sin(x * 0.12))
	var bumps := 0.3 * sin(x * 0.31) * sin(z * 0.27)
	return north + sides + south + bumps


## Hauteur du terrain en coordonnées monde (mètres).
static func height_world(wx: float, wz: float) -> float:
	return height(wx / S, wz / S)


static func to_world(x: float, z: float) -> Vector3:
	return Vector3(x * S, height(x, z), z * S)


static func site(id: String) -> Dictionary:
	for s in SITES:
		if s["id"] == id:
			return s
	return {}


## Échantillonne une courbe Catmull-Rom passant par pts (Vector2).
## Renvoie un tableau de { "p": Vector2, "t": Vector2 (tangente normalisée) }.
static func sample_path(pts: Array, n: int) -> Array:
	var out: Array = []
	var segs := pts.size() - 1
	for i in n + 1:
		var u := float(i) / float(n) * segs
		var s := mini(int(u), segs - 1)
		var t := u - s
		var p0: Vector2 = pts[maxi(s - 1, 0)]
		var p1: Vector2 = pts[s]
		var p2: Vector2 = pts[s + 1]
		var p3: Vector2 = pts[mini(s + 2, segs)]
		var t2 := t * t
		var t3 := t2 * t
		var p := 0.5 * ((2.0 * p1) + (-p0 + p2) * t + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * t2 \
			+ (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * t3)
		var d := 0.5 * ((-p0 + p2) + 2.0 * (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * t \
			+ 3.0 * (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * t2)
		out.append({"p": p, "t": d.normalized()})
	return out
