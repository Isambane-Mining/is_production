<template>
	<div class="cad-modal" @click.self="$emit('close')">
		<div class="cad-dialog">
			<div class="cad-row">
				<h3 class="cad-grow">{{ __("Import into {0}", [model.name]) }}</h3>
				<button class="cad-icon-btn" @click="$emit('close')"><Icon name="close" /></button>
			</div>

			<template v-if="!exchange">
				<p class="muted">{{ __("The file is checked first; nothing is added to the model until you commit. The original file is kept.") }}</p>
				<input type="file" :accept="ACCEPT" @change="file = $event.target.files[0]" />
				<p class="muted small">{{ __("DXF drawings · LandXML, CSV / XYZ points or STL / OBJ surfaces · LAS / LAZ point clouds · STEP solids.") }}</p>
				<div class="cad-row">
					<button class="cad-primary" :disabled="!file || busy" @click="upload">{{ busy ? __("Uploading…") : __("Upload and Check") }}</button>
				</div>
			</template>

			<template v-else-if="exchange.processing_status === 'Inspecting' || exchange.processing_status === 'Importing'">
				<p>{{ exchange.processing_status === "Inspecting" ? __("Checking the file…") : __("Importing…") }} {{ job ? `${job.progress || 0}%` : "" }}</p>
				<p class="muted small">{{ job && job.status_message }}</p>
			</template>

			<template v-else-if="exchange.processing_status === 'Ready to Commit' && report && !is_dxf">
				<h4 class="cad-subhead">{{ __("Validation report") }} · {{ exchange.file_format }}</h4>
				<div class="cad-report">
					<div v-if="report.solids !== undefined"><span>{{ __("Solids") }}</span><b>{{ report.solids }}</b></div>
					<template v-if="exchange.file_format === 'IFC'">
						<div><span>{{ __("Schema / project") }}</span><b>{{ report.schema }} · {{ report.project || "—" }}</b></div>
						<div><span>{{ __("Elements with geometry") }}</span><b>{{ report.elements.toLocaleString() }}</b></div>
						<div><span>{{ __("Georeferencing") }}</span><b>{{ report.georeferencing ? `${report.georeferencing.crs || "?"} · E ${report.georeferencing.eastings} N ${report.georeferencing.northings}` : __("none") }}</b></div>
						<div><span>{{ __("Classes") }}</span><b class="small">{{ Object.entries(report.by_class || {}).slice(0, 8).map(([k, n]) => `${k.replace("Ifc", "")} ${n}`).join(" · ") }}</b></div>
					</template>
					<div v-if="(report.scans || []).length"><span>{{ __("Scans") }}</span><b>{{ report.scans.length }}{{ report.has_rgb ? " · " + __("colour") : "" }}</b></div>
					<div v-if="report.invalid_solids"><span>{{ __("Invalid solids (skipped)") }}</span><b class="is-warning">{{ report.invalid_solids }}</b></div>
					<div v-if="(report.surfaces || []).length"><span>{{ __("Surfaces") }}</span><b>{{ report.surfaces.length }}</b></div>
					<div v-if="(report.meshes || []).length"><span>{{ __("Meshes (not 2.5D)") }}</span><b>{{ report.meshes.length }}</b></div>
					<div v-if="report.points"><span>{{ __("Points") }}</span><b>{{ report.points.toLocaleString() }}</b></div>
					<div v-if="report.points_total && report.points_total !== report.points"><span>{{ __("Points in file") }}</span><b>{{ report.points_total.toLocaleString() }}</b></div>
					<div><span>{{ __("Extents") }}</span><b class="mono">{{ extents }}</b></div>
				</div>
				<table v-if="(report.surfaces || []).length || (report.meshes || []).length" class="cad-table small">
					<tr><th>{{ __("Name") }}</th><th>{{ __("Triangles") }}</th><th>{{ __("Plan area") }}</th><th>{{ __("Elevations") }}</th><th>{{ __("Checks") }}</th></tr>
					<tr v-for="(s, i) in [...(report.surfaces || []), ...(report.meshes || [])]" :key="i">
						<td>{{ s.name }}</td>
						<td class="mono">{{ s.stats.triangle_count.toLocaleString() }}</td>
						<td class="mono">{{ s.stats.plan_area.toFixed(2) }}</td>
						<td class="mono">{{ s.stats.min_elevation.toFixed(2) }} … {{ s.stats.max_elevation.toFixed(2) }}</td>
						<td :class="checks_ok(s) ? 'is-ok' : 'is-warning'">{{ checks_text(s) }}</td>
					</tr>
				</table>
				<ul v-if="(report.warnings || []).length" class="small is-warning cad-warnings">
					<li v-for="(w, i) in report.warnings" :key="i">{{ w }}</li>
				</ul>

				<h4 class="cad-subhead">{{ __("Options") }}</h4>
				<div class="cad-form-grid">
					<label>{{ __("File units") }}</label>
					<select v-model="options.source_units">
						<option v-for="u in units" :key="u" :value="u">{{ u }}</option>
					</select>
					<label>{{ __("Axis mapping") }}</label>
					<select v-model="options.axis_mapping">
						<option>As in file</option>
						<option>Negate X and Y</option>
						<option>Swap X and Y</option>
					</select>
					<label>{{ __("Layer") }}</label>
					<input v-model="options.layer" :placeholder="exchange.file_format" />
					<template v-if="exchange.file_format === 'IFC'">
						<label>{{ __("One mesh per") }}</label>
						<select v-model="options.ifc_group">
							<option value="element">{{ __("Element (with its IFC GlobalId, class and storey)") }}</option>
							<option value="class">{{ __("IFC class") }}</option>
							<option value="storey">{{ __("Storey") }}</option>
						</select>
						<label>{{ __("Georeferencing") }}</label>
						<label class="cad-row"><input v-model="options.ifc_georeference" type="checkbox" /> {{ __("Place it with the file's IfcMapConversion") }}</label>
					</template>
					<template v-if="!['STEP', 'IFC', 'E57'].includes(exchange.file_format)">
						<label>{{ __("Surface type") }}</label>
						<select v-model="options.surface_type">
							<option v-for="t in surface_types" :key="t">{{ t }}</option>
						</select>
					</template>
					<template v-if="report.points && exchange.file_format !== 'E57'">
						<label>{{ __("Points become") }}</label>
						<select v-model="options.points_as">
							<option value="">{{ __("Automatic (surface when large)") }}</option>
							<option v-if="['LAS', 'LAZ', 'XYZ', 'CSV'].includes(exchange.file_format)" value="cloud">{{ __("A point cloud (every point, editable)") }}</option>
							<option value="surface">{{ __("A surface (TIN)") }}</option>
							<option value="points">{{ __("Point objects") }}</option>
							<option value="both">{{ __("Both") }}</option>
						</select>
						<label>{{ __("Longest triangle edge") }}</label>
						<input v-model.number="options.max_edge" type="number" min="0" step="any" :placeholder="__('no limit')" />
					</template>
					<template v-if="['CSV', 'XYZ'].includes(exchange.file_format)">
						<label>{{ __("Columns") }}</label>
						<input v-model="options.columns" :placeholder="__('automatic, e.g. id,x,y,z or y,x,z')" />
					</template>
					<template v-if="['LAS', 'LAZ'].includes(exchange.file_format)">
						<label>{{ __("Classes") }}</label>
						<label class="cad-row"><input v-model="options.ground_only" type="checkbox" /> {{ __("Ground points only (class 2)") }}</label>
						<label>{{ __("Thin to grid") }}</label>
						<input v-model.number="options.thin" type="number" min="0" step="any" :placeholder="__('keep all points')" />
					</template>
					<template v-if="['STL', 'OBJ'].includes(exchange.file_format)">
						<label>{{ __("Triangles") }}</label>
						<label class="cad-row"><input v-model="options.as_mesh" type="checkbox" /> {{ __("Keep as a mesh (not a 2.5D surface)") }}</label>
					</template>
				</div>
				<p v-if="exchange.file_format === 'IFC'" class="muted small">{{ __("IFC carries its own units (read in metres; File units scale them further) and, when ticked, its georeferencing (IfcMapConversion): the result should be in the model's CRS ({0}). Everything goes into the Working revision.", [model.crs || __("none")]) }}</p>
				<p v-else class="muted small">{{ __("These formats carry no units or CRS: coordinates are taken in the model's CRS ({0}). Everything goes into the Working revision.", [model.crs || __("none")]) }}</p>
				<div class="cad-row">
					<span class="cad-grow"></span>
					<button @click="$emit('close')">{{ __("Cancel") }}</button>
					<button class="cad-primary" :disabled="!options.source_units || busy" @click="commit">{{ __("Import") }}</button>
				</div>
			</template>

			<template v-else-if="exchange.processing_status === 'Ready to Commit' && report">
				<h4 class="cad-subhead">{{ __("Validation report") }}</h4>
				<div class="cad-report">
					<div><span>{{ __("DXF") }}</span><b>{{ exchange.format_version }}</b></div>
					<div><span>{{ __("Units in file") }}</span><b>{{ report.units_name }}</b></div>
					<div><span>{{ __("Entities to import") }}</span><b>{{ report.entity_count }}</b></div>
					<div><span>{{ __("Layers / blocks") }}</span><b>{{ report.layers.length }} / {{ report.blocks }}</b></div>
					<div><span>{{ __("Duplicates") }}</span><b>{{ report.duplicates }}</b></div>
					<div><span>{{ __("Invalid (skipped)") }}</span><b :class="{ 'is-warning': report.invalid_count }">{{ report.invalid_count }}</b></div>
					<div><span>{{ __("Extents") }}</span><b class="mono">{{ extents }}</b></div>
					<div><span>{{ __("Elevation range") }}</span><b class="mono">{{ elevations }}</b></div>
					<div v-if="report.audit.errors"><span>{{ __("Repaired file errors") }}</span><b>{{ report.audit.errors }}</b></div>
				</div>
				<div class="small"><b>{{ __("By type") }}:</b> {{ list(report.converted) }}</div>
				<div v-if="Object.keys(report.unsupported).length" class="small is-warning"><b>{{ __("Not imported") }}:</b> {{ list(report.unsupported) }}</div>
				<div v-if="Object.keys(report.exploded).length" class="small"><b>{{ __("Exploded") }}:</b> {{ list(report.exploded) }}</div>
				<ul v-if="report.warnings.length" class="small is-warning cad-warnings">
					<li v-for="(w, i) in report.warnings" :key="i">{{ w }}</li>
				</ul>
				<ul v-if="report.invalid.length" class="small cad-warnings">
					<li v-for="(w, i) in report.invalid.slice(0, 20)" :key="i">{{ w.type }} {{ w.handle }}: {{ w.message }}</li>
				</ul>

				<h4 class="cad-subhead">{{ __("Options") }}</h4>
				<div class="cad-form-grid">
					<label>{{ __("Drawing units") }}</label>
					<select v-model="options.source_units">
						<option v-for="u in units" :key="u" :value="u">{{ u }}</option>
					</select>
					<label>{{ __("Source CRS") }}</label>
					<input v-model="options.source_crs" :placeholder="model.crs ? __('{0} (same as the model)', [model.crs]) : __('none')" />
					<label>{{ __("Axis mapping") }}</label>
					<select v-model="options.axis_mapping">
						<option>As in file</option>
						<option>Negate X and Y</option>
						<option>Swap X and Y</option>
					</select>
					<label>{{ __("Layers") }}</label>
					<select v-model="options.layers">
						<option value="all">{{ __("All layers in the file") }}</option>
						<option value="used">{{ __("Only layers with entities") }}</option>
					</select>
					<label>{{ __("Existing block names") }}</label>
					<select v-model="options.block_conflict">
						<option value="rename">{{ __("Rename the imported block") }}</option>
						<option value="keep">{{ __("Keep the model's definition") }}</option>
					</select>
					<label>{{ __("Duplicates") }}</label>
					<label class="cad-row"><input v-model="options.skip_duplicates" type="checkbox" /> {{ __("Skip exact duplicates") }}</label>
				</div>
				<p class="muted small">{{ __("Imported entities go into the Working revision. Approved revisions are never changed. A different source CRS reprojects (curves are then densified).") }}</p>
				<div class="cad-row">
					<span class="cad-grow"></span>
					<button @click="$emit('close')">{{ __("Cancel") }}</button>
					<button class="cad-primary" :disabled="!options.source_units || busy" @click="commit">{{ __("Import {0} Entities", [report.entity_count]) }}</button>
				</div>
			</template>

			<template v-else>
				<p :class="exchange.processing_status === 'Completed' ? 'is-ok' : 'is-error'">
					<b>{{ __(exchange.processing_status) }}</b>
					<span v-if="exchange.processing_status === 'Completed'"> — {{ __("{0} entities imported into {1}.", [exchange.entity_count, exchange.revision]) }}</span>
				</p>
				<pre v-if="exchange.warnings" class="small cad-warnings-pre">{{ exchange.warnings }}</pre>
				<p v-if="job && job.status === 'Failed'" class="is-error small">{{ job.status_message }}</p>
				<div class="cad-row">
					<span class="cad-grow"></span>
					<button class="cad-primary" @click="$emit('close', exchange.processing_status === 'Completed')">{{ __("Close") }}</button>
				</div>
			</template>
		</div>
	</div>
