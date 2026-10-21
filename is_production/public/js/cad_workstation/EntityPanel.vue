<template>
	<div class="cad-panel-body">
		<div class="cad-row">
			<input v-model="filter" class="cad-grow" :placeholder="__('Filter by type, id or layer…')" />
			<select v-model="status">
				<option value="">{{ __("Any status") }}</option>
				<option v-for="s in ['Not Checked', 'Valid', 'Warning', 'Invalid']" :key="s" :value="s">{{ __(s) }}</option>
			</select>
		</div>
		<div class="muted small pad">{{ __("{0} of {1} entities", [shown.length, rows.length]) }}</div>
		<div class="cad-list">
			<button
				v-for="e in shown.slice(0, 500)"
				:key="e.entity_id"
				class="cad-list-row"
				:class="{ active: selected.includes(e.entity_id) }"
				@click="$emit('select', [e.entity_id], $event.shiftKey)"
				@dblclick="$emit('zoom', [e.entity_id])"
			>
				<span class="cad-grow cad-ellipsis">
					<b>{{ __(e.entity_type) }}</b>
					<span class="muted small mono"> {{ e.entity_id }}</span>
					<br />
					<span class="muted small">{{ layer_name(e) }}</span>
				</span>
				<span class="cad-chip" :class="chip(e)">{{ dirty.has(e.entity_id) ? __("Unsaved") : __(e.validation_status || "Not Checked") }}</span>
			</button>
			<div v-if="shown.length > 500" class="muted small pad">{{ __("Showing the first 500; filter to narrow.") }}</div>
		</div>
	</div>
</template>

<script setup>
import { computed, ref } from "vue";

const props = defineProps({ rows: Array, layers: Map, selected: Array, dirty: Set });
defineEmits(["select", "zoom"]);
const filter = ref("");
const status = ref("");

function layer_name(e) {
	const layer = props.layers.get(e.cad_layer);
	return layer ? layer.layer_name : e.cad_layer;
}

function chip(e) {
	if (props.dirty.has(e.entity_id)) return "info";
	return { Valid: "ok", Warning: "warning", Invalid: "error" }[e.validation_status] || "";
}

const shown = computed(() => {
	const f = filter.value.toLowerCase();
	return props.rows.filter(
		(e) => (!status.value || e.validation_status === status.value) && (!f || `${e.entity_type} ${e.entity_id} ${layer_name(e)}`.toLowerCase().includes(f))
	);
});
</script>
