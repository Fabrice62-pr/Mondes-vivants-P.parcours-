// Routes de village : terre battue et gravier, ornières, bords qui se fondent dans l'herbe.
// UV.x va d'un bord à l'autre (0 → 1), UV.y suit la route (en mètres).
shader_type spatial;
render_mode blend_mix, depth_draw_opaque;

#include "res://shaders/bruit.gdshaderinc"

uniform vec3 base : source_color = vec3(0.74, 0.69, 0.58);
uniform vec3 worn : source_color = vec3(0.62, 0.57, 0.47);

varying vec3 wpos;

void vertex() {
	wpos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
}

void fragment() {
	float n = fbm(wpos.xz * 0.35);
	float g = vnoise(wpos.xz * 9.0);
	float x = abs(UV.x - 0.5) * 2.0;
	vec3 col = mix(base, worn, smoothstep(0.35, 0.75, n));
	// Deux ornières, là où passent les roues.
	float rut = smoothstep(0.12, 0.0, abs(x - 0.5));
	col *= 1.0 - rut * 0.12;
	col *= 0.9 + 0.2 * g;
	ALBEDO = col;
	ROUGHNESS = 0.95;
	SPECULAR = 0.2;
	ALPHA = 1.0 - smoothstep(0.78, 1.0, x + (n - 0.5) * 0.25);
}
