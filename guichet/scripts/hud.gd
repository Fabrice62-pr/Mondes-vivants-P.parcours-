class_name Hud
extends CanvasLayer
## Interface : lancement d'un run, état de la Caisse, indicateurs (B2), leviers (D3),
## inspection d'un agent (D2) et journal de ce qui se dit dans la vallée.
## Les leviers n'agissent pas localement : ils envoient des commandes au moteur.

## Courbe de l'indicateur de contexte commun, synchronisée avec la scène. Un clic sur un point
## demande l'explication de la variation (B4) : chaque point garde les événements de croyance du pas.
class Courbe extends Control:
	signal point_clicked(i: int)
	var points: Array = []          # {v, t, sources, c}
	var seuil := 0.6
	var choisi := -1

	func _init() -> void:
		mouse_filter = Control.MOUSE_FILTER_STOP
		mouse_default_cursor_shape = Control.CURSOR_POINTING_HAND
		tooltip_text = "Cliquer sur la courbe : ce qui a fait varier l'ICC à ce moment"

	func ajouter(v: float, t: int, sources: Array, c: Dictionary) -> void:
		points.append({"v": v, "t": t, "sources": sources, "c": c})
		if points.size() > 800:      # 7 jours de pas de 15 min = 672 points
			points.pop_front()
			choisi -= 1
		queue_redraw()

	func vider() -> void:
		points.clear()
		choisi = -1
		queue_redraw()

	func _x(i: int) -> float:
		return size.x * i / float(maxi(points.size() - 1, 1))

	func _gui_input(event: InputEvent) -> void:
		var mb := event as InputEventMouseButton
		if mb and mb.pressed and mb.button_index == MOUSE_BUTTON_LEFT:
			if points.size() < 2:
				return
			choisi = clampi(int(round(mb.position.x / size.x * (points.size() - 1))), 0, points.size() - 1)
			queue_redraw()
			point_clicked.emit(choisi)
			accept_event()

	func _draw() -> void:
		var w := size.x
		var h := size.y
		draw_rect(Rect2(Vector2.ZERO, size), Color(0, 0, 0, 0.25))
		var ys := h * (1.0 - seuil)
		draw_dashed_line(Vector2(0, ys), Vector2(w, ys), Color("f0b46a"), 1.0, 4.0)
		if points.size() < 2:
			return
		var pts := PackedVector2Array()
		for i in points.size():
			pts.append(Vector2(_x(i), h * (1.0 - clampf(points[i]["v"], 0.0, 1.0))))
		draw_polyline(pts, Color("9fd3c0"), 2.0, true)
		if choisi >= 0 and choisi < points.size():
			draw_line(Vector2(_x(choisi), 0), Vector2(_x(choisi), h), Color(1, 1, 1, 0.55), 1.0)
			draw_circle(pts[choisi], 3.5, Color("ffe08a"))


## Courbes de l'ICC de plusieurs runs superposées, sur l'axe du temps simulé (comparaison des runs).
class CourbesRuns extends Control:
	var series: Array = []          # {pts: [[t, v], …], col: Color, visible: bool}
	var t_max := 7 * 1440
	var seuil := 0.6

	func _draw() -> void:
		var w := size.x
		var h := size.y - 16.0
		draw_rect(Rect2(Vector2.ZERO, Vector2(w, h)), Color(0, 0, 0, 0.25))
		var font := get_theme_default_font()
		var jours := int(ceil(t_max / 1440.0))
		for j in jours + 1:
			var x := w * j * 1440.0 / t_max
			draw_line(Vector2(x, 0), Vector2(x, h), Color(1, 1, 1, 0.08), 1.0)
			if j < jours:
				draw_string(font, Vector2(x + 4, h + 13), "J%d" % (j + 1), HORIZONTAL_ALIGNMENT_LEFT, -1, 12,
					Color("aebbb4"))
		var ys := h * (1.0 - seuil)
		draw_dashed_line(Vector2(0, ys), Vector2(w, ys), Color("f0b46a"), 1.0, 4.0)
		for s in series:
			if not s["visible"] or s["pts"].size() < 2:
				continue
			var pts := PackedVector2Array()
			for p in s["pts"]:
				pts.append(Vector2(w * float(p[0]) / t_max, h * (1.0 - clampf(float(p[1]), 0.0, 1.0))))
			draw_polyline(pts, s["col"], 2.0, true)


var main: Node
var clock_text := "—"
var show_all_conversations := false

var _conn_l: Label
var _run_l: Label
var _config_ob: OptionButton
var _dep_sb: SpinBox
var _seed_sb: SpinBox
var _rythme_s: HSlider
var _llm_cb: CheckBox
var _mode_ob: OptionButton
var _svc_l: Label
var _svc_retards: Array = []
var _rejeu := ""
var _llm_l: Label
var _llm := {}
var _pause_b: Button
var _paused := false
var _time_l: Label
var _state_l: Label
var _reserve_bar: ProgressBar
var _reserve_l: Label
var _tip_l: Label
var _icc_l: Label
var _courbe: Courbe
var _pop_l: Label
var _guarantee_cb: CheckBox
var _ceiling_s: HSlider
var _cap_s: HSlider
var _silent_cb: CheckBox
var _agency_checks := {}
var _log: RichTextLabel
var _inspect_panel: PanelContainer
var _inspect_title: Label
var _inspect: RichTextLabel
var _cmp_panel: PanelContainer
var _cmp_curves: CourbesRuns
var _cmp_grid: GridContainer
var _cmp_info: Label
var _last_day := 0
var _last_open = null
var _reserve_start := 0.0
var _caisse := {}


