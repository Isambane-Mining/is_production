<template>
	<div class="cad-panel-body">
		<div class="cad-field">
			<label>{{ __("Geo Project") }}</label>
			<select v-model="project" @change="load_models">
				<option value="">{{ __("All projects") }}</option>
				<option v-for="p in projects" :key="p.name" :value="p.name">{{ p.project_name || p.name }}</option>
			</select>
		</div>
		<div class="cad-row">
			<input v-model="filter" class="cad-grow" :placeholder="__('Filter models…')" />
			<button v-if="config.can_create_models" @click="creating = !creating">{{ creating ? __("Cancel") : __("New") }}</button>
		</div>

		<form v-if="creating" class="cad-new-model" @submit.prevent="create">
			<div class="cad-field">
				<label>{{ __("Model Name") }} *</label>
				<input v-model="draft.model_name" required />
			</div>
			<div class="cad-field">
				<label>{{ __("Model Code") }} *</label>
				<input v-model="draft.model_code" required pattern="[A-Za-z0-9][A-Za-z0-9_.\-]{0,59}" :placeholder="__('e.g. SW-PIT-01')" />
			</div>
			<div class="cad-field">
				<label>{{ __("Model Type") }}</label>
				<select v-model="draft.model_type">
					<option v-for="t in config.model_types" :key="t" :value="t">{{ t }}</option>
				</select>
			</div>
			<div class="cad-field">
				<label>{{ __("Geo Project") }}</label>
				<select v-model="draft.geo_project">
					<option value="">{{ __("None") }}</option>
					<option v-for="p in projects" :key="p.name" :value="p.name">{{ p.project_name || p.name }}</option>
				</select>
			</div>
			<div class="cad-row">
				<div class="cad-field cad-grow">
					<label>{{ __("CRS") }}</label>
					<input v-model="draft.crs" :placeholder="config.default_crs" />
				</div>
				<div class="cad-field cad-grow">
					<label>{{ __("Units") }}</label>
					<select v-model="draft.linear_units">
						<option v-for="u in config.linear_units" :key="u" :value="u">{{ u }}</option>
					</select>
				</div>
			</div>
			<button type="submit" class="cad-primary" :disabled="busy">{{ __("Create Model") }}</button>
		</form>

		<div class="cad-list">
			<button
				v-for="m in shown"
				:key="m.name"
				class="cad-list-row"
				:class="{ active: current === m.name }"
				@click="$emit('open', m.name)"
			>
				<span class="cad-grow">
					<b>{{ m.model_name }}</b>
					<span class="muted small"> · {{ m.name }}</span>
					<br />
					<span class="muted small">{{ m.model_type }} · {{ m.entity_count || 0 }} {{ __("entities") }}</span>
				</span>
				<span class="cad-chip" :class="chip(m.approval_status)">{{ __(m.approval_status) }}</span>
			</button>
			<div v-if="!shown.length" class="muted pad">{{ __("No models.") }}</div>
		</div>
	</div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from "vue";
import { call } from "./api";

const props = defineProps({ config: Object, current: String });
const emit = defineEmits(["open", "error"]);
const projects = ref([]);
const models = ref([]);
const project = ref("");
const filter = ref("");
const creating = ref(false);
const busy = ref(false);
const draft = reactive({ model_name: "", model_code: "", model_type: "General CAD", geo_project: "", crs: "", linear_units: "Metres" });

const shown = computed(() => {
	const f = filter.value.toLowerCase();
	return models.value.filter((m) => !f || `${m.model_name} ${m.name} ${m.model_type}`.toLowerCase().includes(f));
});

function chip(status) {
	return { Approved: "ok", "In Review": "info", Rejected: "error" }[status] || "";
}

async function load_models() {
	try {
		models.value = await call("list_models", { geo_project: project.value || null });
	} catch (e) {
		emit("error", e.message);
	}
}

async function create() {
	busy.value = true;
	try {
		const values = { ...draft, crs: draft.crs || props.config.default_crs, geo_project: draft.geo_project || project.value || null };
		const result = await call("create_model", { values });
		creating.value = false;
		Object.assign(draft, { model_name: "", model_code: "" });
		await load_models();
		emit("open", result.model.name);
	} catch (e) {
		emit("error", e.message);
	} finally {
		busy.value = false;
	}
}

onMounted(async () => {
	draft.linear_units = props.config.default_linear_units || "Metres";
	try {
		projects.value = await call("list_projects");
	} catch (e) {
		emit("error", e.message);
	}
	await load_models();
});

defineExpose({ reload: load_models });
</script>