</template>

<script setup>
import { computed, reactive, ref } from "vue";
import { call, wait_for_job } from "./api";
import { format_point } from "./geometry";
import { tin_problems } from "./surface_commands";
import Icon from "./Icon.vue";

const props = defineProps({ model: Object, surface_types: Array });
const ACCEPT = ".dxf,.xml,.landxml,.csv,.xyz,.txt,.pts,.stl,.obj,.las,.laz,.e57,.step,.stp,.ifc";
const emit = defineEmits(["close", "error"]);
const file = ref(null);
const busy = ref(false);
const exchange = ref(null);
const job = ref(null);
const units = ["Metres", "Millimetres", "Centimetres", "Kilometres", "Feet", "US Survey Feet", "Inches", "Yards"];
const options = reactive({
	source_units: "",
	source_crs: "",
	axis_mapping: "As in file",
	layers: "all",
	block_conflict: "rename",
	skip_duplicates: true,
	layer: "",
	surface_type: "Survey Surface",
	points_as: "",
	max_edge: null,
	columns: "",
	ground_only: false,
	thin: null,
	as_mesh: false,
	ifc_group: "element",
	ifc_georeference: true,
});
const is_dxf = computed(() => !exchange.value || exchange.value.file_format === "DXF");

const report = computed(() => exchange.value && exchange.value.report);
const extents = computed(() => (report.value && report.value.extents ? `${format_point(report.value.extents.slice(0, 3), 1)} → ${format_point(report.value.extents.slice(3), 1)}` : "—"));
const elevations = computed(() => (report.value && report.value.elevation_range ? report.value.elevation_range.map((v) => v.toFixed(2)).join(" … ") : "—"));

