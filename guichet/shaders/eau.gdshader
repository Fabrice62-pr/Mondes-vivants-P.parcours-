// Rivière : vaguelettes animées qui reflètent le ciel, berges plus claires et fondues.
// UV.x va d'une berge à l'autre (0 → 1), UV.y suit le courant (en mètres).
shader_type spatial;
render_mode blend_mix, depth_draw_opaque, cull_disabled;

#include "res://shaders/bruit.gdshaderinc"

uniform vec3 deep : source_color = vec3(0.14, 0.30, 0.36);
uniform vec3 shallow : source_color = vec3(0.34, 0.52, 0.52);
uniform float flow_speed = 0.7;

varying vec3 wpos;

float waves(vec2 p, float t) {
	return fbm(p * 0.45 + vec2(t * 0.22, t * 0.08)) + 0.5 * fbm(p * 1.4 - vec2(t * 0.3, -t * 0.18));
}

void vertex() {
	wpos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
}

void fragment() {
	float t = TIME;
	vec2 p = wpos.xz;
	float e = 0.12;
	float h = waves(p, t);
	float hx = waves(p + vec2(e, 0.0), t);
	float hz = waves(p + vec2(0.0, e), t);
	vec3 nw = normalize(vec3(-(hx - h) / e * 0.3, 1.0, -(hz - h) / e * 0.3));
	NORMAL = normalize((VIEW_MATRIX * vec4(nw, 0.0)).xyz);
	float edge = abs(UV.x - 0.5) * 2.0;
	vec3 col = mix(deep, shallow, smoothstep(0.45, 1.0, edge));
	// Traînées d'écume emportées par le courant.
	float foam = smoothstep(0.68, 0.82, fbm(vec2(UV.y * 0.5 - t * flow_speed, UV.x * 7.0)));
	col += foam * 0.18;
	ALBEDO = col;
	ROUGHNESS = 0.05;
	SPECULAR = 0.6;
	ALPHA = 1.0 - smoothstep(0.8, 1.0, edge) * 0.85;
}