func _ready() -> void:
	var root := Control.new()
	root.set_anchors_preset(Control.PRESET_FULL_RECT)
	root.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(root)
	_build_left(root)
	_build_right(root)
	_build_comparison(root)


# ================================================================ construction

func _build_left(root: Control) -> void:
	var left := PanelContainer.new()
	left.anchor_bottom = 1.0
	left.offset_left = 12
	left.offset_top = 12
	left.offset_right = 392
	left.offset_bottom = -12
	left.add_theme_stylebox_override("panel", _panel_style())
	root.add_child(left)
	var scroll := ScrollContainer.new()
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	left.add_child(scroll)
	var v := VBoxContainer.new()
	v.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	v.add_theme_constant_override("separation", 6)
	scroll.add_child(v)

	var title := Label.new()
	title.text = "GUICHET"
	title.add_theme_font_size_override("font_size", 30)
	v.add_child(title)
	var sub := Label.new()
	sub.text = "Caisse Coopérative de Brévanne"
	sub.add_theme_color_override("font_color", Color("e9a68f"))
	v.add_child(sub)
	_conn_l = _label(v)
	_conn_l.text = "Connexion au bridge…"

	# ---------- lancement
	_section(v, "Lancer un run")
	_config_ob = OptionButton.new()
	v.add_child(_config_ob)
	_dep_sb = _spin(v, "Déposants", 10, 400, 10, 140)
	_seed_sb = _spin(v, "Graine", 1, 9999, 1, 1)
	_rythme_s = _slider(v, "Secondes réelles par pas de 15 min", 0.05, 2.0, 0.05, 0.5, "s", func(x): _set_rythme(x))
	_llm_cb = CheckBox.new()
	_llm_cb.text = "Agents LLM (consomme le quota gratuit)"
	_llm_cb.button_pressed = true
	_llm_cb.tooltip_text = "Décoché : tous les agents décident avec leurs règles, sans aucun appel aux API."
	v.add_child(_llm_cb)
	_mode_ob = OptionButton.new()
	_mode_ob.add_item("Mode synchrone : tous agissent au même pas")
	_mode_ob.add_item("Mode asynchrone : chacun à son rythme, latences réelles")
	_mode_ob.tooltip_text = "Asynchrone : un agent LLM qui réfléchit n'arrête pas la vallée ; sa décision arrive avec la latence réelle du modèle (C4)."
	v.add_child(_mode_ob)
	var row := HBoxContainer.new()
	var go := Button.new()
	go.text = "Lancer"
	go.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	go.pressed.connect(_on_launch)
	row.add_child(go)
	var stop := Button.new()
	stop.text = "Arrêter"
	stop.pressed.connect(func(): main.stop_run())
	row.add_child(stop)
	_pause_b = Button.new()
	_pause_b.text = "Pause"
	_pause_b.pressed.connect(_toggle_pause)
	row.add_child(_pause_b)
	v.add_child(row)
	_run_l = _label(v)
	_run_l.add_theme_color_override("font_color", Color("aebbb4"))
	var cmp := Button.new()
	cmp.text = "Comparer les runs passés"
	cmp.pressed.connect(func():
		_cmp_panel.visible = not _cmp_panel.visible
		if _cmp_panel.visible:
			_cmp_info.text = "Chargement…"
			main.request_runs())
	v.add_child(cmp)

	# ---------- état
	_section(v, "État de la vallée")
	_time_l = _label(v)
	_time_l.add_theme_font_size_override("font_size", 18)
	_state_l = _label(v)
	_reserve_bar = ProgressBar.new()
	_reserve_bar.max_value = 1.0
	_reserve_bar.step = 0.001
	_reserve_bar.show_percentage = false
	_reserve_bar.custom_minimum_size = Vector2(0, 18)
	v.add_child(_reserve_bar)
	_reserve_l = _label(v)
	_tip_l = _label(v)
	_tip_l.add_theme_font_size_override("font_size", 17)
	_icc_l = _label(v)
	_courbe = Courbe.new()
	_courbe.custom_minimum_size = Vector2(0, 70)
	_courbe.point_clicked.connect(show_icc_sources)
	v.add_child(_courbe)
	var hint := _label(v)
	hint.text = "Cliquer sur la courbe pour voir ce qui a fait varier l'ICC."
	hint.add_theme_color_override("font_color", Color("aebbb4"))
	hint.add_theme_font_size_override("font_size", 12)
	_pop_l = _label(v)
	_llm_l = _label(v)
	_llm_l.add_theme_color_override("font_color", Color("c7b8f0"))
	_svc_l = _label(v)
	_svc_l.add_theme_color_override("font_color", Color("aebbb4"))
	_svc_l.add_theme_font_size_override("font_size", 13)
	_svc_l.text = "Service d'indicateurs : en attente (lancé par lancer.bat)."

	# ---------- leviers
	_section(v, "Leviers (envoyés au moteur)")
	_guarantee_cb = CheckBox.new()
	_guarantee_cb.text = "Garantie des dépôts"
	_guarantee_cb.toggled.connect(func(on): main.send_command("garantie", {"active": on, "plafond": _ceiling_s.value}))
	v.add_child(_guarantee_cb)
	_ceiling_s = _slider(v, "Plafond de garantie", 0, 150000, 5000, 20000, "money", func(x):
		if _guarantee_cb.button_pressed:
			main.send_command("garantie", {"active": true, "plafond": x}))
	_cap_s = _slider(v, "Retrait max par passage", 0.1, 1.0, 0.05, 1.0, "pct", func(x):
		main.send_command("plafond_retrait", {"valeur": x}))
	var comm := HBoxContainer.new()
	var tone := OptionButton.new()
	for t in ["rassurant", "factuel", "vague"]:
		tone.add_item(t.capitalize())
	tone.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	comm.add_child(tone)
	var pub := Button.new()
	pub.text = "Publier un communiqué"
	pub.pressed.connect(func():
		main.send_command("publier_communique", {"ton": ["rassurant", "factuel", "vague"][tone.selected], "auteur": "utilisateur"}))
	comm.add_child(pub)
	v.add_child(comm)
	for id in ["bourg", "ag_marche", "zone"]:
		var cb := CheckBox.new()
		cb.text = "%s : ouverte" % WorldData.site(id).get("name", id)
		cb.button_pressed = true
		cb.toggled.connect(func(on): main.send_command("rouvrir_agence" if on else "fermer_agence", {"agence_id": id}))
		v.add_child(cb)
		_agency_checks[id] = cb
	_silent_cb = CheckBox.new()
	_silent_cb.text = "Employé d'agence tenu au silence"
	_silent_cb.button_pressed = true
	_silent_cb.toggled.connect(func(on): main.send_command("consigne_employe", {"consigne": "silence" if on else "transparence"}))
	v.add_child(_silent_cb)
	var rum := HBoxContainer.new()
	var force := SpinBox.new()
	force.min_value = 0.1
	force.max_value = 1.0
	force.step = 0.05
	force.value = 0.5
	rum.add_child(force)
	var rb := Button.new()
	rb.text = "Injecter une rumeur"
	rb.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	rb.pressed.connect(func(): main.send_command("injecter_rumeur", {"force": force.value}))
	rum.add_child(rb)
	v.add_child(rum)
	var loss := HBoxContainer.new()
	var lv := SpinBox.new()
	lv.min_value = 0.0
	lv.max_value = 0.5
	lv.step = 0.01
	lv.value = 0.15
	loss.add_child(lv)
	var lb := Button.new()
	lb.text = "Pertes réelles sur prêts"
	lb.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	lb.pressed.connect(func(): main.send_command("pertes_reelles", {"valeur": lv.value}))
	loss.add_child(lb)
	v.add_child(loss)

	# ---------- affichage
	_section(v, "Affichage")
	var labels_cb := CheckBox.new()
	labels_cb.text = "Étiquettes des lieux"
	labels_cb.button_pressed = true
	labels_cb.toggled.connect(func(on):
		for l in main.builder.labels:
			l.visible = on)
	v.add_child(labels_cb)
	var all_cb := CheckBox.new()
	all_cb.text = "Journal : toutes les conversations"
	all_cb.toggled.connect(func(on): show_all_conversations = on)
	v.add_child(all_cb)
	var help := Label.new()
	help.text = "Cliquer sur un habitant pour l'inspecter. Caméra : glisser pour tourner, molette pour zoomer, Maj + glisser ou ZQSD pour se déplacer. R : vue d'ensemble, T : vue de dessus."
	help.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	help.add_theme_color_override("font_color", Color("aebbb4"))
	help.add_theme_font_size_override("font_size", 13)
	v.add_child(help)


