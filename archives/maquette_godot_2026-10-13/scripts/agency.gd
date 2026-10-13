class_name Agency
extends Node3D
## Une agence de la Caisse : un guichet, une file d'attente.

var agency_id := ""
var display_name := ""
var is_open := true
var queue: Array = []          # Depositor
var service_time := 2.2        # secondes par client
var sim: Node                  # Main (simulation)
var _timer := 0.0


func queue_slot(i: int) -> Vector3:
	var row := floori(i / 12.0)
	var col := i % 12
	var p := global_position + Vector3(2.5 + col * 1.3, 0.0, 5.8 + row * 1.4)
	p.y = WorldData.height_world(p.x, p.z)
	return p


func join(agent: Node) -> int:
	queue.append(agent)
	return queue.size() - 1


func leave(agent: Node) -> void:
	queue.erase(agent)
	_reindex()


func set_open(v: bool) -> void:
	is_open = v
	if not v:
		var q := queue.duplicate()
		queue.clear()
		for a in q:
			if is_instance_valid(a):
				a.on_agency_closed(self)


func clear_queue() -> void:
	queue.clear()
	_timer = 0.0


func _reindex() -> void:
	for i in queue.size():
		if is_instance_valid(queue[i]):
			queue[i].set_queue_index(i)


func _process(delta: float) -> void:
	if queue.is_empty() or not is_open or sim == null:
		_timer = 0.0
		return
	var front = queue[0]
	if not is_instance_valid(front):
		queue.pop_front()
		_reindex()
		return
	if not front.is_at_counter():
		return
	_timer += delta
	if _timer >= service_time:
		_timer = 0.0
		queue.pop_front()
		_reindex()
		sim.serve(front, self)
