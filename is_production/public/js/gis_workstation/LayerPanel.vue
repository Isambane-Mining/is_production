<template>
	<div class="gis-layer-panel">
		<section class="gis-section" :style="{ flex: browser_open ? '1 1 45%' : '0 0 auto' }">
			<header class="gis-section-head" @click="browser_open = !browser_open">
				<span>{{ browser_open ? "▾" : "▸" }} {{ __("Browser") }}</span>
				<button class="gis-icon-btn" :title="__('Refresh the dataset list')" @click.stop="$emit('refresh')">⟳</button>
			</header>
			<template v-if="browser_open">
				<input v-model="search" class="gis-input" type="search" :placeholder="__('Search datasets…')" />
				<div class="gis-scroll">
					<p v-if="loadingDatasets" class="muted pad">{{ __("Loading…") }}</p>
					<p v-else-if="!datasets.length" class="muted pad">
						{{ __("No datasets yet.") }}
						<a href="/desk/gis-dataset/new" target="_blank">{{ __("Create one") }}</a>
					</p>
					<template v-for="group in groups" :key="group.category">
						<div class="gis-group">{{ group.category }}</div>
						<div
							v-for="d in group.items"
							:key="d.name"
							class="gis-row gis-dataset-row"
							:class="{ added: added.has(d.name), disabled: !d.spatial }"
							:title="`${d.name} · ${d.geometry_type} · ${d.crs}`"
							@dblclick="d.spatial && $emit('add', d)"
						>
							<span class="gis-geom-icon">{{ icon(d.geometry_type) }}</span>
							<span class="gis-row-title">{{ d.title }}</span>
							<span class="muted small">{{ d.feature_count }}</span>
							<button
								class="gis-icon-btn"
								:disabled="!d.spatial || added.has(d.name)"
								:title="d.spatial ? __('Add to map') : __('No geometry')"
								@click="$emit('add', d)"
							>
								＋
							</button>
						</div>
					</template>
				</div>
			</template>
		</section>

		<section class="gis-section" style="flex: 1 1 55%">
			<header class="gis-section-head">
				<span>{{ __("Layers") }}</span>
				<span class="muted small">{{ layers.length }}</span>
			</header>
			<div class="gis-scroll">
				<p v-if="!layers.length" class="muted pad">{{ __("Add a dataset from the Browser (＋ or double-click).") }}</p>
				<div
					v-for="(l, i) in layers"
					:key="l.name"
					class="gis-layer"
					:class="{ active: l.name === active }"
					@click="$emit('activate', l.name)"
				>
					<div class="gis-row">
						<input type="checkbox" :checked="l.visible" :title="__('Visible')" @click.stop @change="$emit('visible', l.name, $event.target.checked)" />
						<input
							type="color"
							class="gis-swatch"
							:value="l.color || '#2f80ed'"
							:title="__('Colour')"
							@click.stop
							@change="$emit('color', l.name, $event.target.value)"
						/>
						<span class="gis-row-title" :title="`${l.name} · ${l.dataset.crs}`">{{ l.title }}</span>
						<span class="small" :class="state_class(l)" :title="state_title(l)">{{ state_text(l) }}</span>
					</div>
					<div v-if="l.name === active" class="gis-row gis-layer-tools">
						<button class="gis-icon-btn" :disabled="i === 0" :title="__('Move up')" @click.stop="$emit('move', l.name, -1)">▲</button>
						<button class="gis-icon-btn" :disabled="i === layers.length - 1" :title="__('Move down')" @click.stop="$emit('move', l.name, 1)">▼</button>
						<button class="gis-icon-btn" :title="__('Zoom to layer')" @click.stop="$emit('zoom', l.name)">▣</button>
						<label class="gis-opacity" :title="__('Opacity')" @click.stop>
							◐
							<input type="range" min="0" max="1" step="0.05" :value="l.opacity" @input="$emit('opacity', l.name, Number($event.target.value))" />
						</label>
						<button class="gis-icon-btn" :title="__('Remove layer')" @click.stop="$emit('remove', l.name)">✕</button>
					</div>
				</div>
			</div>
		</section>
	</div>
</template>

<script setup>
import { computed, ref } from "vue";

const __ = window.__;
const props = defineProps({
	datasets: Array,
	layers: Array,
	active: String,
	loadingDatasets: Boolean,
});
defineEmits(["add", "remove", "activate", "visible", "opacity", "color", "move", "zoom", "refresh"]);

const search = ref("");
const browser_open = ref(true);
const added = computed(() => new Set(props.layers.map((l) => l.name)));

const groups = computed(() => {
	const q = search.value.trim().toLowerCase();
	const by = {};
	for (const d of props.datasets) {
		if (q && !`${d.title} ${d.name} ${d.category || ""}`.toLowerCase().includes(q)) continue;
		(by[d.category || __("Uncategorised")] ||= []).push(d);
	}
	return Object.keys(by)
		.sort()
		.map((category) => ({ category, items: by[category] }));
});

function icon(type) {
	if (/Point/.test(type)) return "•";
	if (/LineString/.test(type)) return "∿";
	if (/Polygon/.test(type)) return "▱";
	if (type === "Raster") return "▦";
	if (type === "No Geometry") return "☰";
	return "◇";
}

function state_text(l) {
	if (!l.visible) return "";
	return { loading: "…", error: "!", truncated: `${l.count}+` }[l.state] ?? String(l.count);
}
function state_title(l) {
	return {
		loading: __("Loading"),
		error: __("Could not load"),
		truncated: __("More features than shown; zoom in"),
	}[l.state] ?? __("Features in view");
}
function state_class(l) {
	return { error: "is-error", truncated: "is-warning" }[l.state] || "muted";
}
</script>
