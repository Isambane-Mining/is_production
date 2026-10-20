<template>
	<!-- One algorithm (QGIS: the algorithm dialog opened from the Processing Toolbox): parameters, run, log, outputs -->
	<div class="gis-modal-backdrop" @click.self="close">
		<div class="gis-modal gis-modal-wide gis-algorithm-dialog">
			<header class="gis-toolbox-head">
				<h3>{{ selected ? selected.name : __("Loading…") }}</h3>
				<span v-if="selected" class="muted small mono">{{ selected.id }}</span>
				<span class="gis-spacer"></span>
				<button class="gis-icon-btn" :title="__('Close')" @click="close">✕</button>
			</header>
			<div v-if="selected" class="gis-algorithm-body">
				<section class="gis-toolbox-form">
					<p v-if="!selected.supported" class="is-warning small">{{ __("Not available here: {0}.", [selected.unsupported_reason]) }}</p>
					<form v-if="!job" class="gis-form" @submit.prevent="run">
						<template v-for="p in basic_params" :key="p.name"><ParamField :p="p" /></template>
						<details v-if="advanced_params.length">
							<summary class="small">{{ __("Advanced parameters ({0})", [advanced_params.length]) }}</summary>
							<template v-for="p in advanced_params" :key="p.name"><ParamField :p="p" /></template>
						</details>
						<p v-if="error" class="is-error small">{{ error }}</p>
						<div class="gis-modal-actions">
							<button type="button" @click="close">{{ __("Close") }}</button>
							<button class="gis-primary" type="submit" :disabled="busy || !selected.supported">{{ busy ? __("Starting…") : __("Run") }}</button>
						</div>
					</form>
					<div v-else class="gis-job">
						<p><b>{{ __(job.status) }}</b> · {{ job.status_message }}</p>
						<progress :value="job.progress || 0" max="100" class="gis-progress"></progress>
						<table v-if="job.outputs.length || Object.keys(job.results || {}).length" class="gis-table">
							<thead><tr><th>{{ __("Output") }}</th><th>{{ __("Result") }}</th><th></th></tr></thead>
							<tbody>
								<tr v-for="o in job.outputs" :key="o.output_name">
									<td>{{ output_label(o.output_name) }}</td>
									<td><span v-if="o.dataset" class="mono">{{ o.dataset }}</span> {{ o.value || "" }}</td>
									<td>
										<button v-if="o.dataset && !o.file" class="gis-icon-btn" @click="$emit('add-datasets', [o.dataset])">{{ __("Add to Map") }}</button>
										<a v-if="o.file" :href="o.file" target="_blank">{{ __("Open") }}</a>
									</td>
								</tr>
								<tr v-for="(value, key) in job.results" :key="key"><td>{{ output_label(key) }}</td><td class="mono">{{ format_value(value) }}</td><td></td></tr>
							</tbody>
						</table>
						<pre v-if="job.log" class="gis-log">{{ job.log }}</pre>
						<div class="gis-modal-actions">
							<a class="gis-button" :href="`/desk/gis-processing-job/${job.name}`" target="_blank">{{ __("Job in Desk") }}</a>
							<button v-if="running" @click="cancel">{{ __("Cancel") }}</button>
							<button v-else @click="job = null">{{ __("Change Parameters") }}</button>
							<button v-if="!running && output_datasets.length" class="gis-primary" @click="$emit('add-datasets', output_datasets)">{{ __("Add Outputs to Map") }}</button>
						</div>
					</div>
				</section>
				<aside class="gis-algorithm-help small">
					<h5 class="gis-subhead">{{ selected.provider_name }}{{ selected.group ? " › " + selected.group : "" }}</h5>
					<div class="gis-alg-help">{{ plain_text(selected.help) || selected.description || __("No description.") }}</div>
				</aside>
			</div>
			<p v-else-if="error" class="is-error">{{ error }}</p>
		</div>
	</div>
</template>

