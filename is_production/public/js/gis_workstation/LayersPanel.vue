<template>
	<div class="gis-layers-panel" @dragover.prevent @drop="drop_dataset">
		<div class="gis-scroll">
			<p v-if="!layers.length" class="muted pad">{{ __("Add a layer: double-click a dataset in the Browser, or drag it here.") }}</p>
			<div
				v-for="(l, i) in layers"
				:key="l.name"
				class="gis-layer-node"
				:class="{ active: l.name === active, dragging: dragged === l.name, 'drop-before': drop_at === i }"
				draggable="true"
				@dragstart="dragged = l.name"
				@dragend="dragged = null; drop_at = null"
				@dragover.prevent="dragged && (drop_at = i)"
				@drop.stop="drop_layer(i)"
				@click="$emit('activate', l.name)"
				@contextmenu.prevent="$emit('activate', l.name); $emit('context', $event, l)"
			>
				<div class="gis-row">
					<button
						class="gis-icon-btn gis-expander"
						:class="{ hidden: l.kind === 'service' }"
						@click.stop="expanded[l.name] = !expanded[l.name]"
					>
						<Icon :name="expanded[l.name] ? 'chevron-down' : 'chevron-right'" :size="13" />
					</button>
					<input type="checkbox" :checked="l.visible" :title="__('Visible')" @click.stop @change="$emit('visible', l.name, $event.target.checked)" />
					<span v-if="l.kind === 'service'" class="gis-geom-icon">▦</span>
					<span v-else class="gis-layer-swatch" :style="{ background: l.renderer === 'browser' ? l.color : 'transparent' }">
						<img v-if="l.renderer === 'qgis' && legendUrl(l.name)" :src="legendUrl(l.name)" alt="" />
					</span>
					<span class="gis-row-title" :title="`${l.title} · ${l.kind === 'service' ? __('map service') : l.dataset.crs}`">{{ l.title }}</span>
					<Icon v-if="editingName === l.name" name="edit" :size="13" class="gis-editing-mark" />
					<span class="small" :class="state_class(l)" :title="state_title(l)">{{ state_text(l) }}</span>
				</div>
				<div v-if="expanded[l.name] && l.kind !== 'service'" class="gis-legend">
					<img v-if="l.renderer === 'qgis' && legendUrl(l.name)" :src="legendUrl(l.name)" alt="" />
					<span v-else class="small muted"><span class="gis-layer-swatch" :style="{ background: l.color }"></span> {{ l.dataset.geometry_type }} · {{ l.dataset.crs }}</span>
					<span class="small muted">{{ l.renderer === "qgis" ? __("drawn by QGIS Server") : __("drawn in the browser") }} · {{ Math.round(l.opacity * 100) }}%</span>
				</div>
			</div>
			<div class="gis-layer-node gis-basemap-node" :title="__('The basemap is always drawn under the layers')">
				<div class="gis-row">
					<span class="gis-icon-btn gis-expander hidden"></span>
					<Icon name="map" :size="15" />
					<select class="gis-basemap-select" :value="basemap || ''" @change="$emit('set-basemap', $event.target.value || null)">
						<option value="">{{ __("No basemap") }}</option>
						<optgroup v-for="g in basemap_groups" :key="g.category" :label="g.category">
							<option v-for="s in g.items" :key="s.name" :value="s.name" :disabled="!!s.error">{{ s.title }}{{ s.error ? " — " + __("not available") : "" }}</option>
						</optgroup>
					</select>
				</div>
				<label v-if="basemap" class="gis-row gis-opacity small muted" :title="__('Basemap opacity')">
					<span class="gis-icon-btn gis-expander hidden"></span>{{ __("Opacity") }}
					<input type="range" min="0" max="1" step="0.05" :value="basemapOpacity" @input="$emit('basemap-opacity', Number($event.target.value))" />
				</label>
			</div>
		</div>
	</div>
</template>

<script setup>
import { computed, reactive, ref } from "vue";
import Icon from "./Icon.vue";

const __ = window.__;
const props = defineProps({
	layers: Array,
	active: String,
	editingName: String,
	legendUrl: Function,
	mapServices: { type: Array, default: () => [] },
	basemap: String,
	basemapOpacity: { type: Number, default: 1 },
});
const emit = defineEmits(["activate", "visible", "context", "reorder", "drop-dataset", "set-basemap", "basemap-opacity"]);
const expanded = reactive({});
const dragged = ref(null);
const drop_at = ref(null);

const basemap_groups = computed(() => {
	const by = {};
	for (const s of props.mapServices.filter((x) => x.use_as === "Basemap")) (by[s.category || __("Other")] ||= []).push(s);
	return Object.keys(by).map((category) => ({ category, items: by[category] }));
});

function drop_layer(index) {
	if (dragged.value) emit("reorder", dragged.value, index);
	dragged.value = drop_at.value = null;
}

function drop_dataset(event) {
	const name = event.dataTransfer.getData("text/gis-dataset");
	if (name) emit("drop-dataset", name);
}

function state_text(l) {
	if (l.kind === "service" || !l.visible) return "";
	return { loading: "…", error: "!", truncated: `${l.count}+` }[l.state] ?? String(l.count);
}
function state_title(l) {
	return { loading: __("Loading"), error: __("Could not load"), truncated: __("More features than shown; zoom in") }[l.state] ?? __("Features in view");
}
function state_class(l) {
	return { error: "is-error", truncated: "is-warning" }[l.state] || "muted";
}
</script>
