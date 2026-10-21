<template>
	<div class="cad-panel-body">
		<template v-if="entities.length === 1">
			<div class="cad-props">
				<div class="cad-prop"><span>{{ __("Type") }}</span><b>{{ __(e.entity_type) }}</b></div>
				<div class="cad-prop"><span>{{ __("Identifier") }}</span><span class="mono">{{ e.entity_id }}</span></div>
				<div class="cad-prop">
					<span>{{ __("Layer") }}</span>
					<select :value="e.cad_layer" :disabled="!editable" @change="update({ cad_layer: $event.target.value })">
						<option v-for="l in layer_list" :key="l.key" :value="l.key">{{ l.layer_name }}</option>
					</select>
				</div>
				<div class="cad-prop">
					<span>{{ __("Colour") }}</span>
					<span class="cad-row">
						<input type="color" :value="e.color || layer_color" :disabled="!editable" @change="update({ color: $event.target.value })" />
						<button v-if="e.color" class="small" :disabled="!editable" @click="update({ color: null })">{{ __("By layer") }}</button>
						<span v-else class="muted small">{{ __("By layer") }}</span>
					</span>
				</div>
				<div class="cad-prop">
					<span>{{ __("Line Type") }}</span>
					<select :value="e.line_type || ''" :disabled="!editable" @change="update({ line_type: $event.target.value || null })">
						<option value="">{{ __("By layer") }}</option>
						<option v-for="t in line_types" :key="t" :value="t">{{ __(t) }}</option>
					</select>
				</div>
				<div class="cad-prop">
					<span>{{ __("Validation") }}</span>
					<span class="cad-chip" :class="chip">{{ dirty ? __("Unsaved") : __(e.validation_status || "Not Checked") }}</span>
				</div>
				<div v-if="e.validation_message" class="is-error small pad">{{ e.validation_message }}</div>
			</div>

			<h4 class="cad-subhead">{{ __("Geometry") }}</h4>
			<div class="cad-props">
				<template v-for="field in geometry_fields" :key="field.key">
					<div class="cad-prop">
						<span>{{ field.label }}</span>
						<input
							v-if="field.kind === 'text'"
							:value="field.value"
							:disabled="!editable"
							@change="set_geometry(field, $event.target.value)"
						/>
						<textarea
							v-else-if="field.kind === 'textarea'"
							:value="field.value"
							:disabled="!editable"
							rows="3"
							@change="set_geometry(field, $event.target.value)"
						></textarea>
						<select v-else-if="field.kind === 'select'" :value="field.value" :disabled="!editable" @change="set_geometry(field, $event.target.value)">
							<option v-for="o in field.options" :key="o.value ?? o" :value="o.value ?? o">{{ o.label ?? o }}</option>
						</select>
						<input
							v-else
							class="mono"
							:value="field.display"
							:disabled="!editable || field.readonly"
							@change="set_geometry(field, $event.target.value)"
						/>
					</div>
				</template>
			</div>

			<h4 class="cad-subhead">{{ __("Measures (Open CASCADE)") }}</h4>
			<div class="cad-props">
				<div v-if="dirty || e.validation_status === 'Not Checked'" class="muted small pad">{{ __("Measured by the CAD worker after the model is saved.") }}</div>
				<template v-else>
					<div v-if="e.entity_type === 'Dimension'" class="cad-prop"><span>{{ __("Measured") }}</span><span class="mono">{{ fmt(e.length) }}</span></div>
					<div v-else-if="e.length" class="cad-prop"><span>{{ __("Length") }}</span><span class="mono">{{ fmt(e.length) }}</span></div>
					<div v-if="e.area" class="cad-prop"><span>{{ __("Area") }}</span><span class="mono">{{ fmt(e.area) }}</span></div>
					<div v-if="e.volume" class="cad-prop"><span>{{ __("Volume") }}</span><span class="mono">{{ fmt(e.volume) }}</span></div>
				</template>
			</div>
		</template>

		<template v-else-if="entities.length > 1">
			<div class="pad"><b>{{ __("{0} objects selected", [entities.length]) }}</b></div>
			<div class="cad-props">
				<div class="cad-prop">
					<span>{{ __("Layer") }}</span>
					<select value="" :disabled="!can_edit" @change="update({ cad_layer: $event.target.value })">
						<option value="" disabled>{{ __("Move to layer…") }}</option>
						<option v-for="l in layer_list" :key="l.key" :value="l.key">{{ l.layer_name }}</option>
					</select>
				</div>
				<div v-for="(count, type) in type_counts" :key="type" class="cad-prop"><span>{{ __(type) }}</span><span>{{ count }}</span></div>
			</div>
		</template>
		<div v-else class="muted pad">{{ __("Select an object to see its properties.") }}</div>
	</div>
