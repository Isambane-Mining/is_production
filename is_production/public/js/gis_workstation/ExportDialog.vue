<template>
	<div class="gis-modal-backdrop" @click.self="close">
		<div class="gis-modal">
			<h3>{{ project_mode ? __("Export Project to QGIS") : __("Export") }}</h3>
			<template v-if="!job">
				<form class="gis-form" @submit.prevent="start">
					<label v-if="!project_mode">{{ __("What") }}
						<select v-model="scope" class="gis-input">
							<option v-if="activeLayer" value="layer">{{ __("Active layer: {0}", [activeTitle]) }}</option>
							<option v-if="activeLayer && selectedIds.length" value="selection">{{ __("Selected features of {0} ({1})", [activeTitle, selectedIds.length]) }}</option>
							<option v-if="layerNames.length > 1" value="all">{{ __("All {0} layers in the map", [layerNames.length]) }}</option>
						</select>
					</label>
					<p v-else class="small">
						{{ __("A zip with a GeoPackage of the project's datasets and a QGIS project (.qgz): layer order, visibility, opacity, colour, CRS and extent. Opens directly in QGIS Desktop.") }}
					</p>
					<label v-if="!project_mode">{{ __("Format") }}
						<select v-model="format" class="gis-input">
							<option v-for="f in formats" :key="f.value" :value="f.value">{{ f.label }}</option>
						</select>
					</label>
					<label>{{ __("Coordinates") }}
						<select v-model="crs_choice" class="gis-input" :disabled="format === 'KML'">
							<option value="">{{ __("Each dataset's own CRS (no reprojection)") }}</option>
							<option v-if="displayCrs && displayCrs !== 'EPSG:4326'" :value="displayCrs">{{ displayCrs }}</option>
							<option value="EPSG:4326">EPSG:4326 · WGS 84</option>
							<option value="other">{{ __("Other…") }}</option>
						</select>
					</label>
					<input v-if="crs_choice === 'other'" v-model="other_crs" class="gis-input mono" placeholder="EPSG:32735" />
					<p v-if="format === 'KML'" class="muted small">{{ __("KML is always WGS 84 (EPSG:4326).") }}</p>
					<p v-if="format === 'Shapefile'" class="muted small">{{ __("Shapefile field names are cut to 10 characters; the job log lists the new names.") }}</p>
					<p v-if="error" class="is-error small">{{ error }}</p>
					<div class="gis-modal-actions">
						<button type="button" @click="close">{{ __("Cancel") }}</button>
						<button class="gis-primary" type="submit" :disabled="busy">{{ __("Export") }}</button>
					</div>
				</form>
			</template>
			<template v-else>
				<p><b>{{ __(job.status) }}</b> · {{ job.status_message }}</p>
				<progress :value="job.progress || 0" max="100" class="gis-progress"></progress>
				<pre v-if="finished && job.log" class="gis-log">{{ job.log }}</pre>
				<div class="gis-modal-actions">
					<a class="gis-button" :href="`/desk/gis-import-export-job/${job.name}`" target="_blank">{{ __("Job in Desk") }}</a>
					<a v-if="job.output_file" class="gis-button gis-primary" :href="job.output_file" download>⭳ {{ __("Download") }}</a>
					<button :disabled="!finished" @click="close">{{ __("Close") }}</button>
				</div>
			</template>
		</div>
	</div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref } from "vue";
import { call } from "./api.js";

const __ = window.__;
const IO = "is_production.geo_planning.services.gis_io_service.";
const props = defineProps({
	activeLayer: String,
	activeTitle: String,
	layerNames: { type: Array, default: () => [] },
	selectedIds: { type: Array, default: () => [] },
	project: String, // set: export this saved GIS Project as a QGIS package
	displayCrs: String,
});
const emit = defineEmits(["close"]);

const formats = [
	{ value: "GeoPackage", label: "GeoPackage (.gpkg) — best for QGIS" },
	{ value: "GeoJSON", label: "GeoJSON" },
	{ value: "Shapefile", label: "ESRI Shapefile (.zip)" },
	{ value: "KML", label: "KML (Google Earth)" },
	{ value: "GML", label: "GML" },
	{ value: "CSV", label: "CSV (geometry as WKT)" },
];
const project_mode = computed(() => !!props.project);
const scope = ref(props.selectedIds.length ? "selection" : props.activeLayer ? "layer" : "all");
const format = ref(project_mode.value ? "QGIS Project" : "GeoPackage");
const crs_choice = ref("");
const other_crs = ref("");
const job = ref(null);
const busy = ref(false);
const error = ref("");
let timer = null;

const finished = computed(() => job.value && !["Queued", "Running"].includes(job.value.status));
onBeforeUnmount(() => clearTimeout(timer));

function close() {
	clearTimeout(timer);
	emit("close");
}

async function start() {
	busy.value = true;
	error.value = "";
	const args = {
		export_format: format.value,
		target_crs: crs_choice.value === "other" ? other_crs.value : crs_choice.value || null,
	};
	if (project_mode.value) args.project = props.project;
	else if (scope.value === "all") args.datasets = props.layerNames;
	else args.datasets = [props.activeLayer];
	if (!project_mode.value && scope.value === "selection") args.feature_ids = props.selectedIds.filter((id) => id > 0);
	try {
		job.value = await call(IO + "start_export", args);
		poll();
	} catch (e) {
		error.value = e.message;
	} finally {
		busy.value = false;
	}
}

async function poll() {
	try {
		job.value = await call(IO + "get_job", { name: job.value.name });
	} catch (e) {
		/* keep polling */
	}
	if (!finished.value) timer = setTimeout(poll, 1500);
}
</script>
