// Sol de la vallée : prairies tachetées, herbe sèche, roche sur les pentes et les crêtes.
shader_type spatial;

#include "res://shaders/bruit.gdshaderinc"

uniform vec3 grass_a : source_color = vec3(0.47, 0.60, 0.30);
uniform vec3 grass_b : source_color = vec3(0.38, 0.52, 0.25);
uniform vec3 grass_dry : source_color = vec3(0.66, 0.65, 0.40);
uniform vec3 bank : source_color = vec3(0.42, 0.45, 0.30);
uniform vec3 rock_a : source_color = vec3(0.55, 0.53, 0.48);
uniform vec3 rock_b : source_color = vec3(0.70, 0.68, 0.62);

varying vec3 wpos;
varying vec3 wnrm;

void vertex() {
	wpos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
	wnrm = normalize((MODEL_MATRIX * vec4(NORMAL, 0.0)).xyz);
}

void fragment() {
	float n = fbm(wpos.xz * 0.03);
	float n2 = fbm(wpos.xz * 0.21);
	float n3 = vnoise(wpos.xz * 1.7);
	vec3 g = mix(grass_a, grass_b, smoothstep(0.35, 0.65, n));
	g = mix(g, grass_dry, smoothstep(0.58, 0.78, n2) * 0.45);
	g = mix(g, bank, smoothstep(0.9, -0.2, wpos.y) * 0.5);
	float slope = 1.0 - wnrm.y;
	vec3 rock = mix(rock_a, rock_b, n2);
	float rk = smoothstep(0.22, 0.42, slope + (n2 - 0.5) * 0.2);
	rk = max(rk, smoothstep(14.0, 20.0, wpos.y + (n - 0.5) * 8.0));
	vec3 col = mix(g, rock, rk);
	ALBEDO = col * (0.9 + 0.12 * n2 + 0.06 * n3);
	ROUGHNESS = 0.96;
	SPECULAR = 0.25;
}
