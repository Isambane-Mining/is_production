<template>
	<div class="cad-modal" @click.self="$emit('close')">
		<div class="cad-dialog">
			<div class="cad-row">
				<h3 class="cad-grow">{{ __("Export {0} as DXF", [model.name]) }}</h3>
				<button class="cad-icon-btn" @click="$emit('close')"><Icon name="close" /></button>
			</div>
			<template v-if="!exchange">
				<div class="cad-form-grid">
					<label>{{ __("What") }}</label>
					<select v-model="options.revision">
						<option value="">{{ __("Current working state (as saved)") }}</option>
						<option v-for="r in frozen" :key="r.name" :value="r.name">{{ r.revision_label }} · {{ __(r.revision_status) }}</option>
					</select>
					<label>{{ __("DXF version") }}</label>
					<select v-model="options.version">
						<option v-for="v in ['R2018', 'R2013', 'R2010', 'R2007', 'R2004', 'R2000', 'R12']" :key="v">{{ v }}</option>
					</select>
					<label>{{ __("Axis mapping") }}</label>
					<select v-model="options.axis_mapping">
						<option>As in file</option>
						<option>Negate X and Y</option>
						<option>Swap X and Y</option>
					</select>
					<label>{{ __("Layers") }}</label>
					<label class="cad-row"><input v-model="options.visible_layers_only" type="checkbox" /> {{ __("Visible layers only") }}</label>
					<label>{{ __("Solids") }}</label>
					<label class="cad-row"><input v-model="options.include_solids" type="checkbox" /> {{ __("As MESH approximations") }}</label>
				</div>
				<p class="muted small">{{ __("Coordinates and units are the model's ({0}, {1}). Unsaved changes are not exported.", [model.crs || __("no CRS"), model.linear_units]) }}</p>
				<div class="cad-row">
					<span class="cad-grow"></span>
					<button class="cad-primary" :disabled="busy" @click="start">{{ __("Export") }}</button>
				</div>
			</template>
			<template v-else>
				<p v-if="exchange.processing_status === 'Exporting'">{{ __("Writing the DXF…") }} {{ job ? `${job.progress || 0}%` : "" }}</p>
				<template v-else-if="exchange.processing_status === 'Completed'">
					<p class="is-ok">{{ __("{0} entities written.", [exchange.entity_count]) }}</p>
					<pre v-if="exchange.warnings" class="small cad-warnings-pre">{{ exchange.warnings }}</pre>
					<a class="cad-primary-link" :href="exchange.result_file" download>{{ __("Download DXF") }}</a>
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

const props = defineProps({ model: Object, revisions: Array });
const emit = defineEmits(["close", "error"]);
const options = reactive({ revision: "", version: "R2018", axis_mapping: "As in file", visible_layers_only: false, include_solids: true });
const exchange = ref(null);
const job = ref(null);
const busy = ref(false);
const frozen = computed(() => (props.revisions || []).filter((r) => r.geometry_checksum));

async function start() {
	busy.value = true;
	try {
		const io = await call("start_export", { model: props.model.name, options: { ...options } });
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