function checks_ok(s) {
	const v = s.stats.validation || {};
	return v.ok !== false && s.stats.closed !== false;
}

function checks_text(s) {
	const v = s.stats.validation || {};
	if (s.stats.closed !== undefined) return s.stats.closed ? __("Closed") : __("Open ({0} edges)", [s.stats.open_edges]);
	return v.ok === false ? v.message || tin_problems(v) : v.spike_count ? __("{0} possible spike(s)", [v.spike_count]) : __("OK");
}

function list(counts) {
	return Object.entries(counts || {})
		.map(([k, v]) => `${k} ${v}`)
		.join(" · ");
}

async function follow(name) {
	job.value = { progress: 0 };
	const io = await call("get_exchange", { name });
	exchange.value = io;
	if (io.processing_job) job.value = await wait_for_job(io.processing_job, (j) => (job.value = j));
	exchange.value = await call("get_exchange", { name });
	if (exchange.value.processing_status === "Ready to Commit") options.source_units = exchange.value.source_units || (is_dxf.value ? "" : props.model.linear_units || "Metres");
	if (["LAS", "LAZ"].includes(exchange.value.file_format) && !options.points_as) options.points_as = "cloud"; // scans stay point clouds
}

async function upload() {
	busy.value = true;
	try {
		const form = new FormData();
		form.append("file", file.value, file.value.name);
		form.append("is_private", "1");
		form.append("doctype", "CAD Model");
		form.append("docname", props.model.name);
		const response = await fetch("/api/method/upload_file", { method: "POST", body: form, headers: { "X-Frappe-CSRF-Token": window.FRAPPE_CAD.csrfToken, Accept: "application/json" } });
		const data = await response.json();
		if (!response.ok || data.exc_type) throw new Error(__("Upload failed: {0}", [data.exception || response.statusText]));
		const io = await call("start_import", { model: props.model.name, file_url: data.message.file_url });
		await follow(io.name);
	} catch (e) {
		emit("error", e.message);
	} finally {
		busy.value = false;
	}
}

async function commit() {
	busy.value = true;
	try {
		const io = await call("commit_import", { name: exchange.value.name, options: { ...options } });
		await follow(io.name);
	} catch (e) {
		emit("error", e.message);
	} finally {
		busy.value = false;
	}
}
</script>
