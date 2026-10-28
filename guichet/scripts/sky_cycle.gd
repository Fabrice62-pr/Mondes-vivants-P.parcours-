class_name SkyCycle
extends Node3D
## Ciel, soleil et lune réglés sur l'horloge simulée : lumière rasante le matin, ciel orangé au
## coucher, nuit bleutée (assez claire pour lire les couleurs des habitants) avec fenêtres et
## lampadaires allumés.

const SUNRISE := 7.0
const SUNSET := 19.5
const MAX_ELEV := 40.0      # degrés : soleil d'automne, ombres longues

# Couleurs du ciel [zénith, horizon] : nuit, aube/crépuscule, jour
const SKY_NIGHT := [Color("0b1328"), Color("1d2a45")]
const SKY_DUSK := [Color("3b4f86"), Color("f0a46c")]
const SKY_DAY := [Color("5d8fc9"), Color("c9dbe6")]

signal night_changed(f: float)

var env: Environment
var sun: DirectionalLight3D
var sky_mat: ProceduralSkyMaterial
var hours := 10.0           # heure affichée (absolue : jour × 24 + heure)
var _target := 10.0
var _rate := 0.5            # heures simulées par seconde réelle
var night := 0.0


func _ready() -> void:
	sky_mat = ProceduralSkyMaterial.new()
	sky_mat.sun_angle_max = 8.0
	sky_mat.sun_curve = 0.12
	sky_mat.ground_bottom_color = Color("3e4a36")
	var sky := Sky.new()
	sky.sky_material = sky_mat
	env = Environment.new()
	env.background_mode = Environment.BG_SKY
	env.sky = sky
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.reflected_light_source = Environment.REFLECTION_SOURCE_SKY
	env.tonemap_mode = Environment.TONE_MAPPER_FILMIC
	env.tonemap_white = 6.0
	env.ssao_enabled = true
	env.ssao_radius = 1.4
	env.ssao_intensity = 1.8
	env.ssao_power = 1.4
	env.glow_enabled = true
	env.glow_intensity = 0.55
	env.glow_bloom = 0.03
	env.glow_hdr_threshold = 1.1
	env.fog_enabled = true
	env.fog_density = 0.0006
	env.fog_sun_scatter = 0.25
	env.fog_aerial_perspective = 0.35
	env.adjustment_enabled = true
	env.adjustment_saturation = 1.08
	var we := WorldEnvironment.new()
	we.environment = env
	add_child(we)
	sun = DirectionalLight3D.new()
	sun.name = "Soleil"
	sun.shadow_enabled = true
	sun.directional_shadow_mode = DirectionalLight3D.SHADOW_PARALLEL_4_SPLITS
	sun.directional_shadow_max_distance = 450.0
	sun.shadow_blur = 1.2
	add_child(sun)
	_apply(hours)


## Heure simulée reçue à chaque pas (clock.tick). rate : heures simulées par seconde réelle.
func set_clock(jour: int, heure: int, minute: int, rate: float) -> void:
	_target = jour * 24.0 + heure + minute / 60.0
	_rate = rate
	if absf(_target - hours) > 3.0:
		hours = _target   # début de run, rejeu : pas de longue transition


func _process(delta: float) -> void:
	if hours != _target:
		# On avance au rythme de l'horloge, un peu plus vite si l'on est en retard.
		hours = move_toward(hours, _target, delta * _rate * maxf(1.0, absf(_target - hours) * 4.0))
		_apply(hours)


func _apply(h_abs: float) -> void:
	var h := fposmod(h_abs, 24.0)
	var frac := (h - SUNRISE) / (SUNSET - SUNRISE)
	var elev := sin(clampf(frac, -0.15, 1.15) * PI) * MAX_ELEV
	var az := lerpf(0.0, PI, clampf(frac, 0.0, 1.0))   # est (+X) → sud (+Z) → ouest (-X)
	var day_w := smoothstep(-2.0, 10.0, elev)
	var dusk_w := 1.0 - smoothstep(4.0, 18.0, absf(elev - 2.0))
	night = 1.0 - smoothstep(-6.0, 4.0, elev)
	var light_dir: Vector3
	if elev > -1.0:
		var e := deg_to_rad(maxf(elev, 1.0))
		light_dir = Vector3(cos(az) * cos(e), sin(e), sin(az) * cos(e))
		sun.light_color = Color("ffb877").lerp(Color("fff4e2"), smoothstep(5.0, 25.0, elev))
		sun.light_energy = 1.35 * day_w
	else:
		# La lune prend le relais (même lumière, autre direction) : ombres douces et bleutées.
		var e := deg_to_rad(38.0)
		light_dir = Vector3(cos(2.2) * cos(e), sin(e), sin(2.2) * cos(e))
		sun.light_color = Color("9fb4e0")
		sun.light_energy = 0.32 * smoothstep(-1.0, -8.0, elev)
	sun.basis = Basis.looking_at(-light_dir, Vector3.UP)
	var top: Color = (SKY_NIGHT[0] as Color).lerp(SKY_DAY[0], day_w).lerp(SKY_DUSK[0], dusk_w * 0.6)
	var hor: Color = (SKY_NIGHT[1] as Color).lerp(SKY_DAY[1], day_w).lerp(SKY_DUSK[1], dusk_w * 0.8)
	sky_mat.sky_top_color = top
	sky_mat.sky_horizon_color = hor
	sky_mat.ground_horizon_color = hor.darkened(0.15)
	sky_mat.sky_energy_multiplier = lerpf(0.6, 1.0, day_w)
	env.ambient_light_color = Color("5a6c96").lerp(Color("dfe7ec"), day_w).lerp(Color("f2c9a6"), dusk_w * 0.4)
	env.ambient_light_energy = lerpf(0.75, 0.6, day_w)
	env.fog_light_color = hor
	night_changed.emit(night)
