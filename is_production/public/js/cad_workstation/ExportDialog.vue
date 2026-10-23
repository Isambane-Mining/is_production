<template>
	<div class="cad-modal" @click.self="$emit('close')">
		<div class="cad-dialog">
			<div class="cad-row">
				<h3 class="cad-grow">{{ __("Export {0}", [model.name]) }}</h3>
				<button class="cad-icon-btn" @click="$emit('close')"><Icon name="close" /></button>
			</div>
			<template v-if="!exchange">
				<div class="cad-form-grid">
					<label>{{ __("What") }}</label>
					<select v-model="options.revision">
						<option value="">{{ __("Current working state (as saved)") }}</option>
						<option v-for="r in frozen" :key="r.name" :value="r.name">{{ r.revision_label }} · {{ __(r.revision_status) }}</option>
					</select>
					<label>{{ __("Format") }}</label>
					<select v-model="options.format">
						<option v-for="f in FORMATS" :key="f.value" :value="f.value">{{ f.label }}</option>
					</select>
					<template v-if="options.format === 'DXF'">
						<label>{{ __("DXF version") }}</label>
						<select v-model="options.version">
							<option v-for="v in ['R2018', 'R2013', 'R2010', 'R2007', 'R2004', 'R2000', 'R12']" :key="v">{{ v }}</option>
						</select>
					</template>
					<template v-else-if="!['GeoJSON', 'Blocks CSV', 'Drill Holes CSV'].includes(options.format)">
						<label>{{ options.format === "STEP" ? __("Solids") : __("Surfaces") }}</label>
						<label class="cad-row">
							<input v-model="selected_only" type="checkbox" :disabled="!chosen.length" />
							{{ chosen.length ? __("Only the {0} selected", [chosen.length]) : __("All (select some to export only those)") }}
						</label>
					</template>
					<label>{{ __("Axis mapping") }}</label>
					<select v-model="options.axis_mapping">
						<option>As in file</option>
						<option>Negate X and Y</option>
						<option>Swap X and Y</option>
					</select>
					<template v-if="options.format === 'DXF'">
						<label>{{ __("Layers") }}</label>
						<label class="cad-row"><input v-model="options.visible_layers_only" type="checkbox" /> {{ __("Visible layers only") }}</label>
						<label>{{ __("Solids") }}</label>
						<label class="cad-row"><input v-model="options.include_solids" type="checkbox" /> {{ __("As MESH approximations") }}</label>
					</template>
				</div>
				<p class="muted small">{{ __("Coordinates and units are the model's ({0}, {1}). Unsaved changes are not exported.", [model.crs || __("no CRS"), model.linear_units]) }}</p>
				<div class="cad-row">
					<span class="cad-grow"></span>
					<button class="cad-primary" :disabled="busy" @click="start">{{ __("Export") }}</button>
				</div>
			</template>
			<template v-else>
				<p v-if="exchange.processing_status === 'Exporting'">{{ __("Writing the file…") }} {{ job ? `${job.progress || 0}%` : "" }}</p>
				<template v-else-if="exchange.processing_status === 'Completed'">
					<p class="is-ok">{{ __("{0} entities written.", [exchange.entity_count]) }}</p>
					<pre v-if="exchange.warnings" class="small cad-warnings-pre">{{ exchange.warnings }}</pre>
					<a class="cad-primary-link" :href="exchange.result_file" download>{{ __("Download {0}", [exchange.file_format || options.format]) }}</a>
				</template>
				<p v-else class="is-error">{{ __(exchange.processing_status) }} — {{ job && job.status_message }}</p>
			</template>
		</div>
	</div>
</template>

<script setup>
import { computed, reactive, ref } from "vue";
import { call, wait_for_job } from "./api";
import Icon from "./Icon.vue";

const props = defineProps({ model: Object, revisions: Array, selected: { type: Array, default: () => [] } });
const FORMATS = [
	{ value: "DXF", label: __("DXF drawing") },
	{ value: "LandXML", label: __("LandXML surfaces") },
	{ value: "STL", label: __("STL triangles") },
	{ value: "OBJ", label: __("OBJ triangles") },
	{ value: "CSV", label: __("CSV surface points") },
	{ value: "XYZ", label: __("XYZ surface points") },
	{ value: "STEP", label: __("STEP solids (AP214)") },
	{ value: "GeoJSON", label: __("GeoJSON mining design (pits, roads, blocks)") },
	{ value: "Blocks CSV", label: __("Mining blocks table (CSV)") },
	{ value: "Drill Holes CSV", label: __("Drill holes: collars and toes (CSV)") },
];
const selected_only = ref(true);
const emit = defineEmits(["close", "error"]);
const options = reactive({ format: "DXF", revision: "", version: "R2018", axis_mapping: "As in file", visible_layers_only: false, include_solids: true });
const exchange = ref(null);
const job = ref(null);
const busy = ref(false);
// geometry refs of the selected surfaces / meshes (or solids, for STEP)
const chosen = computed(() =>
	props.selected.filter((e) => e.geometry_ref && (options.format === "STEP" ? e.entity_type === "Solid" : ["Surface", "Mesh"].includes(e.entity_type))).map((e) => e.geometry_ref)
);
const frozen = computed(() => (props.revisions || []).filter((r) => r.geometry_checksum));

async function start() {
	busy.value = true;
	try {
		const geometry_refs = !["DXF", "GeoJSON", "Blocks CSV", "Drill Holes CSV"].includes(options.format) && selected_only.value ? chosen.value : [];
		const io = await call("start_export", { model: props.model.name, options: { ...options, geometry_refs } });
		exchange.value = io;
		if (io.processing_job) job.value = await wait_for_job(io.processing_job, (j) => (job.value = j));
		exchange.value = await call("get_exchange", { name: io.name });
	} catch (e) {
		emit("error", e.message);
	} finally {
		busy.value = false;
	}
}
</script>
