<template>
	<div class="gis-modal-backdrop" @click.self="close">
		<div class="gis-modal gis-modal-wide">
			<h3>{{ project ? __("Import QGIS Project Package") : __("Import Dataset") }}</h3>

			<!-- 1. file -->
			<template v-if="step === 'file'">
				<p class="muted small">
					{{ project
						? __("A .zip with a QGIS project (.qgz or .qgs) and its data (GeoPackage, Shapefile, GeoJSON …), e.g. one exported from here.")
						: __("GeoPackage, GeoJSON, KML/KMZ, GML, GPX, CSV with coordinates, DXF, or a .zip of Shapefiles (with .shx, .dbf, .prj) or a File Geodatabase.") }}
				</p>
				<input type="file" :accept="project ? '.zip' : accept" @change="file = $event.target.files[0]" />
				<p v-if="file" class="small">{{ file.name }} · {{ size(file.size) }}</p>
				<div class="gis-modal-actions">
					<button @click="close">{{ __("Cancel") }}</button>
					<button class="gis-primary" :disabled="!file || busy" @click="upload">{{ busy ? __("Reading…") : __("Inspect File") }}</button>
				</div>
			</template>

			<!-- 2. mapping -->
			<template v-else-if="step === 'map'">
				<p class="small muted">{{ job.name }} · {{ __("{0} layer(s) found. Choose what each becomes.", [job.layers.length]) }}</p>
				<pre v-if="job.log" class="gis-log">{{ job.log }}</pre>
				<div class="gis-import-layers">
					<section v-for="l in job.layers" :key="l.idx" class="gis-import-layer" :class="{ skipped: l.action === 'Skip' }">
						<header>
							<b>{{ l.source_layer }}</b>
							<span class="muted small">{{ l.source_geometry_type }} · {{ __("{0} features", [l.feature_count]) }}{{ l.has_z ? " · Z" : "" }}</span>
						</header>
						<div class="gis-grid">
							<label>{{ __("Action") }}
								<select v-model="l.action" class="gis-input">
									<option value="Create Dataset">{{ __("Create a new dataset") }}</option>
									<option value="Append">{{ __("Append to a dataset") }}</option>
									<option value="Skip">{{ __("Skip") }}</option>
								</select>
							</label>
							<template v-if="l.action === 'Create Dataset'">
								<label>{{ __("Dataset name") }}<input v-model="l.new_dataset_name" class="gis-input mono" /></label>
								<label>{{ __("Title") }}<input v-model="l.new_title" class="gis-input" /></label>
								<label>{{ __("Geometry type") }}
									<select v-model="l.new_geometry_type" class="gis-input">
										<option v-for="t in geometry_types" :key="t">{{ t }}</option>
									</select>
								</label>
								<label>{{ __("Dataset CRS") }}<input v-model="l.dataset_crs" class="gis-input mono" placeholder="EPSG:2053" /></label>
							</template>
							<label v-else-if="l.action === 'Append'">{{ __("Dataset") }}
								<select v-model="l.dataset" class="gis-input" @change="l.dataset_crs = crs_of(l.dataset)">
									<option value=""></option>
									<option v-for="d in datasets" :key="d.name" :value="d.name">{{ d.title }} ({{ d.crs }})</option>
								</select>
							</label>
							<template v-if="l.action !== 'Skip'">
								<label>{{ __("Source CRS") }}
									<input v-model="l.source_crs" class="gis-input mono" :placeholder="__('none in file: taken as the dataset CRS')" />
								</label>
								<label v-if="needs_reproject(l)" class="gis-check gis-reproject">
									<input v-model="l.reproject" type="checkbox" :true-value="1" :false-value="0" />
									{{ __("Reproject {0} → {1}", [l.source_crs, l.dataset_crs]) }}
								</label>
							</template>
							<template v-if="is_csv && l.action !== 'Skip'">
								<label>{{ __("X column") }}<input v-model="l.csv_x" class="gis-input mono" :placeholder="__('e.g. Y for Lo westing')" /></label>
								<label>{{ __("Y column") }}<input v-model="l.csv_y" class="gis-input mono" :placeholder="__('e.g. X for Lo southing')" /></label>
								<label>{{ __("Z column") }}<input v-model="l.csv_z" class="gis-input mono" placeholder="Z" /></label>
							</template>
						</div>
						<p v-if="is_csv && l.action !== 'Skip'" class="muted small">
							{{ __("South African Lo coordinates: the Y column (westing) is the x, the X column (southing) is the y.") }}
						</p>
						<details v-if="l.action !== 'Skip' && l.field_mapping.length">
							<summary class="small">{{ __("Fields ({0} of {1} imported)", [l.field_mapping.filter((m) => m.include).length, l.field_mapping.length]) }}</summary>
							<table class="gis-table gis-field-map">
								<thead><tr><th></th><th>{{ __("Source field") }}</th><th>{{ __("Attribute") }}</th><th>{{ __("Type") }}</th></tr></thead>
								<tbody>
									<tr v-for="m in l.field_mapping" :key="m.source">
										<td><input v-model="m.include" type="checkbox" /></td>
										<td class="mono">{{ m.source }}</td>
										<td><input v-model="m.field_name" class="gis-cell-input mono" :disabled="!m.include || l.action === 'Append'" /></td>
										<td>
											<select v-model="m.data_type" :disabled="!m.include || l.action === 'Append'">
												<option v-for="t in data_types" :key="t">{{ t }}</option>
											</select>
										</td>
									</tr>
								</tbody>
							</table>
							<p v-if="l.action === 'Append'" class="muted small">{{ __("Appending: fields go to the dataset's attribute of the same name; others are not imported.") }}</p>
						</details>
					</section>
				</div>
				<p v-if="error" class="is-error small">{{ error }}</p>
				<div class="gis-modal-actions">
					<button @click="close">{{ __("Cancel") }}</button>
					<button class="gis-primary" :disabled="busy || !job.layers.some((l) => l.action !== 'Skip')" @click="run">{{ __("Import") }}</button>
				</div>
			</template>

			<!-- 3. running / done -->
			<template v-else>
				<p><b>{{ __(job.status) }}</b> · {{ job.status_message }}</p>
				<progress :value="job.progress || 0" max="100" class="gis-progress"></progress>
				<table v-if="finished" class="gis-table">
					<thead><tr><th>{{ __("Layer") }}</th><th>{{ __("Dataset") }}</th><th>{{ __("Imported") }}</th><th>{{ __("Failed") }}</th><th>{{ __("Status") }}</th></tr></thead>
					<tbody>
						<tr v-for="l in job.layers.filter((x) => x.action !== 'Skip')" :key="l.idx">
							<td>{{ l.source_layer }}</td><td class="mono">{{ l.dataset }}</td><td>{{ l.imported }}</td><td>{{ l.failed }}</td><td>{{ l.layer_status }}</td>
						</tr>
					</tbody>
				</table>
				<pre v-if="finished && job.log" class="gis-log">{{ job.log }}</pre>
				<div class="gis-modal-actions">
					<a class="gis-button" :href="`/desk/gis-import-export-job/${job.name}`" target="_blank">{{ __("Job in Desk") }}</a>
					<button v-if="finished && job.gis_project" class="gis-primary" @click="$emit('open-project', job.gis_project); close()">{{ __("Open the Project") }}</button>
					<button v-else-if="finished && imported_datasets.length" class="gis-primary" @click="$emit('add-datasets', imported_datasets); close()">{{ __("Add to Map") }}</button>
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
const props = defineProps({ datasets: Array, project: Boolean });
const emit = defineEmits(["close", "add-datasets", "open-project", "error"]);