</template>

<script setup>
import { computed } from "vue";
import { dimension_measurement, format_number, format_point, loop_area } from "./geometry";

const props = defineProps({
	entities: Array,
	layers: Map,
	can_edit: Boolean,
	decimals: Number,
	dirty_ids: Set,
	angular_decimals: Number,
});
const emit = defineEmits(["update", "update_geometry", "error"]);
const line_types = ["Continuous", "Dashed", "Dotted", "Dash Dot", "Centre", "Hidden", "Phantom"];

const e = computed(() => props.entities[0]);
const dirty = computed(() => props.dirty_ids.has(e.value.entity_id));
const layer_list = computed(() => [...props.layers.entries()].map(([key, l]) => ({ key, layer_name: l.layer_name })));
const layer_color = computed(() => (props.layers.get(e.value.cad_layer) || {}).color || "#e0e0e0");
const editable = computed(() => props.can_edit && !(props.layers.get(e.value.cad_layer) || {}).locked);
const chip = computed(() => (dirty.value ? "info" : { Valid: "ok", Warning: "warning", Invalid: "error" }[e.value.validation_status] || ""));
const type_counts = computed(() => props.entities.reduce((acc, x) => ({ ...acc, [x.entity_type]: (acc[x.entity_type] || 0) + 1 }), {}));

function fmt(v) {
	return format_number(v, props.decimals);
}

function point_field(key, label, path) {
	const value = path.reduce((o, k) => o && o[k], e.value.geometry);
	return { key, label, kind: "point", path, display: format_point(value, props.decimals) };
}

const deg = (v) => format_number(((v || 0) * 180) / Math.PI, props.angular_decimals);
const num = (key, label, path, value) => ({ key, label, kind: "number", path, display: format_number(value, props.decimals) });
const ang = (key, label, path, value) => ({ key, label, kind: "angle", path, display: deg(value) });
const info = (key, label, display) => ({ key, label, kind: "info", readonly: true, display: String(display ?? "") });
const select = (key, label, path, value, options) => ({ key, label, kind: "select", path, value, options });

