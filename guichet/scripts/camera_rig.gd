class_name CameraRig
extends Node3D
## Caméra orbitale.
## Clic gauche / droit + glisser : tourner. Molette : zoom.
## Clic molette ou Maj + clic gauche : déplacer. ZQSD / WASD / flèches : déplacer.
## R : vue d'ensemble. T : vue de dessus.

const DEFAULT_TARGET := Vector3(7, 0, 12)
const DEFAULT_DIST := 265.0
const DEFAULT_YAW := -0.45
const DEFAULT_PITCH := 0.92

var cam: Camera3D
var target := DEFAULT_TARGET
var dist := DEFAULT_DIST
var yaw := DEFAULT_YAW
var pitch := DEFAULT_PITCH
var _rotating := false
var _panning := false


func _ready() -> void:
	cam = Camera3D.new()
	cam.fov = 42.0
	cam.far = 1500.0
	add_child(cam)
	cam.current = true
	_apply()


func reset_view() -> void:
	target = DEFAULT_TARGET
	dist = DEFAULT_DIST
	yaw = DEFAULT_YAW
	pitch = DEFAULT_PITCH
	_apply()


func top_view() -> void:
	target = Vector3.ZERO
	dist = 305.0
	pitch = 0.08
	_apply()


func _apply() -> void:
	pitch = clampf(pitch, 0.08, 1.42)
	dist = clampf(dist, 20.0, 460.0)
	target.x = clampf(target.x, -140.0, 140.0)
	target.z = clampf(target.z, -114.0, 114.0)
	var off := Vector3(dist * sin(pitch) * sin(yaw), dist * cos(pitch), dist * sin(pitch) * cos(yaw))
	cam.look_at_from_position(target + off, target, Vector3.UP)


func _pan(dx: float, dy: float) -> void:
	var k := dist * 0.0016
	var fx := sin(yaw)
	var fz := cos(yaw)
	target.x += (-dx * fz - dy * fx) * k
	target.z += (dx * fx - dy * fz) * k


func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventMouseButton:
		var mb := event as InputEventMouseButton
		match mb.button_index:
			MOUSE_BUTTON_WHEEL_UP:
				dist *= 0.9
				_apply()
			MOUSE_BUTTON_WHEEL_DOWN:
				dist *= 1.1
				_apply()
			MOUSE_BUTTON_LEFT:
				_panning = mb.pressed and mb.shift_pressed
				_rotating = mb.pressed and not mb.shift_pressed
			MOUSE_BUTTON_RIGHT:
				_rotating = mb.pressed
			MOUSE_BUTTON_MIDDLE:
				_panning = mb.pressed
	elif event is InputEventMouseMotion:
		var mm := event as InputEventMouseMotion
		if _panning:
			_pan(mm.relative.x, mm.relative.y)
			_apply()
		elif _rotating:
			yaw -= mm.relative.x * 0.006
			pitch -= mm.relative.y * 0.005
			_apply()
	elif event is InputEventKey and event.pressed and not event.echo:
		match (event as InputEventKey).physical_keycode:
			KEY_R:
				reset_view()
			KEY_T:
				top_view()


func _process(delta: float) -> void:
	var mv := Vector2.ZERO
	if Input.is_physical_key_pressed(KEY_W) or Input.is_physical_key_pressed(KEY_UP):
		mv.y -= 1.0
	if Input.is_physical_key_pressed(KEY_S) or Input.is_physical_key_pressed(KEY_DOWN):
		mv.y += 1.0
	if Input.is_physical_key_pressed(KEY_A) or Input.is_physical_key_pressed(KEY_LEFT):
		mv.x -= 1.0
	if Input.is_physical_key_pressed(KEY_D) or Input.is_physical_key_pressed(KEY_RIGHT):
		mv.x += 1.0
	if mv != Vector2.ZERO:
		# Vitesse indépendante de Engine.time_scale.
		var real_dt := delta / maxf(Engine.time_scale, 0.01)
		mv = mv.normalized() * dist * 0.6 * real_dt
		var forward := Vector2(-sin(yaw), -cos(yaw))
		var right := Vector2(cos(yaw), -sin(yaw))
		var m := right * mv.x - forward * mv.y
		target.x += m.x
		target.z += m.y
		_apply()