<script setup>
import { computed, defineComponent, h, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import { call } from "./api.js";

const __ = window.__;
const PROC = "is_production.geo_planning.services.gis_processing_service.";
const props = defineProps({
	algorithmId: String, // open this algorithm
	jobName: String, // or show this earlier job (Processing History)
	datasets: { type: Array, default: () => [] },
	activeLayer: String,
	selectedIds: { type: Array, default: () => [] },
	displayCrs: String,
	mapExtent: Function, // () => "xmin,xmax,ymin,ymax [EPSG:3857]"
	mapCentre: Function, // () => "x,y [EPSG:3857]"
});
const emit = defineEmits(["close", "add-datasets", "datasets-changed"]);

const selected = ref(null); // full algorithm definition
const values = reactive({});
const schemas = reactive({}); // dataset -> attributes, for field parameters
const job = ref(null);
const busy = ref(false);
const error = ref("");
let timer = null;

// QGIS source types: -1 any geometry, 0 point, 1 line, 2 polygon, 5 any vector (also without geometry)
const SOURCE_KINDS = { 0: /Point/, 1: /LineString/, 2: /Polygon/ };

onMounted(async () => {
	if (props.jobName) {
		const existing = await call(PROC + "get_processing_job", { name: props.jobName });
		await choose({ id: existing.algorithm });
		job.value = existing;
		if (running.value) poll();
	} else if (props.algorithmId) {
		await choose({ id: props.algorithmId });
	}
});
onBeforeUnmount(() => clearTimeout(timer));

const visible_params = computed(() => (selected.value ? selected.value.parameters.filter((p) => !p.hidden) : []));
const basic_params = computed(() => visible_params.value.filter((p) => !p.advanced));
const advanced_params = computed(() => visible_params.value.filter((p) => p.advanced));
const running = computed(() => job.value && ["Queued", "Running", "Cancelling"].includes(job.value.status));
const output_datasets = computed(() => (job.value ? job.value.outputs.filter((o) => o.dataset && !o.file).map((o) => o.dataset) : []));
const vector_datasets = computed(() => props.datasets.filter((d) => d.geometry_type !== "Raster"));
const raster_datasets = computed(() => props.datasets.filter((d) => d.geometry_type === "Raster"));

function close() {
	clearTimeout(timer);
	emit("close");
}

async function choose(summary) {
	error.value = "";
	job.value = null;
	clearTimeout(timer);
	try {
		selected.value = await call(PROC + "get_algorithm", { algorithm_id: summary.id });
	} catch (e) {
		error.value = e.message;
		return;
	}
	for (const key of Object.keys(values)) delete values[key];
	const base = summary.id.split(":").pop().replace(/[^a-z0-9]+/gi, "_").toLowerCase();
	for (const p of selected.value.parameters) {
		if (p.hidden) continue;
		if (["source", "vector", "layer", "maplayer"].includes(p.type)) {
			const fits = datasets_for(p);
			const active = fits.find((d) => d.name === props.activeLayer);
			values[p.name] = { dataset: active ? active.name : !p.optional && fits.length === 1 ? fits[0].name : "", selected_only: false };
		} else if (p.type === "multilayer") values[p.name] = [];
		else if (["sink", "vectorDestination", "rasterDestination"].includes(p.type)) values[p.name] = { save: !p.optional, dataset_name: `${base}_${p.name.toLowerCase()}`.slice(0, 50), title: `${selected.value.name}: ${p.description}` };
		else if (p.type === "fileDestination") values[p.name] = { save: !p.optional, name: `${base}_${p.name.toLowerCase()}` };
		else if (p.type === "crs") values[p.name] = p.default || props.displayCrs || "";
		else if (p.type === "enum") values[p.name] = p.default ?? (p.multiple ? [] : 0);
		else if (p.type === "boolean") values[p.name] = !!p.default;
		else values[p.name] = p.default ?? "";
	}
	for (const p of selected.value.parameters) if (values[p.name] && values[p.name].dataset) load_schema(values[p.name].dataset);
}

function datasets_for(p) {
	const types = (p.data_types || []).filter((t) => t in SOURCE_KINDS);
	if (!types.length) return vector_datasets.value;
	return vector_datasets.value.filter((d) => types.some((t) => SOURCE_KINDS[t].test(d.geometry_type) || d.geometry_type === "Geometry"));
}

async function load_schema(name) {
	if (!name || schemas[name]) return;
	try {
		schemas[name] = (await call("is_production.geo_planning.services.gis_edit_service.get_edit_schema", { dataset: name })).attributes;
	} catch (e) {
		schemas[name] = [];
	}
}

function fields_of(p) {
	const parent = values[p.parent];
	return parent && parent.dataset ? schemas[parent.dataset] || [] : [];
}

function payload() {
	const out = {};
	for (const p of visible_params.value) {
		const v = values[p.name];
		if (["source", "vector", "layer", "maplayer", "raster"].includes(p.type)) {
			if (v && v.dataset) out[p.name] = { dataset: v.dataset, selected_ids: v.selected_only && v.dataset === props.activeLayer ? props.selectedIds : [] };
		} else if (p.type === "multilayer") {
			if (v.length) out[p.name] = v.map((name) => ({ dataset: name }));
		} else if (["sink", "vectorDestination", "rasterDestination"].includes(p.type)) {
			if (v.save) out[p.name] = { dataset_name: v.dataset_name, title: v.title };
		} else if (p.type === "fileDestination") {
			if (v.save) out[p.name] = { name: v.name };
		} else if (v !== "" && v !== null && v !== undefined) out[p.name] = v;
	}
	return out;
}

async function run() {
	busy.value = true;
	error.value = "";
	try {
		job.value = await call(PROC + "run_algorithm", { algorithm_id: selected.value.id, parameters: payload() });
		poll();
	} catch (e) {
		error.value = e.message;
	} finally {
		busy.value = false;
	}
}

async function poll() {
	try {
		job.value = await call(PROC + "get_processing_job", { name: job.value.name });
	} catch (e) {
		/* keep polling */
	}
	if (running.value) timer = setTimeout(poll, 1500);
	else if (output_datasets.value.length) emit("datasets-changed");
}

async function cancel() {
	try {
		job.value = await call(PROC + "cancel_processing", { name: job.value.name });
	} catch (e) {
		error.value = e.message;
	}
}

function plain_text(html) {
	const div = document.createElement("div");
	div.innerHTML = String(html || "").replace(/<br\s*\/?>/gi, "\n").replace(/<\/p>/gi, "\n");
	return div.textContent; // shown as text: never as HTML
}

function output_label(name) {
	const o = selected.value && (selected.value.outputs || []).find((x) => x.name === name);
	return o ? o.description || name : name;
}

function format_value(value) {
	if (typeof value === "number") return Number.isInteger(value) ? value : value.toFixed(4);
	return typeof value === "object" ? JSON.stringify(value) : String(value ?? "");
}

// One parameter's input, chosen from its QGIS type.
const ParamField = defineComponent({
	props: { p: Object },
	setup(fieldProps) {
		return () => {
			const p = fieldProps.p;
			const label = h("span", [p.description || p.name, p.optional ? h("span", { class: "muted" }, ` (${__("optional")})`) : null]);
			const v = values[p.name];
			const input = (attrs) => h("input", { class: "gis-input", value: v, onInput: (e) => (values[p.name] = e.target.value), ...attrs });
			let control;
			switch (p.type) {
				case "source":
				case "vector":
				case "layer":
				case "maplayer": {
					const options = datasets_for(p);
					control = [
						h("select", { class: "gis-input", value: v.dataset, onChange: (e) => ((v.dataset = e.target.value), load_schema(v.dataset)) }, [
							h("option", { value: "" }, ""),
							...options.map((d) => h("option", { value: d.name }, `${d.title} (${d.geometry_type})`)),
						]),
						v.dataset && v.dataset === props.activeLayer && props.selectedIds.length
							? h("label", { class: "gis-check small" }, [
									h("input", { type: "checkbox", checked: v.selected_only, onChange: (e) => (v.selected_only = e.target.checked) }),
									" " + __("Selected features only ({0})", [props.selectedIds.length]),
							  ])
							: null,
					];
					break;
				}
				case "raster":
					control = h("select", { class: "gis-input", value: v && v.dataset, onChange: (e) => (values[p.name] = { dataset: e.target.value }) }, [
						h("option", { value: "" }, raster_datasets.value.length ? "" : __("no raster datasets yet")),
						...raster_datasets.value.map((d) => h("option", { value: d.name }, d.title)),
					]);
					break;
				case "multilayer":
					control = h(
						"div",
						{ class: "gis-multi" },
						vector_datasets.value.map((d) =>
							h("label", { class: "gis-check small" }, [
								h("input", { type: "checkbox", checked: v.includes(d.name), onChange: (e) => (values[p.name] = e.target.checked ? [...v, d.name] : v.filter((x) => x !== d.name)) }),
								" " + d.title,
							])
						)
					);
					break;
				case "number":
				case "distance":
				case "scale":
				case "duration":
				case "area":
				case "band":
					control = input({ type: "number", step: p.integer || p.type === "band" ? "1" : "any", min: p.minimum > -1e300 ? p.minimum : undefined, max: p.maximum < 1e300 ? p.maximum : undefined });
					break;
				case "boolean":
					control = h("label", { class: "gis-check" }, [h("input", { type: "checkbox", checked: !!v, onChange: (e) => (values[p.name] = e.target.checked) }), " " + __("Yes")]);
					break;
				case "enum":
					control = p.multiple
						? h("div", { class: "gis-multi" }, p.options.map((o, i) => {
								const key = p.static_strings ? o : i;
								return h("label", { class: "gis-check small" }, [
									h("input", { type: "checkbox", checked: (v || []).includes(key), onChange: (e) => (values[p.name] = e.target.checked ? [...(v || []), key] : (v || []).filter((x) => x !== key)) }),
									" " + o,
								]);
						  }))
						: h("select", { class: "gis-input", value: v, onChange: (e) => (values[p.name] = p.static_strings ? e.target.value : Number(e.target.value)) },
								p.options.map((o, i) => h("option", { value: p.static_strings ? o : i }, o)));
					break;
				case "field": {
					const fields = fields_of(p);
					control = fields.length
						? h("select", { class: "gis-input", multiple: p.multiple, value: v, onChange: (e) => (values[p.name] = p.multiple ? [...e.target.selectedOptions].map((o) => o.value) : e.target.value) }, [
								p.multiple ? null : h("option", { value: "" }, ""),
								...fields.map((f) => h("option", { value: f.field_name }, `${f.label} (${f.data_type})`)),
						  ])
						: input({ placeholder: __("field name") });
					break;
				}
				case "extent":
					control = h("div", { class: "gis-inline" }, [input({ placeholder: "xmin,xmax,ymin,ymax [EPSG:…]" }), h("button", { type: "button", onClick: () => (values[p.name] = props.mapExtent()) }, __("Map View"))]);
					break;
				case "point":
					control = h("div", { class: "gis-inline" }, [input({ placeholder: "x,y [EPSG:…]" }), h("button", { type: "button", onClick: () => (values[p.name] = props.mapCentre()) }, __("Map Centre"))]);
					break;
				case "sink":
				case "vectorDestination":
				case "rasterDestination":
					control = h("div", { class: "gis-dest" }, [
						p.optional ? h("label", { class: "gis-check small" }, [h("input", { type: "checkbox", checked: v.save, onChange: (e) => (v.save = e.target.checked) }), " " + __("Save as a new dataset")]) : null,
						v.save ? h("input", { class: "gis-input mono", value: v.dataset_name, placeholder: __("dataset name"), onInput: (e) => (v.dataset_name = e.target.value) }) : null,
						v.save ? h("input", { class: "gis-input", value: v.title, placeholder: __("title"), onInput: (e) => (v.title = e.target.value) }) : null,
					]);
					break;
				case "fileDestination":
					control = h("div", { class: "gis-dest" }, [
						p.optional ? h("label", { class: "gis-check small" }, [h("input", { type: "checkbox", checked: v.save, onChange: (e) => (v.save = e.target.checked) }), " " + __("Keep this file")]) : null,
						v.save ? h("input", { class: "gis-input mono", value: v.name, onInput: (e) => (v.name = e.target.value) }) : null,
					]);
					break;
				case "string":
				case "expression":
					control = p.multiline ? h("textarea", { class: "gis-input", rows: 3, value: v, onInput: (e) => (values[p.name] = e.target.value) }) : input({});
					break;
				default:
					control = input({});
			}
			return h("label", { class: "gis-param", title: p.help || p.name }, [label, control].flat());
		};
	},
});
</script>
