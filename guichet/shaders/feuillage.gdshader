// Feuillages et herbes (MultiMesh, une couleur par instance) qui ondulent au vent.
// sway : amplitude (m) par mètre de hauteur locale ; shift : déplacement d'ensemble (couronnes).
shader_type spatial;
render_mode cull_disabled;

uniform float sway = 0.0;
uniform float shift = 0.0;
uniform float backlight = 0.3;

void vertex() {
	vec3 wp = (MODEL_MATRIX * vec4(0.0, 0.0, 0.0, 1.0)).xyz;
	float s = sin(TIME * 1.3 + wp.x * 0.21 + wp.z * 0.17) + 0.4 * sin(TIME * 2.9 + wp.x * 0.53);
	float k = shift + sway * max(VERTEX.y, 0.0);
	VERTEX.x += s * k;
	VERTEX.z += s * k * 0.6;
}

void fragment() {
	vec3 c = pow(COLOR.rgb, vec3(2.2));   // couleurs d'instance données en sRGB
	ALBEDO = c;
	ROUGHNESS = 0.9;
	SPECULAR = 0.2;
	BACKLIGHT = c * backlight;
}
