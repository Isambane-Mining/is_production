<template>
	<div class="cad-modal" @click.self="$emit('close')">
		<div class="cad-dialog">
			<div class="cad-row">
				<h3 class="cad-grow">{{ __("Import DXF into {0}", [model.name]) }}</h3>
				<button class="cad-icon-btn" @click="$emit('close')"><Icon name="close" /></button>
			</div>

			<template v-if="!exchange">
				<p class="muted">{{ __("The file is checked first; nothing is added to the model until you commit. The original file is kept.") }}</p>
				<input type="file" accept=".dxf,.DXF" @change="file = $event.target.files[0]" />
				<div class="cad-row">
					<button class="cad-primary" :disabled="!file || busy" @click="upload">{{ busy ? __("Uploading…") : __("Upload and Check") }}</button>
				</div>
			</template>

			<template v-else-if="exchange.processing_status === 'Inspecting' || exchange.processing_status === 'Importing'">
				<p>{{ exchange.processing_status === "Inspecting" ? __("Checking the file…") : __("Importing…") }} {{ job ? `${job.progress || 0}%` : "" }}</p>
				<p class="muted small">{{ job && job.status_message }}</p>
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
import Icon from "./Icon.vue";

const props = defineProps({ model: Object });
const emit = defineEmits(["close", "error"]);
const file = ref(null);
const busy = ref(false);
const exchange = ref(null);
const job = ref(null);
const units = ["Metres", "Millimetres", "Centimetres", "Kilometres", "Feet", "US Survey Feet", "Inches", "Yards"];
const options = reactive({ source_units: "", source_crs: "", axis_mapping: "As in file", layers: "all", block_conflict: "rename", skip_duplicates: true });

const report = computed(() => exchange.value && exchange.value.report);
const extents = computed(() => (report.value && report.value.extents ? `${format_point(report.value.extents.slice(0, 3), 1)} → ${format_point(report.value.extents.slice(3), 1)}` : "—"));
const elevations = computed(() => (report.value && report.value.elevation_range ? report.value.elevation_range.map((v) => v.toFixed(2)).join(" … ") : "—"));

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
	if (exchange.value.processing_status === "Ready to Commit") options.source_units = exchange.value.source_units || "";
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
