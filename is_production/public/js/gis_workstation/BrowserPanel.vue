<template>
	<div class="gis-browser-panel">
		<div class="gis-panel-tools">
			<input v-model="search" class="gis-input" type="search" :placeholder="__('Filter…')" />
			<button class="gis-icon-btn" :title="__('Refresh')" @click="$emit('refresh')"><Icon name="refresh" :size="15" /></button>
		</div>
		<div class="gis-scroll">
			<p v-if="loading" class="muted pad">{{ __("Loading…") }}</p>
			<details v-for="group in groups" :key="group.category" open class="gis-tree-group">
				<summary><Icon name="layers" :size="14" /> {{ group.category }}</summary>
				<div
					v-for="d in group.items"
					:key="d.name"
					class="gis-row gis-tree-item"
					:class="{ added: added.has(d.name), disabled: !d.spatial }"
					:title="`${d.name} · ${d.geometry_type} · ${d.crs} · ${d.feature_count} features`"
					draggable="true"
					@dragstart="$event.dataTransfer.setData('text/gis-dataset', d.name)"
					@dblclick="d.spatial && $emit('add', d)"
					@contextmenu.prevent="$emit('context', $event, { kind: 'dataset', item: d })"
				>
					<span class="gis-geom-icon">{{ icon(d.geometry_type) }}</span>
					<span class="gis-row-title">{{ d.title }}</span>
					<span class="muted small">{{ d.feature_count }}</span>
				</div>
			</details>
			<details v-if="services.length" open class="gis-tree-group">
				<summary><Icon name="globe" :size="14" /> {{ __("Map Services") }}</summary>
				<div
					v-for="s in services"
					:key="s.name"
					class="gis-row gis-tree-item"
					:class="{ added: added.has('service:' + s.name), disabled: !!s.error }"
					:title="s.error || s.attribution || ''"
					@dblclick="!s.error && $emit('add-service', s)"
					@contextmenu.prevent="$emit('context', $event, { kind: 'service', item: s })"
				>
					<span class="gis-geom-icon">▦</span>
					<span class="gis-row-title">{{ s.title }}</span>
					<span class="muted small">{{ __("overlay") }}</span>
				</div>
			</details>
			<p v-if="!loading && !datasets.length" class="muted pad">
				{{ __("No datasets yet.") }} <a href="/desk/gis-dataset/new" target="_blank">{{ __("Create one") }}</a> {{ __("or use Layer → Import Dataset.") }}
			</p>
		</div>
	</div>
</template>

<script setup>
import { computed, ref } from "vue";
import Icon from "./Icon.vue";

const __ = window.__;
const props = defineProps({ datasets: Array, mapServices: Array, layers: Array, loading: Boolean });
defineEmits(["add", "add-service", "context", "refresh"]);
const search = ref("");
const added = computed(() => new Set(props.layers.map((l) => l.name)));
const q = computed(() => search.value.trim().toLowerCase());

const groups = computed(() => {
	const by = {};
	for (const d of props.datasets) {
		if (q.value && !`${d.title} ${d.name} ${d.category || ""}`.toLowerCase().includes(q.value)) continue;
		(by[d.category || __("Uncategorised")] ||= []).push(d);
	}
	return Object.keys(by).sort().map((category) => ({ category, items: by[category] }));
});
const services = computed(() =>
	(props.mapServices || []).filter((s) => s.use_as === "Overlay" && (!q.value || s.title.toLowerCase().includes(q.value)))
);

function icon(type) {
	if (/Point/.test(type)) return "•";
	if (/LineString/.test(type)) return "∿";
	if (/Polygon/.test(type)) return "▱";
	if (type === "Raster") return "▦";
	if (type === "No Geometry") return "☰";
	return "◇";
}
</script>
