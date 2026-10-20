<template>
	<div class="gis-modal-backdrop" @click.self="$emit('close')">
		<div class="gis-modal">
			<h3>{{ __("Open Project") }}</h3>
			<input v-model="search" class="gis-input" type="search" :placeholder="__('Search projects…')" />
			<p v-if="loading" class="muted">{{ __("Loading…") }}</p>
			<p v-else-if="!filtered.length" class="muted">{{ __("No saved projects yet. Use Project → Save Project.") }}</p>
			<div class="gis-project-list">
				<button v-for="p in filtered" :key="p.name" class="gis-project-item" @click="$emit('open', p.name)">
					<b>{{ p.title }}</b>
					<span class="muted small">{{ p.name }} · {{ p.modified.slice(0, 16) }} · {{ p.owner }}</span>
					<span v-if="p.description" class="small">{{ p.description }}</span>
				</button>
			</div>
			<div class="gis-modal-actions"><button @click="$emit('close')">{{ __("Cancel") }}</button></div>
		</div>
	</div>
</template>

<script setup>
import { computed, onMounted, ref } from "vue";
import { call } from "./api.js";

const __ = window.__;
defineEmits(["close", "open"]);
const projects = ref([]);
const loading = ref(true);
const search = ref("");
const filtered = computed(() => {
	const q = search.value.trim().toLowerCase();
	return q ? projects.value.filter((p) => `${p.title} ${p.name} ${p.description || ""}`.toLowerCase().includes(q)) : projects.value;
});

onMounted(async () => {
	try {
		projects.value = await call("is_production.geo_planning.services.gis_project_service.list_projects");
	} finally {
		loading.value = false;
	}
});
</script>
