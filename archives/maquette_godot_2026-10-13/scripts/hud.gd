class_name Hud
extends CanvasLayer
## Interface : état de la Caisse, indicateur de basculement, leviers et journal.

var sim: Node

var _time_l: Label
var _state_l: Label
var _reserve_bar: ProgressBar
var _reserve_l: Label
var _tip_l: Label
var _pop_l: Label
var _belief_l: Label
var _llm_l: Label
var _log: RichTextLabel
var _agency_checks := {}
var _guarantee_cb: CheckBox
var _ceiling_s: HSlider
var _cap_s: HSlider
var _silent_cb: CheckBox
var _pause_b: Button
var _labels_cb: CheckBox
var _relaunch := {}   # clé param -> contrôle


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	var root := Control.new()
	root.set_anchors_preset(Control.PRESET_FULL_RECT)
	root.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(root)

	# ---------- panneau gauche
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

	_time_l = _label(v)
	_state_l = _label(v)
	_reserve_bar = ProgressBar.new()
	_reserve_bar.max_value = 1.0
	_reserve_bar.step = 0.001
	_reserve_bar.show_percentage = false
	_reserve_bar.custom_minimum_size = Vector2(0, 18)
	v.add_child(_reserve_bar)
	_reserve_l = _label(v)
	_tip_l = _label(v)
	_tip_l.add_theme_font_size_override("font_size", 18)
	_pop_l = _label(v)
	_belief_l = _label(v)
	_llm_l = _label(v)

	_section(v, "Leviers en direct")
	_guarantee_cb = CheckBox.new()
	_guarantee_cb.text = "Garantie des dépôts"
	_guarantee_cb.toggled.connect(func(on): sim.params["guarantee"] = on)
	v.add_child(_guarantee_cb)
	_ceiling_s = _slider(v, "Plafond de garantie", 0, 80000, 5000, "guarantee_ceiling", "money")
	_cap_s = _slider(v, "Retrait max par passage", 0.1, 1.0, 0.05, "withdraw_cap", "pct")

	var comm := HBoxContainer.new()
	var tone := OptionButton.new()
	tone.add_item("Rassurant")
	tone.add_item("Factuel")
	tone.add_item("Vague")
	tone.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	comm.add_child(tone)
	var pub := Button.new()
	pub.text = "Publier le communiqué"
	pub.pressed.connect(func(): sim.publish_communique(tone.selected))
	comm.add_child(pub)
	v.add_child(comm)

	for ag in sim.agency_list:
		var cb := CheckBox.new()
		cb.text = "%s ouverte" % ag.display_name
		cb.button_pressed = true
		var id: String = ag.agency_id
		cb.toggled.connect(func(on): sim.set_agency_open(id, on))
		v.add_child(cb)
		_agency_checks[id] = cb
	_silent_cb = CheckBox.new()
	_silent_cb.text = "Employé d'agence tenu au silence"
	_silent_cb.toggled.connect(func(on): sim.params["employee_silent"] = on)
	v.add_child(_silent_cb)

	_section(v, "Au prochain lancement")
	_relaunch["population"] = _slider(v, "Déposants", 20, 400, 10, "population", "int")
	_relaunch["reserve_ratio"] = _slider(v, "Réserves en caisse", 0.05, 0.4, 0.01, "reserve_ratio", "pct")
	_relaunch["connected_share"] = _slider(v, "Part de très connectés", 0.0, 0.6, 0.05, "connected_share", "pct")
	_relaunch["loan_loss"] = _slider(v, "Pertes réelles sur prêts", 0.0, 0.4, 0.01, "loan_loss", "pct")
	_relaunch["rumor_strength"] = _slider(v, "Force de la rumeur", 0.1, 1.0, 0.05, "rumor_strength", "pct")
	_relaunch["rumor_time"] = _slider(v, "Article après", 5, 90, 5, "rumor_time", "sec")
	var llm_cb := CheckBox.new()
	llm_cb.text = "Dialogues par LLM (proxy local)"
	llm_cb.toggled.connect(func(on): sim.params["use_llm"] = on)
	v.add_child(llm_cb)
	_relaunch["use_llm"] = llm_cb
	var url := LineEdit.new()
	url.text_changed.connect(func(t): sim.params["llm_endpoint"] = t)
	v.add_child(url)
	_relaunch["llm_endpoint"] = url
	_relaunch["llm_budget"] = _slider(v, "Budget d'appels LLM", 0, 1000, 10, "llm_budget", "int")

	var row := HBoxContainer.new()
	var restart := Button.new()
	restart.text = "Relancer"
	restart.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	restart.pressed.connect(func(): sim.reset_simulation())
	row.add_child(restart)
	_pause_b = Button.new()
	_pause_b.text = "Pause"
	_pause_b.pressed.connect(_toggle_pause)
	row.add_child(_pause_b)
	v.add_child(row)
	_slider_custom(v, "Vitesse", 0.25, 4.0, 0.25, 1.0, _set_speed, "x")
	_labels_cb = CheckBox.new()
	_labels_cb.text = "Étiquettes des lieux"
	_labels_cb.button_pressed = true
	_labels_cb.toggled.connect(_set_labels_visible)
	v.add_child(_labels_cb)
	var help := Label.new()
	help.text = "Caméra : glisser pour tourner, molette pour zoomer, Maj + glisser ou ZQSD pour se déplacer. R : vue d'ensemble, T : vue de dessus."
	help.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	help.add_theme_color_override("font_color", Color("aebbb4"))
	help.add_theme_font_size_override("font_size", 13)
	v.add_child(help)

	# ---------- journal à droite
	var right := PanelContainer.new()
	right.anchor_left = 1.0
	right.anchor_right = 1.0
	right.anchor_bottom = 1.0
	right.offset_left = -440
	right.offset_right = -12
	right.offset_top = 12
	right.offset_bottom = -12
	right.add_theme_stylebox_override("panel", _panel_style())
	root.add_child(right)
	var rv := VBoxContainer.new()
	right.add_child(rv)
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
	legend.text = "Vert : calme · Orange : inquiet · Rouge : va retirer · Gris : a retiré · Noir : a perdu"
	legend.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	legend.add_theme_font_size_override("font_size", 13)
	rv.add_child(legend)


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