func _build_right(root: Control) -> void:
	var right := VBoxContainer.new()
	right.anchor_left = 1.0
	right.anchor_right = 1.0
	right.anchor_bottom = 1.0
	right.offset_left = -460
	right.offset_right = -12
	right.offset_top = 12
	right.offset_bottom = -12
	right.add_theme_constant_override("separation", 10)
	root.add_child(right)

	_inspect_panel = PanelContainer.new()
	_inspect_panel.add_theme_stylebox_override("panel", _panel_style())
	_inspect_panel.custom_minimum_size = Vector2(0, 330)
	_inspect_panel.visible = false
	right.add_child(_inspect_panel)
	var iv := VBoxContainer.new()
	_inspect_panel.add_child(iv)
	var head := HBoxContainer.new()
	_inspect_title = Label.new()
	_inspect_title.text = "Inspection d'un habitant"
	_inspect_title.add_theme_font_size_override("font_size", 18)
	_inspect_title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	head.add_child(_inspect_title)
	var close := Button.new()
	close.text = "×"
	close.pressed.connect(func():
		if main.selected:
			main.selected.set_selected(false)
		main.selected = null
		_courbe.choisi = -1
		_courbe.queue_redraw()
		hide_agent())
	head.add_child(close)
	iv.add_child(head)
	_inspect = RichTextLabel.new()
	_inspect.bbcode_enabled = true
	_inspect.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_inspect.add_theme_font_size_override("normal_font_size", 14)
	_inspect.add_theme_font_size_override("bold_font_size", 14)
	iv.add_child(_inspect)

	var jp := PanelContainer.new()
	jp.add_theme_stylebox_override("panel", _panel_style())
	jp.size_flags_vertical = Control.SIZE_EXPAND_FILL
	right.add_child(jp)
	var rv := VBoxContainer.new()
	jp.add_child(rv)
	var lt := Label.new()
	lt.text = "Ce qui se dit dans la vallée"
	lt.add_theme_font_size_override("font_size", 18)
	rv.add_child(lt)
	_log = RichTextLabel.new()
	_log.bbcode_enabled = true
	_log.scroll_following = true
	_log.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_log.add_theme_font_size_override("normal_font_size", 14)
	_log.add_theme_font_size_override("bold_font_size", 14)
	_log.add_theme_font_size_override("italics_font_size", 14)
	rv.add_child(_log)
	var legend := Label.new()
	legend.text = "Vert : calme · Orange : inquiet · Rouge : va retirer · Gris : a retiré · Noir : a perdu · Bleu : personnel"
	legend.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	legend.add_theme_font_size_override("font_size", 13)
	rv.add_child(legend)


