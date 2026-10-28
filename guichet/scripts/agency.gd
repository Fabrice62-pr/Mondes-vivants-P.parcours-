class_name Agency
extends Node3D
## Une agence de la Caisse, vue dans Godot : emplacement de la file d'attente et panneau « FERMÉE ».
## La file elle-même est gérée par le moteur Python ; Godot ne fait que l'afficher.

var agency_id := ""
var display_name := ""
var is_open := true
var _closed_label: Label3D


func queue_slot(i: int) -> Vector3:
	var row := floori(i / 12.0)
	var col := i % 12
	var p := global_position + Vector3(2.5 + col * 1.3, 0.0, 5.8 + row * 1.4)
	p.y = WorldData.height_world(p.x, p.z)
	return p


## Ce que regarde la personne à la place i : la porte pour la première, sinon la personne devant elle.
func queue_look(i: int) -> Vector3:
	if i <= 0:
		return global_position + Vector3(0.0, 0.0, 3.9)
	return queue_slot(i - 1)


func set_open(v: bool) -> void:
	is_open = v
	if _closed_label == null:
		_closed_label = Label3D.new()
		_closed_label.text = "FERMÉE"
		_closed_label.billboard = BaseMaterial3D.BILLBOARD_ENABLED
		_closed_label.no_depth_test = true
		_closed_label.font_size = 44
		_closed_label.outline_size = 12
		_closed_label.pixel_size = 0.03
		_closed_label.modulate = Color("ffd2c4")
		_closed_label.outline_modulate = Color("5a1a0c")
		_closed_label.position = Vector3(0, 8.5, 0)
		add_child(_closed_label)
	_closed_label.visible = not v
