class_name BridgeClient
extends Node
## Connexion WebSocket au bridge Python (services/bridge.py), qui relaie Kafka.
## Se reconnecte automatiquement toutes les 2 secondes si le bridge n'est pas joignable.

signal message_received(msg: Dictionary)
signal connection_changed(connected: bool)

var url := "ws://127.0.0.1:8765"
var connected := false
var _ws: WebSocketPeer
var _retry := 0.0


func _ready() -> void:
	_open()


func _open() -> void:
	_ws = WebSocketPeer.new()
	_ws.inbound_buffer_size = 1 << 23
	_ws.max_queued_packets = 16384
	_ws.connect_to_url(url)


func send(d: Dictionary) -> bool:
	if not connected:
		return false
	_ws.send_text(JSON.stringify(d))
	return true


func _process(delta: float) -> void:
	_ws.poll()
	var st := _ws.get_ready_state()
	if st == WebSocketPeer.STATE_OPEN:
		if not connected:
			connected = true
			connection_changed.emit(true)
		while _ws.get_available_packet_count() > 0:
			var d = JSON.parse_string(_ws.get_packet().get_string_from_utf8())
			if d is Dictionary:
				message_received.emit(d)
	elif st == WebSocketPeer.STATE_CLOSED:
		if connected:
			connected = false
			connection_changed.emit(false)
		_retry -= delta
		if _retry <= 0.0:
			_retry = 2.0
			_open()
