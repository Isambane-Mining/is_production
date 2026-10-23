<template>
	<div class="cad-object-panel" @keydown.stop="on_key">
		<div class="cad-row cad-object-head">
			<b class="cad-grow">{{ title }}</b>
			<button class="cad-icon-btn" :title="__('Close (Esc)')" @click="$emit('close')"><Icon name="close" /></button>
		</div>
		<div class="cad-object-body">
			<!-- appearance -->
			<div class="cad-form-grid compact">
				<label>{{ __("Layer") }}</label>
				<select :value="entity.cad_layer" :disabled="!can_edit" @change="$emit('update', { cad_layer: $event.target.value })">
					<option v-for="l in layer_list" :key="l.key" :value="l.key">{{ l.layer_name }}</option>
				</select>
				<label>{{ __("Colour") }}</label>
				<span class="cad-row">
					<input type="color" :value="entity.color || layer.color || '#e0e0e0'" :disabled="!can_edit" @input="$emit('update', { color: $event.target.value })" />
					<button v-if="entity.color" class="small" :disabled="!can_edit" @click="$emit('update', { color: null })">{{ __("By layer") }}</button>
					<span v-else class="muted small">{{ __("By layer") }}</span>
				</span>
				<template v-if="has_lines">
					<label>{{ __("Line weight") }}</label>
					<select :value="String(entity.line_weight || 0)" :disabled="!can_edit" @change="$emit('update', { line_weight: Number($event.target.value) || 0 })">
						<option value="0">{{ __("By layer ({0} mm)", [layer.line_weight || 0.25]) }}</option>
						<option v-for="w in WEIGHTS" :key="w" :value="String(w)">{{ w.toFixed(2) }} mm</option>
					</select>
					<label>{{ __("Line type") }}</label>
					<select :value="entity.line_type || ''" :disabled="!can_edit" @change="$emit('update', { line_type: $event.target.value || null })">
						<option value="">{{ __("By layer") }}</option>
						<option v-for="t in LINE_TYPES" :key="t" :value="t">{{ __(t) }}</option>
					</select>
				</template>
			</div>

			<!-- closed shapes: outline / filled / solid -->
			<template v-if="shape">
				<h4 class="cad-subhead">{{ __("Shape") }}</h4>
				<div class="cad-segmented">
					<button v-for="m in MODES" :key="m.value" :class="{ on: mode === m.value }" :disabled="!can_edit || (m.value === 'solid' && !worker)" :title="m.hint" @click="$emit('mode', m.value)">{{ m.label }}</button>
				</div>
				<p v-if="mode === 'filled'" class="muted small">{{ __("Filled with its colour (a SOLID hatch in DXF). The outline stays the editable object.") }}</p>
				<p v-if="mode === 'outline'" class="muted small">{{ __("Lines only.") }}</p>
			</template>

			<!-- extrude -->
			<template v-if="mode === 'solid' && extrude">
				<h4 class="cad-subhead">{{ editing ? __("Edit Extrusion") : __("Extrude") }}{{ profiles > 1 ? ` · ${__("{0} profiles", [profiles])}` : "" }}</h4>
				<p v-if="editing" class="small muted">{{ __("This solid remembers how it was made. Change the values (or drag the arrow); OK rebuilds it in place.") }}</p>
				<p v-if="stale" class="small is-warning">{{ __("An outline changed since this solid was made: OK rebuilds it from the outline as it is now.") }}</p>
				<div class="cad-form-grid compact">
					<label>{{ __("Distance") }}</label>
					<span class="cad-row">
						<input ref="distance_el" class="mono cad-grow" type="number" step="any" :value="round(extrude.distance)" @input="set('distance', Number($event.target.value))" />
						<span class="muted small">{{ units }}</span>
					</span>
					<label>{{ __("Direction") }}</label>
					<select :value="extrude.direction" @change="set('direction', $event.target.value)">
						<option value="one">{{ __("One side") }}</option>
						<option value="symmetric">{{ __("Symmetric") }}</option>
					</select>
					<label>{{ __("Taper angle (°)") }}</label>
					<input class="mono" type="number" step="any" :value="extrude.taper" @input="set('taper', Number($event.target.value) || 0)" />
					<label>{{ __("Operation") }}</label>
					<select :value="extrude.operation" :disabled="editing" @change="set('operation', $event.target.value)">
						<option value="new">{{ __("New body") }}</option>
						<option value="join" :disabled="!targets.length">{{ __("Join") }}</option>
						<option value="cut" :disabled="!targets.length">{{ __("Cut") }}</option>
						<option value="intersect" :disabled="!targets.length">{{ __("Intersect") }}</option>
					</select>
					<template v-if="extrude.operation !== 'new' && !editing">
						<label>{{ __("Body") }}</label>
						<select :value="extrude.target" @change="set('target', $event.target.value)">
							<option v-for="t in targets" :key="t.entity_id" :value="t.entity_id">{{ t.label }}</option>
						</select>
					</template>
					<template v-if="!editing">
						<label>{{ __("Outline") }}</label>
						<label class="cad-row small"><input type="checkbox" :checked="extrude.keep" @change="set('keep', $event.target.checked)" /> {{ __("Keep the outline") }}</label>
					</template>
				</div>
				<p class="muted small">{{ __("Drag the orange arrow in the view (Shift: finer steps), or type the distance. + taper narrows the far end.") }}</p>
				<p v-if="extrude.error" class="is-error small">{{ extrude.error }}</p>
			</template>

			<!-- the solid's finishing operations (feature history) -->
			<template v-if="history && history.length">
				<h4 class="cad-subhead">{{ __("Features (in order)") }}</h4>
				<div v-for="(h, i) in history" :key="i" class="cad-row small">
					<span class="cad-grow">{{ i + 1 }}. {{ h }}</span>
					<button class="small" :disabled="!can_edit || busy" @click="$emit('history_edit', i)">{{ __("Edit") }}</button>
					<button class="small" :disabled="!can_edit || busy" @click="$emit('history_remove', i)">{{ __("Remove") }}</button>
				</div>
				<p class="muted small">{{ __("Changing a step (or the extrusion) rebuilds the solid with all its steps.") }}</p>
			</template>
			
			<!-- solids -->
			<template v-if="entity.entity_type === 'Solid' && !editing">
				<div class="cad-report">
					<div><span>{{ __("Volume") }}</span><b class="mono">{{ fmt(entity.volume) }}</b></div>
					<div><span>{{ __("Area") }}</span><b class="mono">{{ fmt(entity.area) }}</b></div>
				</div>
				<p class="muted small">{{ __("This solid was not made by the Extrude tool window, so it has no editable feature.") }}</p>
			</template>
		</div>
		<div class="cad-row cad-object-foot">
			<span class="cad-grow"></span>
			<button @click="$emit('close')">{{ mode === "solid" ? __("Cancel") : __("Close") }}</button>
			<button v-if="mode === 'solid'" class="cad-primary" :disabled="busy || !can_edit || !extrude || !extrude.distance" @click="$emit('ok')">{{ busy ? __("Building…") : __("OK") }}</button>
		</div>
	</div>