func _build_comparison(root: Control) -> void:
	_cmp_panel = PanelContainer.new()
	_cmp_panel.set_anchors_preset(Control.PRESET_FULL_RECT)
	_cmp_panel.offset_left = 410
	_cmp_panel.offset_right = -480
	_cmp_panel.offset_top = 40
	_cmp_panel.offset_bottom = -40
	var sb := _panel_style()
	sb.bg_color = Color(0.06, 0.11, 0.095, 0.96)
	_cmp_panel.add_theme_stylebox_override("panel", sb)
	_cmp_panel.visible = false
	root.add_child(_cmp_panel)
	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", 8)
	_cmp_panel.add_child(v)
	var head := HBoxContainer.new()
	var t := Label.new()
	t.text = "Comparaison des runs"
	t.add_theme_font_size_override("font_size", 20)
	t.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	head.add_child(t)
	var refresh := Button.new()
	refresh.text = "Actualiser"
	refresh.pressed.connect(func():
		_cmp_info.text = "Chargement…"
		main.request_runs())
	head.add_child(refresh)
	var close := Button.new()
	close.text = "×"
	close.pressed.connect(func(): _cmp_panel.visible = false)
	head.add_child(close)
	v.add_child(head)
	_cmp_info = _label(v)
	_cmp_info.add_theme_color_override("font_color", Color("aebbb4"))
	_cmp_curves = CourbesRuns.new()
	_cmp_curves.custom_minimum_size = Vector2(0, 200)
	v.add_child(_cmp_curves)
	var scroll := ScrollContainer.new()
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	v.add_child(scroll)
	_cmp_grid = GridContainer.new()
	_cmp_grid.columns = 9
	_cmp_grid.add_theme_constant_override("h_separation", 14)
	_cmp_grid.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.add_child(_cmp_grid)


const RUN_COLORS := ["9fd3c0", "f0b46a", "e07a8f", "8fb4f0", "c7b8f0", "e9d36a", "6ad3e9", "f08f6a",
	"a6e07a", "d38fe0", "f0e0c0", "7a9fe0"]


func on_runs(runs: Array) -> void:
	for c in _cmp_grid.get_children():
		c.queue_free()
	_cmp_curves.series.clear()
	if runs.is_empty():
		_cmp_info.text = "Aucun run terminé dans data/runs/."
		_cmp_curves.queue_redraw()
		return
	_cmp_info.text = "%d derniers runs terminés, du plus récent au plus ancien. Cocher un run pour afficher sa courbe d'ICC ; ligne orange : seuil d'émergence 0,60." % runs.size()
	var t_max := 0
	for h in ["Run", "Config.", "Graine", "Déposants", "ICC max", "Émergence", "Défaut", "Retiré / file max", ""]:
		var l := Label.new()
		l.text = h
		l.add_theme_color_override("font_color", Color("e9a68f"))
		_cmp_grid.add_child(l)
	for i in runs.size():
		var r: Dictionary = runs[i]
		var col := Color(RUN_COLORS[i % RUN_COLORS.size()])
		var serie := {"pts": r["icc"], "col": col, "visible": i < 6}
		_cmp_curves.series.append(serie)
		t_max = maxi(t_max, int(r.get("duree_jours", 7)) * 1440)
		var rid := str(r["run_id"])
		var cb := CheckBox.new()
		cb.text = rid.get_slice("-", rid.get_slice_count("-") - 1)   # heure de lancement
		cb.tooltip_text = rid
		cb.button_pressed = serie["visible"]
		cb.add_theme_color_override("font_color", col)
		cb.add_theme_color_override("font_pressed_color", col)
		cb.disabled = r["icc"].is_empty()
		cb.toggled.connect(func(on):
			serie["visible"] = on
			_cmp_curves.queue_redraw())
		_cmp_grid.add_child(cb)
		var cells := [
			"%s%s%s" % [str(r["config"]), " · LLM" if r.get("llm", false) else "", " · async" if r.get("mode", "") == "asynchrone" else ""],
			str(r["graine"]),
			str(r["deposants"]) if r["deposants"] != null else "—",
			("%.2f" % float(r["icc_max"])) if r["icc_max"] != null else "—",
			("oui, " + _t_label(int(r["t_emergence"]))) if r["emergence"] and r["t_emergence"] != null else "non",
			(_t_label(int(r["t_defaut"])) + (" (solvable)" if r["solvable"] else " (insolvable)")) if r["defaut"] and r["t_defaut"] != null else "non",
			"%d %% / %d" % [int(round(float(r["part_retiree"]) * 100)), int(r["file_max"])],
		]
		for c in cells:
			var l := Label.new()
			l.text = c
			_cmp_grid.add_child(l)
		var rb := Button.new()
		rb.text = "Rejouer"
		rb.tooltip_text = "Rejoue ce run à l'identique, à partir de ses données enregistrées (E3), sans appel LLM."
		rb.disabled = not r.get("rejouable", false)
		if rb.disabled:
			rb.tooltip_text = "Run enregistré avant la semaine 7 : ses instantanés d'état manquent pour un rejeu fidèle."
		rb.pressed.connect(func():
			_cmp_panel.visible = false
			main.replay_run(rid, _rythme_s.value))
		_cmp_grid.add_child(rb)
	_cmp_curves.t_max = maxi(t_max, 1440)
	_cmp_curves.queue_redraw()


