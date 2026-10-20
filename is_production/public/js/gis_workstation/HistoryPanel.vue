<template>
	<!-- Processing History: recent jobs; click one to see its parameters, log and outputs. -->
	<div class="gis-history-panel">
		<div class="gis-panel-tools">
			<span class="muted small">{{ __("Recent processing jobs") }}</span>
			<span class="gis-spacer"></span>
			<button class="gis-icon-btn" :title="__('Refresh')" @click="load"><Icon name="refresh" :size="15" /></button>
		</div>
		<div class="gis-scroll">
			<div v-for="h in history" :key="h.name" class="gis-tree-item gis-history-item" :title="h.name" @click="$emit('open-job', h.name)">
				<span class="gis-status-dot" :class="'is-' + h.status.toLowerCase()"></span>
				<span class="gis-row-title">{{ h.algorithm_name }}</span>
				<span class="muted small">{{ String(h.creation).slice(5, 16) }}</span>
			</div>
			<p v-if="!history.length" class="muted pad">{{ __("No processing jobs yet.") }}</p>
		</div>
	</div>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { call } from "./api.js";
import Icon from "./Icon.vue";

const __ = window.__;
defineEmits(["open-job"]);
const history = ref([]);

async function load() {
	try {
		history.value = await call("is_production.geo_planning.services.gis_processing_service.processing_history");
	} catch (e) {
		history.value = [];
	}
}

onMounted(load);
defineExpose({ load });
</script>