</template>

<script setup>
import { computed, nextTick, ref, watch } from "vue";
import { format_number } from "./geometry";
import Icon from "./Icon.vue";

const props = defineProps({
	entity: Object,
	layers: Map,
	can_edit: Boolean,
	decimals: Number,
	mode: String, // outline | filled | solid | null (not a closed shape)
	extrude: Object, // {distance, direction, taper, operation, target, keep, error}
	targets: { type: Array, default: () => [] },
	profiles: { type: Number, default: 1 },
	units: { type: String, default: "m" },
	worker: Boolean,
	busy: Boolean,
	editing: Boolean, // the window edits an existing extruded solid
	history: { type: Array, default: () => [] }, // labels of the solid's finishing operations
	stale: Boolean, // one of its outlines changed since
});
const emit = defineEmits(["update", "mode", "extrude", "ok", "close", "history_edit", "history_remove"]);

const WEIGHTS = [0.13, 0.18, 0.25, 0.35, 0.5, 0.7, 1.0, 1.4, 2.0];
const LINE_TYPES = ["Continuous", "Dashed", "Dotted", "Dash Dot", "Centre", "Hidden", "Phantom"];
const MODES = [
	{ value: "outline", label: __("Outline"), hint: __("Lines only") },
	{ value: "filled", label: __("Filled"), hint: __("A filled 2D area") },
	{ value: "solid", label: __("Solid"), hint: __("Extrude into a 3D solid") },
];
const distance_el = ref(null);

const layer = computed(() => props.layers.get(props.entity.cad_layer) || {});
const layer_list = computed(() => [...props.layers.entries()].map(([key, l]) => ({ key, layer_name: l.layer_name })));
const shape = computed(() => props.mode !== null && props.mode !== undefined);
const has_lines = computed(() => !["Solid", "Surface", "Mesh", "Text", "Multiline Text"].includes(props.entity.entity_type));
const title = computed(() => (props.editing ? __("Edit Extrusion") : props.mode === "solid" ? __("Extrude") : __(props.entity.entity_type)) + (props.entity.entity_id ? ` · ${props.entity.entity_id}` : ""));

watch(
	() => props.mode,
	(m) => m === "solid" && nextTick(() => distance_el.value && distance_el.value.select()),
	{ immediate: true }
);

function set(key, value) {
	emit("extrude", { [key]: value });
}

function round(v) {
	return Number(Number(v || 0).toFixed(Math.min(props.decimals ?? 3, 6)));
}

function fmt(v) {
	return format_number(v, props.decimals);
}

function on_key(event) {
	if (event.key === "Escape") emit("close");
	else if (event.key === "Enter" && props.mode === "solid") emit("ok");
}
</script>