## t_sim (minutes depuis J1 08:00) -> « J3 10:00 ».
func _t_label(t: int) -> String:
	var m := t + 8 * 60
	return "J%d %02d:%02d" % [m / 1440 + 1, (m % 1440) / 60, m % 60]


func _panel_style() -> StyleBoxFlat:
	var sb := StyleBoxFlat.new()
	sb.bg_color = Color(0.078, 0.145, 0.122, 0.9)
	sb.set_corner_radius_all(8)
	sb.set_content_margin_all(14)
	return sb


func _label(parent: Control) -> Label:
	var l := Label.new()
	l.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	parent.add_child(l)
	return l


func _section(parent: Control, text: String) -> void:
	parent.add_child(HSeparator.new())
	var l := Label.new()
	l.text = text
	l.add_theme_font_size_override("font_size", 17)
	l.add_theme_color_override("font_color", Color("e9a68f"))
	parent.add_child(l)


func _spin(parent: Control, text: String, mn: float, mx: float, stp: float, val: float) -> SpinBox:
	var row := HBoxContainer.new()
	var l := Label.new()
	l.text = text
	l.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(l)
	var s := SpinBox.new()
	s.min_value = mn
	s.max_value = mx
	s.step = stp
	s.value = val
	row.add_child(s)
	parent.add_child(row)
	return s


## Curseur dont la valeur n'est envoyée qu'au relâchement (pas de rafale de commandes).
func _slider(parent: Control, text: String, mn: float, mx: float, stp: float, val: float, kind: String,
		on_release: Callable) -> HSlider:
	var l := Label.new()
	parent.add_child(l)
	var s := HSlider.new()
	s.min_value = mn
	s.max_value = mx
	s.step = stp
	s.value = val
	s.custom_minimum_size = Vector2(0, 24)
	parent.add_child(s)
	l.text = "%s : %s" % [text, _fmt(kind, val)]
	s.value_changed.connect(func(x): l.text = "%s : %s" % [text, _fmt(kind, x)])
	s.drag_ended.connect(func(changed):
		if changed:
			on_release.call(s.value))
	return s


func _fmt(kind: String, x: float) -> String:
	match kind:
		"money":
			return fmt_money(x)
		"pct":
			return "%d %%" % int(round(x * 100))
		"s":
			return "%.2f s" % x
	return str(x)


func fmt_money(v: float) -> String:
	var s := str(int(round(v)))
	var out := ""
	var c := 0
	for i in range(s.length() - 1, -1, -1):
		out = s[i] + out
		c += 1
		if c % 3 == 0 and i > 0 and s[i - 1] != "-":
			out = " " + out
	return out + " u"


# ================================================================ actions

func _on_launch() -> void:
	if _config_ob.item_count == 0:
		log_line("[color=#ff8a6a]Aucune configuration reçue du bridge.[/color]")
		return
	_paused = false
	_pause_b.text = "Pause"
	main.launch_run(_config_ob.get_item_text(_config_ob.selected), int(_dep_sb.value), int(_seed_sb.value),
		_rythme_s.value, _llm_cb.button_pressed, "asynchrone" if _mode_ob.selected == 1 else "synchrone")


func _set_rythme(x: float) -> void:
	if main.run_id != "":
		main.send_command("vitesse", {"rythme_s": x})


func _toggle_pause() -> void:
	_paused = not _paused
	main.send_command("pause" if _paused else "reprendre", {})
	_pause_b.text = "Reprendre" if _paused else "Pause"


# ================================================================ réception

func on_connection(ok: bool) -> void:
	_conn_l.text = "Bridge connecté" if ok else "Bridge injoignable (lancez services/bridge.py) — nouvel essai…"
	_conn_l.add_theme_color_override("font_color", Color("9fd3c0") if ok else Color("ff8a6a"))


func on_bridge(m: Dictionary) -> void:
	match m["type"]:
		"bridge.hello":
			var current := _config_ob.get_item_text(_config_ob.selected) if _config_ob.item_count > 0 else "principal.yaml"
			_config_ob.clear()
			for c in m["configs"]:
				_config_ob.add_item(c)
				if c == current:
					_config_ob.select(_config_ob.item_count - 1)
		"bridge.runs":
			on_runs(m["runs"])
		"bridge.statut":
			match m["etat"]:
				"lance":
					_rejeu = ""
					log_line("[color=#9fd3c0]Moteur lancé : %s[/color]" % m["detail"])
				"rejeu":
					_rejeu = m["detail"]
					log_line("[color=#9fd3c0]Rejeu du run %s : données enregistrées, aucun appel LLM.[/color]" % m["detail"])
				"termine":
					_run_l.text += "\nMoteur terminé (%s)." % m["detail"]
				"arrete":
					log_line("Run arrêté.")
				"erreur":
					log_line("[color=#ff8a6a]Bridge : %s[/color]" % m["detail"])