func _fmt(kind: String, x: float) -> String:
	match kind:
		"money":
			return sim.fmt_money(x)
		"pct":
			return "%d %%" % int(round(x * 100))
		"sec":
			return "%d s" % int(x)
		"x":
			return "×%.2f" % x
	return str(int(x))


func _slider(parent: Control, text: String, mn: float, mx: float, stp: float, key: String, kind: String) -> HSlider:
	var setter := func(x: float) -> void:
		sim.params[key] = int(x) if kind == "int" else x
	return _slider_custom(parent, text, mn, mx, stp, float(sim.params[key]), setter, kind)


func _slider_custom(parent: Control, text: String, mn: float, mx: float, stp: float, val: float,
		cb: Callable, kind: String) -> HSlider:
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
	s.value_changed.connect(_on_slider_changed.bind(l, text, kind, cb))
	return s


func _on_slider_changed(x: float, l: Label, text: String, kind: String, cb: Callable) -> void:
	l.text = "%s : %s" % [text, _fmt(kind, x)]
	cb.call(x)


func _set_speed(x: float) -> void:
	Engine.time_scale = x


func _set_labels_visible(on: bool) -> void:
	for l in sim.builder.labels:
		l.visible = on


func _toggle_pause() -> void:
	get_tree().paused = not get_tree().paused
	_pause_b.text = "Reprendre" if get_tree().paused else "Pause"


## Remet les contrôles en phase avec sim.params (après un lancement).
func refresh_controls() -> void:
	if _guarantee_cb == null:
		return
	_guarantee_cb.set_pressed_no_signal(sim.params["guarantee"])
	_ceiling_s.set_value_no_signal(sim.params["guarantee_ceiling"])
	_cap_s.set_value_no_signal(sim.params["withdraw_cap"])
	_silent_cb.set_pressed_no_signal(sim.params["employee_silent"])
	for id in _agency_checks:
		_agency_checks[id].set_pressed_no_signal(true)
	(_relaunch["use_llm"] as CheckBox).set_pressed_no_signal(sim.params["use_llm"])
	(_relaunch["llm_endpoint"] as LineEdit).text = sim.params["llm_endpoint"]


func clear_log() -> void:
	if _log:
		_log.clear()


func log_line(t: String) -> void:
	if _log == null:
		return
	_log.append_text("[color=#aebbb4]%3d s[/color]  %s\n" % [int(sim.sim_time), t])


func update_stats(st: Dictionary) -> void:
	_time_l.text = "Temps simulé : %d s" % int(st["time"])
	var etat := "EN DÉFAUT" if st["failed"] else "ouverte"
	var solv := "solvable" if st["solvency"] >= 0.0 else "INSOLVABLE"
	_state_l.text = "Caisse %s, %s (marge %s)" % [etat, solv, sim.fmt_money(st["solvency"])]
	_reserve_bar.value = st["reserves"] / maxf(st["reserve_start"], 1.0)
	_reserve_l.text = "Réserves : %s sur %s au départ\nDépôts restants : %s" % [
		sim.fmt_money(st["reserves"]), sim.fmt_money(st["reserve_start"]), sim.fmt_money(st["deposits"])]
	var col := Color("9fd3c0")
	match st["tipping"]:
		"Proche":
			col = Color("f0b46a")
		"Irréversible", "Défaut":
			col = Color("ff8a6a")
	_tip_l.text = "Basculement : %s\nEngagés %s · au bord %s" % [st["tipping"], sim.fmt_money(st["committed"]), sim.fmt_money(st["near"])]
	_tip_l.add_theme_color_override("font_color", col)
	_pop_l.text = "Calmes %d · Inquiets %d · En route %d · Ont retiré %d · Ont perdu %d" % [
		st["calm"], st["worried"], st["run"], st["done"], st["lost"]]
	_belief_l.text = "Peur moyenne %d %% · « les autres vont retirer » %d %%" % [
		int(st["avg_fear"] * 100), int(st["avg_est"] * 100)]
	if not sim.params["use_llm"]:
		_llm_l.text = "Dialogues : règles locales (LLM désactivé)"
	elif sim.llm.unavailable:
		_llm_l.text = "LLM : proxy injoignable, dialogues par règles locales"
	else:
		_llm_l.text = "Appels LLM : %d / %d" % [st["llm_calls"], st["llm_budget"]]