const geometry_fields = computed(() => {
	const g = e.value.geometry || {};
	switch (e.value.entity_type) {
		case "Point":
			return [point_field("p", "X, Y, Z", ["p"])];
		case "Line":
			return [point_field("p0", __("Start"), ["p0"]), point_field("p1", __("End"), ["p1"])];
		case "Circle":
			return [point_field("center", __("Centre"), ["center"]), num("radius", __("Radius"), ["radius"], g.radius)];
		case "Arc":
			return [point_field("center", __("Centre"), ["center"]), num("radius", __("Radius"), ["radius"], g.radius), ang("a0", __("Start angle (°)"), ["start_angle"], g.start_angle), ang("a1", __("End angle (°)"), ["end_angle"], g.end_angle)];
		case "Ellipse":
			return [
				point_field("center", __("Centre"), ["center"]),
				point_field("major", __("Major axis (vector)"), ["major_axis"]),
				num("ratio", __("Axis ratio"), ["ratio"], g.ratio),
				ang("t0", __("Start parameter (°)"), ["start_param"], g.start_param),
				ang("t1", __("End parameter (°)"), ["end_param"], g.end_param),
			];
		case "Spline":
			return [info("deg", __("Degree"), g.degree), info("cp", __("Control points"), g.control_points.length), info("fp", __("Fit points"), (g.fit_points || []).length || "—")];
		case "Construction Line":
			return [point_field("point", __("Through"), ["point"]), info("dir", __("Direction"), format_point(g.direction, 6)), info("ray", __("Ray"), g.ray ? __("Yes") : __("No"))];
		case "Polyline":
			return [
				info("n", __("Vertices"), g.points.length),
				info("closed", __("Closed"), g.closed ? __("Yes") : __("No")),
				info("arcs", __("Arc segments"), (g.bulges || []).filter(Boolean).length),
				...(g.closed ? [info("parea", __("Plan area"), format_number(Math.abs(loop_area(g.points, g.bulges)), props.decimals))] : []),
				...g.points.slice(0, 50).map((p, i) => point_field(`v${i}`, __("Vertex {0}", [i + 1]), ["points", i])),
			];
		case "Text":
			return [
				point_field("position", __("Position"), ["position"]),
				{ key: "text", label: __("Text"), kind: "text", path: ["text"], value: g.text },
				num("height", __("Height"), ["height"], g.height),
				ang("rot", __("Rotation (°)"), ["rotation"], g.rotation),
				num("wf", __("Width factor"), ["width_factor"], g.width_factor || 1),
				select("ha", __("Horizontal"), ["halign"], g.halign || "left", ["left", "center", "right"]),
				select("va", __("Vertical"), ["valign"], g.valign || "baseline", ["baseline", "bottom", "middle", "top"]),
			];
		case "Multiline Text":
			return [
				point_field("position", __("Position"), ["position"]),
				{ key: "text", label: __("Text"), kind: "textarea", path: ["text"], value: g.text },
				num("height", __("Height"), ["height"], g.height),
				ang("rot", __("Rotation (°)"), ["rotation"], g.rotation),
				num("width", __("Wrap width (0 = none)"), ["width"], g.width),
				select("att", __("Attachment"), ["attachment"], g.attachment, [1, 2, 3, 4, 5, 6, 7, 8, 9].map((v) => ({ value: v, label: ["Top left", "Top centre", "Top right", "Middle left", "Middle centre", "Middle right", "Bottom left", "Bottom centre", "Bottom right"][v - 1] }))),
			];
		case "Dimension":
			return [
				info("kind", __("Kind"), __(g.kind)),
				info("value", __("Measures"), format_number(dimension_measurement(g), g.kind === "angular" ? props.angular_decimals : props.decimals)),
				point_field("p1", __("First point"), ["p1"]),
				point_field("p2", __("Second point"), ["p2"]),
				point_field("loc", __("Line location"), ["location"]),
				...(g.center ? [point_field("c", __("Centre"), ["center"])] : []),
				num("th", __("Text height"), ["text_height"], g.text_height),
				num("as", __("Arrow size"), ["arrow_size"], g.arrow_size),
				{ key: "ov", label: __("Text override (<> = value)"), kind: "text", path: ["text_override"], value: g.text_override || "" },
			];
		case "Hatch":
			return [
				select("pat", __("Pattern"), ["pattern"], g.pattern, ["SOLID", "ANSI31", "ANSI37", "LINES", "GRID", "DOTS"]),
				num("sc", __("Pattern scale"), ["scale"], g.scale),
				ang("an", __("Pattern angle (°)"), ["angle"], g.angle),
				info("loops", __("Boundaries"), g.loops.length),
				info("harea", __("Plan area"), format_number(Math.abs(loop_area(g.loops[0].points, g.loops[0].bulges)) - g.loops.slice(1).reduce((s, l) => s + Math.abs(loop_area(l.points, l.bulges)), 0), props.decimals)),
			];
		case "Block Reference":
			return [
				info("block", __("Block"), g.block),
				point_field("ins", __("Insertion point"), ["insert"]),
				point_field("scale", __("Scale X, Y, Z"), ["scale"]),
				ang("rot", __("Rotation (°)"), ["rotation"], g.rotation),
			];
		case "Solid":
			return [info("op", __("Made by"), (g.source && g.source.op) || ""), info("ref", __("B-Rep (SHA-256)"), (e.value.geometry_ref || "").slice(0, 16) + "…")];
		default:
			return [];
	}
});

function update(values) {
	emit("update", props.entities.map((x) => x.entity_id), values);
}

function set_geometry(field, text) {
	let value;
	if (field.kind === "text" || field.kind === "textarea") value = text;
	else if (field.kind === "select") value = /^\d+$/.test(text) ? Number(text) : text;
	else if (field.kind === "point") {
		const parts = text.split(/[,;\s]+/).filter(Boolean).map(Number);
		if (parts.length < 2 || parts.length > 3 || parts.some((v) => !Number.isFinite(v))) return emit("error", __("Enter X, Y or X, Y, Z."));
		value = parts.length === 2 ? [...parts, 0] : parts;
	} else {
		value = Number(text);
		if (!Number.isFinite(value)) return emit("error", __("Enter a number."));
		if (field.kind === "angle") value = (value * Math.PI) / 180;
	}
	const geometry = JSON.parse(JSON.stringify(e.value.geometry));
	let target = geometry;
	for (const k of field.path.slice(0, -1)) target = target[k];
	target[field.path[field.path.length - 1]] = value;
	emit("update_geometry", e.value.entity_id, geometry);
}
</script>