func on_run_started(m: Dictionary) -> void:
	var p: Dictionary = m["payload"]
	clear_log()
	_courbe.vider()
	hide_agent()
	_last_day = 0
	_last_open = null
	_svc_retards.clear()
	_llm = {"appels": 0, "ok": 0, "tokens": 0, "latence": 0, "incidents": 0, "par_modele": {}, "souvenirs": 0}
	var n_llm := 0
	for row in p["attribution"]:
		if row["type"] == "llm":
			n_llm += 1
	_run_l.text = "%sRun %s\nConfiguration « %s », graine %d, mode %s\n%d déposants, %d agents LLM" % [
		"REJEU — " if _rejeu == m["run_id"] else "", m["run_id"], p["config_nom"], int(p["graine"]), p["mode"],
		int(p["population"]), n_llm]
	if n_llm > 0:
		_llm_l.text = "Agents LLM : aucun appel pour l'instant. Ils réfléchissent quand ils apprennent quelque chose."
	else:
		_llm_l.text = "Aucun agent LLM : tout le monde décide avec des règles."
	var n_staff := 0
	for row in p["attribution"]:
		if row["role"] != "deposant":
			n_staff += 1
	var n_tot: int = p["attribution"].size()
	log_line("[b]Nouveau run[/b] : configuration « %s », graine %d, %d habitants (%d déposants, %d membres du personnel)." % [
		p["config_nom"], int(p["graine"]), n_tot, n_tot - n_staff, n_staff])
	log_line("[color=#aebbb4]Jusqu'à l'article du vendredi, la vallée vit sa routine : le journal reste calme.[/color]")
	for id in _agency_checks:
		_agency_checks[id].set_pressed_no_signal(true)


func on_run_ended(p: Dictionary) -> void:
	var r: Dictionary = p["resume"]
	log_line("[b]Fin du run[/b] (%s) : %s retirés, file max %d, ICC max %.2f, émergence %s." % [
		p["raison"], "%d %%" % int(float(r["part_retiree"]) * 100), int(r["file_max"]), float(r["icc_max"]),
		"oui" if r["emergence"] else "non"])


func on_clock(p: Dictionary) -> void:
	clock_text = "J%d %s %02d:%02d" % [int(p["jour"]), p["jour_semaine"], int(p["heure"]), int(p["minute"])]
	_time_l.text = "%s — agences %s" % [clock_text, "ouvertes" if p["agences_ouvertes"] else "fermées"]
	# Repères dans le journal : changement de jour, ouverture et fermeture des agences.
	if int(p["jour"]) != _last_day:
		if _last_day > 0:
			log_line("[b][color=#e9a68f]——— J%d, %s ———[/color][/b]" % [int(p["jour"]), p["jour_semaine"]])
		_last_day = int(p["jour"])
	var open := bool(p["agences_ouvertes"])
	if _last_open != null and open != _last_open:
		log_line("[color=#aebbb4]Les agences %s.[/color]" % ("ouvrent" if open else "ferment"))
	_last_open = open


func on_caisse(p: Dictionary) -> void:
	_caisse = p
	if _reserve_start <= 0.0 or float(p["reserves"]) > _reserve_start:
		_reserve_start = float(p["reserves"])
	var etat := "EN DÉFAUT" if p["defaut"] else "ouverte"
	var solv := "solvable" if float(p["fonds_propres"]) >= 0.0 else "INSOLVABLE"
	_state_l.text = "Caisse %s, %s (marge %s)" % [etat, solv, fmt_money(float(p["fonds_propres"]))]
	_reserve_bar.value = float(p["reserves"]) / maxf(_reserve_start, 1.0)
	_reserve_l.text = "Réserves : %s sur %s au départ\nDépôts restants : %s" % [
		fmt_money(float(p["reserves"])), fmt_money(_reserve_start), fmt_money(float(p["depots"]))]


func on_indicator(p: Dictionary, t: int) -> void:
	match p["nom"]:
		"icc_2h":
			# Service d'indicateurs (C7) : ICC moyenné sur 2 h et retard entre l'instantané et l'indicateur.
			_svc_retards.append(int(p["retard_ms"]))
			if _svc_retards.size() > 200:
				_svc_retards.pop_front()
			var tri := _svc_retards.duplicate()
			tri.sort()
			_svc_l.text = "Service d'indicateurs : ICC moyen sur 2 h %.2f · retard %d ms (médiane %d ms, max %d ms)" % [
				float(p["valeur"]), int(p["retard_ms"]), tri[tri.size() / 2], tri[-1]]
		"icc_flux":
			pass
		"icc":
			var c: Dictionary = p["composantes"]
			_icc_l.text = "Contexte commun (ICC) : %.2f\nattente %d %% × convergence %d %% — seuil d'émergence 0,60" % [
				float(p["valeur"]), int(float(c["attente"]) * 100), int(float(c["convergence"]) * 100)]
			_courbe.ajouter(float(p["valeur"]), t, p.get("sources", []), c)
		"basculement":
			var c: Dictionary = p["composantes"]
			var etat: String = c["etat"]
			_tip_l.text = "Basculement : %s\nengagés %s · au bord %s" % [etat, fmt_money(float(c["engages"])),
				fmt_money(float(c["au_bord"]))]
			var col := Color("9fd3c0")
			if etat == "Proche":
				col = Color("f0b46a")
			elif etat in ["Irréversible", "Défaut"]:
				col = Color("ff8a6a")
			_tip_l.add_theme_color_override("font_color", col)
			if etat != "Faible":
				log_line("[color=#%s]Basculement : %s[/color]" % [col.to_html(false), etat])


func on_snapshot(p: Dictionary) -> void:
	var counts := {"calme": 0, "inquiet": 0, "va retirer": 0, "a retiré": 0, "a perdu de l'argent": 0}
	for a in main.agents.values():
		var k: String = a.color_name()
		if counts.has(k):
			counts[k] += 1
	_pop_l.text = "Calmes %d · Inquiets %d · Vont retirer %d · Ont retiré %d · Ont perdu %d" % [
		counts["calme"], counts["inquiet"], counts["va retirer"], counts["a retiré"], counts["a perdu de l'argent"]]
	var lv: Dictionary = p["leviers"]
	_guarantee_cb.set_pressed_no_signal(bool(lv["garantie"]))
	_ceiling_s.set_value_no_signal(float(lv["plafond_garantie"]))
	_cap_s.set_value_no_signal(float(lv["plafond_retrait"]))
	_silent_cb.set_pressed_no_signal(lv["consigne_employe"] == "silence")