const accept = ".gpkg,.geojson,.json,.zip,.kml,.kmz,.gml,.gpx,.csv,.dxf";
const geometry_types = ["Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon", "Geometry", "No Geometry"];
const data_types = ["Text", "Integer", "Float", "Boolean", "Date", "Datetime"];
const step = ref("file");
const file = ref(null);
const job = ref(null);
const busy = ref(false);
const error = ref("");
let timer = null;

const is_csv = computed(() => job.value && /\.csv$/i.test(file.value ? file.value.name : ""));
const finished = computed(() => job.value && !["Queued", "Running", "Draft"].includes(job.value.status));
const imported_datasets = computed(() => (job.value ? [...new Set(job.value.layers.filter((l) => l.dataset && l.imported).map((l) => l.dataset))] : []));

onBeforeUnmount(() => clearTimeout(timer));

function close() {
	clearTimeout(timer);
	emit("close");
}

function size(bytes) {
	return bytes > 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.ceil(bytes / 1024)} KB`;
}

function crs_of(name) {
	const d = (props.datasets || []).find((x) => x.name === name);
	return d ? d.crs : "";
}

function needs_reproject(l) {
	return l.source_crs && l.dataset_crs && l.source_crs.trim().toUpperCase() !== l.dataset_crs.trim().toUpperCase();
}

async function upload() {
	busy.value = true;
	error.value = "";
	try {
		const form = new FormData();
		form.append("file", file.value, file.value.name);
		form.append("is_private", "1");
		form.append("folder", "Home");
		const response = await fetch("/api/method/upload_file", {
			method: "POST",
			headers: { "X-Frappe-CSRF-Token": (window.FRAPPE_GIS || {}).csrfToken || "", Accept: "application/json" },
			body: form,
		});
		const data = await response.json().catch(() => ({}));
		if (!response.ok || !data.message) throw new Error(upload_error(data, response));
		job.value = await call(IO + "start_import", { file_url: data.message.file_url });
		if (job.value.status === "Failed") throw new Error(job.value.log || __("The file could not be read."));
		if (props.project && !job.value.is_project) throw new Error(__("This zip has no QGIS project (.qgz or .qgs) in it. Use Import Dataset instead."));
		step.value = "map";
	} catch (e) {
		emit("error", e.message);
	} finally {
		busy.value = false;
	}
}

function upload_error(data, response) {
	try {
		const messages = JSON.parse(data._server_messages || "[]").map((m) => JSON.parse(m).message);
		if (messages.length) return messages.join(" ").replace(/<[^>]+>/g, " ");
	} catch (e) {
		/* fall through */
	}
	return response.status === 413 ? __("The file is larger than this site allows.") : `${response.status} ${response.statusText}`;
}

async function run() {
	busy.value = true;
	error.value = "";
	try {
		job.value = await call(IO + "run_import", { job: job.value.name, layers: job.value.layers });
		step.value = "run";
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