## Coût des appels LLM, mis à jour à chaque llm.called (E4).
func on_llm_call(p: Dictionary) -> void:
	if _llm.is_empty():
		return
	_llm["appels"] += 1
	_llm["tokens"] += int(p["tokens_entree"]) + int(p["tokens_sortie"])
	_llm["latence"] += int(p["latence_ms"])
	if p["statut"] == "ok":
		_llm["ok"] += 1
	var m: String = p["modele"]
	_llm["par_modele"][m] = _llm["par_modele"].get(m, 0) + 1
	_refresh_llm()


func on_memory() -> void:
	if _llm.is_empty():
		return
	_llm["souvenirs"] += 1
	_refresh_llm()


func on_incident() -> void:
	if _llm.is_empty():
		return
	_llm["incidents"] += 1
	_refresh_llm()


func _refresh_llm() -> void:
	var parts: Array = []
	for m in _llm["par_modele"]:
		parts.append("%s %d" % [m, _llm["par_modele"][m]])
	_llm_l.text = "Appels LLM : %d (%d réussis) · %d tokens · %d ms en moyenne · %d incident(s)\n%s\nSouvenirs écrits : %d" % [
		_llm["appels"], _llm["ok"], _llm["tokens"], _llm["latence"] / maxi(_llm["appels"], 1), _llm["incidents"],
		" · ".join(parts), _llm["souvenirs"]]


func on_command(p: Dictionary) -> void:
	var prm: Dictionary = p["parametres"]
	var src: String = prm.get("source", "?")
	log_line("[color=#c7b8f0]Commande « %s » (%s) appliquée.[/color]" % [p["commande"],
		"utilisateur" if src == "godot" else src])
	if p["commande"] in ["fermer_agence", "rouvrir_agence"] and _agency_checks.has(prm.get("agence_id", "")):
		_agency_checks[prm["agence_id"]].set_pressed_no_signal(p["commande"] == "rouvrir_agence")


# ================================================================ inspection (D2)

func show_agent(a: AgentView) -> void:
	_inspect_panel.visible = true
	_inspect_title.text = "Inspection d'un habitant"
	var lines: Array = []
	lines.append("[b]%s[/b] — %s" % [a.display_name, a.profil if a.role == "deposant" else a.role])
	if a.type_agent == "llm":
		lines.append("Agent LLM : %s (%s)" % [a.modele, a.fournisseur])
	else:
		lines.append("Agent à règles")
	if a.role == "deposant":
		lines.append("État : %s, lieu : %s — %s" % [a.etat, a.lieu, a.color_name()])
		lines.append("Peur %.2f · estime que les autres retirent %.2f" % [a.peur, a.estime])
		lines.append("Dépôt %s (initial %s)%s" % [fmt_money(a.depot), fmt_money(a.depot_initial),
			(" · perdu %s" % fmt_money(a.perdu)) if a.perdu > 0.0 else ""])
		if a.connecte:
			lines.append("Très connecté : lit l'actualité en ligne")
	else:
		lines.append("Lieu : %s" % a.lieu)
	lines.append("")
	if a.derniere_decision.is_empty():
		lines.append("[i]Aucune décision publiée pour l'instant.[/i]")
	else:
		var d: Dictionary = a.derniere_decision
		lines.append("[b]Dernière décision[/b] (%s) : %s" % [d.get("quand", ""), d["action"]])
		match d.get("type", "regles"):
			"llm":
				lines.append("Décision du LLM %s (appel %s)" % [a.modele, d["call_id"]])
			"repli_regles":
				lines.append("[color=#f0b46a]Repli sur la règle %s : l'appel LLM a échoué[/color]" % d["regle_id"])
			_:
				lines.append("Règle appliquée : %s" % d["regle_id"])
		lines.append("Justification : %s" % d["justification"])
		var cites: Array = d.get("souvenirs", [])
		if not cites.is_empty():
			lines.append("[b]Souvenirs qui lui sont revenus[/b]")
			for mid in cites:
				var s: Dictionary = main.souvenirs.get(mid, {})
				lines.append("· %s" % (("%s — %s" % [s["quand"], s["texte"]]) if not s.is_empty() else mid))
		var docs: Array = d.get("documents", [])
		if not docs.is_empty():
			var noms: Array = []
			for doc in docs:
				noms.append("%s (%.2f)" % [doc["doc_id"], float(doc["score"])])
			lines.append("Documents consultés : " + ", ".join(noms))
	lines.append("")
	lines.append("[b]Derniers échanges[/b]")
	if a.conversations.is_empty():
		lines.append("[i]aucun[/i]")
	for c in a.conversations:
		lines.append("· " + c)
	lines.append("")
	if a.type_agent == "llm":
		lines.append("[b]Derniers souvenirs[/b] (%d au total)" % a.memoires.size())
		if a.memoires.is_empty():
			lines.append("[i]aucun pour l'instant[/i]")
		for s in a.memoires.slice(maxi(0, a.memoires.size() - 5)):
			lines.append("· %s [color=#aebbb4](%s)[/color] — %s" % [s["quand"], s["nature"], s["texte"]])
	_inspect.text = "\n".join(lines)


# ================================================================ sources de l'ICC (B4, F6)

const CAUSES := {"article": "a lu ou entendu parler de l'article", "conversation": "conversation",
	"ambiance": "ambiance du lieu", "file_observee": "a vu une file devant une agence",
	"fermeture_agence": "a vu une agence fermée", "guichet": "échange au guichet",
	"oubli": "s'apaise avec le temps", "communique": "communiqué de la Caisse", "rumeur": "rumeur"}
const FENETRE_PAS := 4      # on remonte 1 h en arrière (4 pas de 15 min)


func _nom(id: String) -> String:
	var a: AgentView = main.agents.get(id)
	return a.display_name if a else id


## Explique la variation de l'ICC au point i : les croyances qui ont changé pendant l'heure qui précède,
## regroupées par cause, puis les conversations et les faits qui les ont provoquées.
func show_icc_sources(i: int) -> void:
	if main.selected:
		main.selected.set_selected(false)
		main.selected = null
	var pts: Array = _courbe.points
	var pt: Dictionary = pts[i]
	var avant: float = pts[i - FENETRE_PAS]["v"] if i >= FENETRE_PAS else 0.0
	var d: float = float(pt["v"]) - avant
	var sources: Array = []
	for k in range(maxi(0, i - FENETRE_PAS + 1), i + 1):
		sources.append_array(pts[k]["sources"])
	var lines: Array = []
	lines.append("[b]%s — ICC %.2f[/b] (%s%.2f en 1 h)" % [_t_label(int(pt["t"])), float(pt["v"]),
		"+" if d >= 0.0 else "", d])
	lines.append("attente %d %% × convergence %d %%" % [int(float(pt["c"].get("attente", 0)) * 100),
		int(float(pt["c"].get("convergence", 0)) * 100)])
	lines.append("")
	if sources.is_empty():
		lines.append("[i]Aucune croyance n'a changé pendant cette heure : l'ICC est stable.[/i]")
		_show_text("Sources de l'ICC", lines)
		return
	var par_cause := {}
	var hausses := 0
	var faits := {}            # event_id -> trace (conversations, article, communiqués, commandes, guichet)
	var exemples: Array = []
	for sid in sources:
		var b: Dictionary = main.traces.get(sid, {})
		if b.is_empty():
			continue
		var bp: Dictionary = b["p"]
		var cause: String = bp.get("cause", "")
		par_cause[cause] = par_cause.get(cause, 0) + 1
		if float(bp["estime_autres"]) > float(bp["estime_autres_avant"]):
			hausses += 1
		if exemples.size() < 6:
			exemples.append("· %s : estime que les autres retirent %.2f → %.2f, peur %.2f → %.2f (%s)" % [
				_nom(bp["agent_id"]), float(bp["estime_autres_avant"]), float(bp["estime_autres"]),
				float(bp["peur_avant"]), float(bp["peur"]), CAUSES.get(cause, cause)])
		for cid in b["causes"]:
			if main.traces.has(cid):
				faits[cid] = main.traces[cid]
	lines.append("[b]%d changements de croyance[/b] dans l'heure, dont %d à la hausse :" % [sources.size(), hausses])
	var causes_tri: Array = par_cause.keys()
	causes_tri.sort_custom(func(x, y): return par_cause[x] > par_cause[y])
	for c in causes_tri:
		lines.append("   %d × %s" % [par_cause[c], CAUSES.get(c, c)])
	lines.append("")
	lines.append("[b]Exemples[/b]")
	lines.append_array(exemples)
	lines.append("")
	lines.append("[b]Ce qui a été dit ou publié juste avant[/b]")
	if faits.is_empty():
		lines.append("[i]Pas de conversation à l'origine : ces habitants ont réagi à ce qu'ils ont vu (files, agences fermées, ambiance).[/i]")
	var liste: Array = faits.values()
	liste.sort_custom(func(x, y): return int(x["t"]) < int(y["t"]))
	for f in liste.slice(0, 8):
		lines.append("· " + _fait(f))
	if liste.size() > 8:
		lines.append("[i]… et %d autres[/i]" % (liste.size() - 8))
	_show_text("Sources de l'ICC", lines)


func _fait(f: Dictionary) -> String:
	var p: Dictionary = f["p"]
	var quand := _t_label(int(f["t"]))
	match f["type"]:
		"conversation.held":
			var rep: String = p["reponse"] if p["reponse"] != null else ""
			return "%s, %s — %s à %s : « %s »%s" % [quand, WorldData.site(p["lieu"]).get("name", p["lieu"]),
				_nom(p["locuteur_id"]), _nom(p["auditeur_id"]), p["phrase"], (" — « %s »" % rep) if rep != "" else ""]
		"article.published":
			return "%s — la presse publie « %s »" % [quand, p["titre"]]
		"communique.published":
			return "%s — communiqué %s : « %s »" % [quand, p["ton"], p["texte"]]
		"counter.exchanged":
			return "%s — au guichet, %s demande « %s » ; réponse : « %s »" % [quand, _nom(p["client_id"]),
				p["question"], p["reponse"]]
		"command.issued":
			return "%s — commande « %s »" % [quand, p["commande"]]
	return "%s — %s" % [quand, f["type"]]


func _show_text(title: String, lines: Array) -> void:
	_inspect_panel.visible = true
	_inspect_title.text = title
	_inspect.text = "\n".join(lines)


func hide_agent() -> void:
	_inspect_panel.visible = false


# ================================================================ journal

func clear_log() -> void:
	if _log:
		_log.clear()


func log_line(t: String) -> void:
	if _log == null:
		return
	_log.append_text("[color=#aebbb4]%s[/color]  %s\n" % [clock_text, t])
