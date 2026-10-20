<template>
	<div class="gis-app" :class="{ 'is-resizing': resizing }">
		<!-- Menu bar -->
		<nav class="gis-menubar" @mouseleave="open_menu = null">
			<span class="gis-brand">{{ __("GIS Workstation") }}</span>
			<div v-for="menu in menus" :key="menu.label" class="gis-menu">
				<button
					class="gis-menu-title"
					:class="{ open: open_menu === menu.label }"
					@click="open_menu = open_menu === menu.label ? null : menu.label"
					@mouseenter="open_menu && (open_menu = menu.label)"
				>
					{{ menu.label }}
				</button>
				<div v-if="open_menu === menu.label" class="gis-menu-items">
					<template v-for="(item, i) in menu.items" :key="i">
						<hr v-if="item === '-'" />
						<button
							v-else
							:disabled="item.disabled"
							:title="item.hint || ''"
							@click="run_menu(item)"
						>
							<span>{{ item.label }}</span>
							<span v-if="item.hint" class="gis-menu-hint">{{ item.hint }}</span>
						</button>
					</template>
				</div>
			</div>
			<span class="gis-spacer"></span>
			<a class="gis-menubar-link" href="/desk/gis-dataset" target="_blank">{{ __("Desk") }}</a>
		</nav>

		<!-- Toolbar -->
		<div class="gis-toolbar" role="toolbar">
			<button :class="{ active: tool === 'identify' }" :title="__('Identify features (click the map)')" @click="map.set_tool('identify')">⌖ {{ __("Identify") }}</button>
			<button :class="{ active: tool === 'zoom_box' }" :title="__('Drag a box to zoom in')" @click="map.set_tool('zoom_box')">⬚ {{ __("Zoom Box") }}</button>
			<span class="gis-sep"></span>
			<button :title="__('Zoom in')" @click="map.zoom_by(1)">＋</button>
			<button :title="__('Zoom out')" @click="map.zoom_by(-1)">－</button>
			<button :title="__('Zoom to all visible layers')" @click="zoom_full">⤢ {{ __("Full Extent") }}</button>
			<button :title="__('Zoom to the active layer')" :disabled="!active_layer" @click="zoom_to_layer(active_layer)">▣ {{ __("Zoom to Layer") }}</button>
			<button :title="__('Zoom to the selected feature')" :disabled="!selection" @click="zoom_to_selection">◎ {{ __("Zoom to Selection") }}</button>
			<span class="gis-sep"></span>
			<button :title="__('Previous extent')" :disabled="!can_back" @click="map.go(-1)">◀</button>
			<button :title="__('Next extent')" :disabled="!can_forward" @click="map.go(1)">▶</button>
			<button :title="__('Reload layers')" @click="map.reload()">⟳</button>
			<span class="gis-sep"></span>
			<button :class="{ active: panels.left }" @click="toggle('left')">{{ __("Layers") }}</button>
			<button :class="{ active: panels.right }" @click="toggle('right')">{{ __("Feature") }}</button>
			<button :class="{ active: panels.bottom }" @click="toggle('bottom')">{{ __("Attribute Table") }}</button>
		</div>

		<!-- Body -->
		<div class="gis-body">
			<aside v-show="panels.left" class="gis-panel gis-left" :style="{ width: sizes.left + 'px' }">
				<LayerPanel
					:datasets="datasets"
					:layers="layers"
					:active="active_layer"
					:loading-datasets="loading_datasets"
					@add="add_layer"
					@remove="remove_layer"
					@activate="active_layer = $event"
					@visible="set_visible"
					@opacity="set_opacity"
					@color="set_color"
					@move="move_layer"
					@zoom="zoom_to_layer"
					@refresh="load_datasets"
				/>
			</aside>
			<div v-show="panels.left" class="gis-splitter vertical" @pointerdown="start_resize('left', $event)"></div>

			<main class="gis-center">
				<div ref="map_el" class="gis-map"></div>
				<template v-if="panels.bottom">
					<div class="gis-splitter horizontal" @pointerdown="start_resize('bottom', $event)"></div>
					<section class="gis-panel gis-bottom" :style="{ height: sizes.bottom + 'px' }">
						<AttributeTable
							:layer="layer_by_name(active_layer)"
							:features="table_rows"
							:selected-id="selection && selection.dataset === active_layer ? selection.id : null"
							@select="select_from_table"
						/>
					</section>
				</template>
			</main>

			<div v-show="panels.right" class="gis-splitter vertical" @pointerdown="start_resize('right', $event)"></div>
			<aside v-show="panels.right" class="gis-panel gis-right" :style="{ width: sizes.right + 'px' }">
				<FeaturePanel
					:selection="selection_detail"
					:can-edit="config.can_edit_features"
					@zoom="zoom_to_selection"
					@clear="clear_selection"
				/>
			</aside>
		</div>

		<!-- Status bar -->
		<footer class="gis-statusbar">
			<span class="gis-status-item" :title="__('Pointer position')">
				{{ __("Coordinate") }}: <b class="mono">{{ pointer_text || "—" }}</b>
			</span>
			<span class="gis-status-item">
				{{ __("Scale") }} <b class="mono">1:{{ scale_text }}</b>
			</span>
			<span class="gis-status-item">
				<label>
					{{ __("CRS") }}
					<select v-model="display_crs" @change="crs_changed">
						<option v-for="code in crs_options" :key="code" :value="code">{{ crs_label(code) }}</option>
					</select>
				</label>
			</span>
			<span class="gis-spacer"></span>
			<span v-if="busy" class="gis-status-item">{{ __("Loading…") }}</span>
			<a
				class="gis-status-item gis-health"
				:class="'is-' + (config.health.status || 'unknown').toLowerCase()"
				:href="config.can_manage_settings ? '/desk/gis-settings' : null"
				target="_blank"
				:title="health_title"
			>
				● QGIS {{ config.health.qgis_version || "" }} · {{ config.health.status ? __(config.health.status) : __("Not checked") }}
			</a>
		</footer>

		<!-- Toasts -->
		<div class="gis-toasts">
			<div v-for="t in toasts" :key="t.id" class="gis-toast" :class="'is-' + t.kind" @click="dismiss(t.id)">{{ t.text }}</div>
		</div>

		<!-- About -->
		<div v-if="about_open" class="gis-modal-backdrop" @click.self="about_open = false">
			<div class="gis-modal">
				<h3>{{ __("About the GIS Workstation") }}</h3>
				<p>{{ __("Part of the Isambane Production app. Datasets, permissions and editing are managed by Frappe; map rendering and processing use QGIS Server and PyQGIS.") }}</p>
				<p>
					<b>QGIS — Open Source Geographic Information System</b><br />
					{{ __("Developed by the QGIS contributors and community.") }}
					<a href="https://qgis.org/" target="_blank" rel="noopener">qgis.org</a> ·
					<a href="https://github.com/qgis/QGIS" target="_blank" rel="noopener">{{ __("source") }}</a> ·
					GPL-2.0-or-later
				</p>
				<p>{{ __("This workstation is an independent application. It is not an official QGIS product and is not endorsed by or affiliated with the QGIS project.") }}</p>
				<p class="muted">OpenLayers (BSD-2-Clause) · proj4js (MIT) · GDAL/OGR, PROJ (MIT) · {{ __("Map data") }} © OpenStreetMap contributors</p>
				<div class="gis-modal-actions"><button @click="about_open = false">{{ __("Close") }}</button></div>
			</div>
		</div>
	</div>
</template>

<script setup>
import { computed, markRaw, nextTick, onBeforeUnmount, onMounted, reactive, ref, shallowRef, watch } from "vue";
import { call } from "./api.js";
import { crs_label, format_coordinate, from_display, load_crs } from "./crs.js";
import { GisMap } from "./map.js";
import AttributeTable from "./AttributeTable.vue";
import FeaturePanel from "./FeaturePanel.vue";
import LayerPanel from "./LayerPanel.vue";

const __ = window.__;
const STORAGE_KEY = "isp-gis-workstation";

const props = defineProps({ config: { type: Object, required: true } });
const config = props.config;

const map_el = ref(null);
const map = shallowRef(null);
const datasets = ref([]);
const loading_datasets = ref(false);
const layers = ref([]); // [{ name, title, visible, opacity, color, state, count, dataset }] top first
const active_layer = ref(null);
const selection = ref(null); // { dataset, id }
const selection_detail = ref(null);
const table_version = ref(0);
const tool = ref("identify");
const pointer = ref(null);
const view_info = reactive({ scale: 0 });
const display_crs = ref(config.default_crs);
const extra_crs = ref([]);
const open_menu = ref(null);
const about_open = ref(false);
const toasts = ref([]);
const can_back = ref(false);
const can_forward = ref(false);
const saved = read_saved();
const panels = reactive(Object.assign({ left: true, right: true, bottom: false }, saved.panels));
const sizes = reactive(Object.assign({ left: 290, right: 300, bottom: 240 }, saved.sizes));
const resizing = ref(false);

const crs_options = computed(() => [...new Set([config.default_crs, "EPSG:4326", "EPSG:3857", ...extra_crs.value])]);
const pointer_text = computed(() => (pointer.value ? format_coordinate(pointer.value, display_crs.value) : ""));
const scale_text = computed(() => (view_info.scale ? Math.round(view_info.scale).toLocaleString() : "—"));
const busy = computed(() => layers.value.some((l) => l.state === "loading"));
const health_title = computed(() => {
	const h = config.health;
	return h.status ? `${h.summary || ""}\n${__("Checked")}: ${h.checked_on || ""}` : __("Run Check Dependencies in GIS Settings.");
});
const table_rows = computed(() => {
	table_version.value; // re-read when a layer reloads
	return map.value && active_layer.value ? map.value.features(active_layer.value) : [];
});

const menus = computed(() => [
	{
		label: __("Project"),
		items: [
			{ label: __("New Project"), disabled: true, hint: __("GIS Project — later phase") },
			{ label: __("Open Project…"), disabled: true, hint: __("later phase") },
			{ label: __("Export to QGIS (GeoPackage)…"), disabled: true, hint: __("later phase") },
		],
	},
	{
		label: __("Layer"),
		items: [
			{ label: __("New Dataset…"), action: () => window.open("/desk/gis-dataset/new", "_blank") },
			{ label: __("Import Dataset…"), disabled: true, hint: __("later phase") },
			"-",
			{ label: __("Zoom to Layer"), disabled: !active_layer.value, action: () => zoom_to_layer(active_layer.value) },
			{ label: __("Remove Layer"), disabled: !active_layer.value, action: () => remove_layer(active_layer.value) },
			{ label: __("Open Dataset in Desk"), disabled: !active_layer.value, action: () => window.open(`/desk/gis-dataset/${encodeURIComponent(active_layer.value)}`, "_blank") },
		],
	},
	{
		label: __("View"),
		items: [
			{ label: __("Full Extent"), action: zoom_full },
			{ label: __("Previous Extent"), disabled: !can_back.value, action: () => map.value.go(-1) },
			{ label: __("Next Extent"), disabled: !can_forward.value, action: () => map.value.go(1) },
			{ label: __("Go to Coordinate…"), action: go_to_coordinate },
			"-",
			{ label: (panels.left ? "✓ " : "") + __("Layers Panel"), action: () => toggle("left") },
			{ label: (panels.right ? "✓ " : "") + __("Feature Panel"), action: () => toggle("right") },
			{ label: (panels.bottom ? "✓ " : "") + __("Attribute Table"), action: () => toggle("bottom") },
			{ label: (basemap_on.value ? "✓ " : "") + __("OpenStreetMap Basemap"), action: toggle_basemap },
		],
	},
	{
		label: __("Processing"),
		items: [{ label: __("Toolbox…"), disabled: true, hint: __("QGIS Processing — later phase") }],
	},
	{
		label: __("Help"),
		items: [
			{ label: __("GIS Settings"), disabled: !config.can_manage_settings, action: () => window.open("/desk/gis-settings", "_blank") },
			{ label: __("About / Attributions"), action: () => (about_open.value = true) },
		],
	},
]);

const basemap_on = ref(saved.basemap !== undefined ? saved.basemap : config.basemap === "OpenStreetMap");

// ---------------------------------------------------------------- startup

onMounted(async () => {
	map.value = markRaw(
		new GisMap(map_el.value, { ...config, basemap: basemap_on.value ? "OpenStreetMap" : "None" }, {
			pointer: (c) => (pointer.value = c),
			view: (v) => {
				view_info.scale = v.scale;
				update_history();
			},
			identify: (hit) => {
				selection.value = hit ? { dataset: hit.dataset, id: hit.feature.getId() } : null;
				if (hit) active_layer.value = hit.dataset;
				if (hit && !panels.right) panels.right = true;
			},
			loading: (name, state, count) => {
				const layer = layer_by_name(name);
				if (layer) Object.assign(layer, { state, count: count ?? layer.count });
				if (state !== "loading") table_version.value++;
				if (state === "truncated") toast(__("{0}: only the first {1} features in this view are shown. Zoom in to see all.", [layer.title, config.max_features]), "warning");
			},
			error: (message) => toast(message, "error"),
			tool: (t) => (tool.value = t),
		})
	);
	try {
		await load_crs(config.default_crs);
	} catch (e) {
		toast(e.message, "error");
	}
	await load_datasets();
	restore_layers();
	window.addEventListener("keydown", on_key);
});

onBeforeUnmount(() => window.removeEventListener("keydown", on_key));

async function load_datasets() {
	loading_datasets.value = true;
	try {
		datasets.value = await call("list_datasets");
		for (const layer of layers.value) {
			const fresh = datasets.value.find((d) => d.name === layer.name);
			if (fresh) layer.dataset = fresh;
		}
	} catch (e) {
		toast(e.message, "error");
	} finally {
		loading_datasets.value = false;
	}
}

// ---------------------------------------------------------------- layers

function layer_by_name(name) {
	return layers.value.find((l) => l.name === name) || null;
}

async function add_layer(dataset, options = {}) {
	if (layer_by_name(dataset.name)) {
		active_layer.value = dataset.name;
		return;
	}
	if (!dataset.spatial) {
		toast(__("{0} has no geometry to show on the map.", [dataset.title]), "warning");
		return;
	}
	try {
		await load_crs(dataset.crs);
		if (!extra_crs.value.includes(dataset.crs)) extra_crs.value.push(dataset.crs);
	} catch (e) {
		/* the readout falls back to the other CRSs */
	}
	const layer = reactive({
		name: dataset.name,
		title: dataset.title,
		visible: options.visible ?? true,
		opacity: options.opacity ?? 1,
		color: null,
		state: "loading",
		count: 0,
		dataset,
	});
	layers.value.unshift(layer);
	layer.color = map.value.add_layer(dataset, { visible: layer.visible, opacity: layer.opacity, color: options.color });
	active_layer.value = dataset.name;
	apply_order();
	if (!options.restoring && layers.value.length === 1) zoom_to_layer(dataset.name);
	save();
}

function remove_layer(name) {
	map.value.remove_layer(name);
	layers.value = layers.value.filter((l) => l.name !== name);
	if (selection.value && selection.value.dataset === name) clear_selection();
	if (active_layer.value === name) active_layer.value = layers.value.length ? layers.value[0].name : null;
	save();
}

function set_visible(name, visible) {
	const layer = layer_by_name(name);
	layer.visible = visible;
	map.value.set_visible(name, visible);
	save();
}

function set_opacity(name, opacity) {
	layer_by_name(name).opacity = opacity;
	map.value.set_opacity(name, opacity);
	save();
}

function set_color(name, color) {
	layer_by_name(name).color = color;
	map.value.set_color(name, color);
	save();
}

function move_layer(name, step) {
	const i = layers.value.findIndex((l) => l.name === name);
	const j = i + step;
	if (i < 0 || j < 0 || j >= layers.value.length) return;
	const list = [...layers.value];
	[list[i], list[j]] = [list[j], list[i]];
	layers.value = list;
	apply_order();
	save();
}

function apply_order() {
	map.value.set_order(layers.value.map((l) => l.name));
}

function zoom_to_layer(name) {
	if (name && !map.value.zoom_to_layer(name)) toast(__("This layer has no features yet."), "warning");
}

function zoom_full() {
	map.value.zoom_full(config);
}

function restore_layers() {
	for (const item of [...(saved.layers || [])].reverse()) {
		const dataset = datasets.value.find((d) => d.name === item.name);
		if (dataset) add_layer(dataset, { ...item, restoring: true });
	}
}

// ---------------------------------------------------------------- selection

watch(selection, async (value) => {
	map.value && map.value.select(value);
	if (!value) {
		selection_detail.value = null;
		return;
	}
	const feature = map.value.find(value.dataset, value.id);
	const layer = layer_by_name(value.dataset);
	selection_detail.value = feature
		? {
				id: value.id,
				dataset: value.dataset,
				dataset_title: layer ? layer.title : value.dataset,
				crs: layer ? layer.dataset.crs : "",
				label: feature.get("label"),
				geometry_type: feature.get("geometry_type"),
				revision: feature.get("revision"),
				validation_status: feature.get("validation_status"),
				attributes: feature.get("attributes") || {},
		  }
		: null;
});

function select_from_table(id) {
	selection.value = { dataset: active_layer.value, id };
	map.value.zoom_to_feature(active_layer.value, id);
}

function zoom_to_selection() {
	if (selection.value) map.value.zoom_to_feature(selection.value.dataset, selection.value.id);
}

function clear_selection() {
	selection.value = null;
}

// ---------------------------------------------------------------- view, panels, misc

function update_history() {
	nextTick(() => {
		can_back.value = map.value.can_go(-1);
		can_forward.value = map.value.can_go(1);
	});
}

function toggle(panel) {
	panels[panel] = !panels[panel];
	save();
	nextTick(() => map.value.update_size());
}

function toggle_basemap() {
	basemap_on.value = !basemap_on.value;
	map.value.set_basemap(basemap_on.value);
	save();
}

async function crs_changed() {
	try {
		await load_crs(display_crs.value);
	} catch (e) {
		toast(e.message, "error");
	}
}

function go_to_coordinate() {
	const text = window.prompt(__("Go to coordinate in {0} (x, y):", [crs_label(display_crs.value)]));
	if (!text) return;
	const xy = text.split(/[\s,;]+/).filter(Boolean).map(Number);
	const target = xy.length === 2 && xy.every(Number.isFinite) ? from_display(xy, display_crs.value) : null;
	if (!target || !target.every(Number.isFinite)) {
		toast(__("Enter two numbers, x and y."), "error");
		return;
	}
	map.value.go_to(target);
}

function start_resize(panel, event) {
	event.preventDefault();
	resizing.value = true;
	const start = { x: event.clientX, y: event.clientY, size: sizes[panel] };
	const move = (e) => {
		if (panel === "left") sizes.left = clamp(start.size + e.clientX - start.x, 180, 600);
		if (panel === "right") sizes.right = clamp(start.size - (e.clientX - start.x), 200, 600);
		if (panel === "bottom") sizes.bottom = clamp(start.size - (e.clientY - start.y), 100, window.innerHeight - 200);
		map.value.update_size();
	};
	const up = () => {
		resizing.value = false;
		window.removeEventListener("pointermove", move);
		window.removeEventListener("pointerup", up);
		save();
		map.value.update_size();
	};
	window.addEventListener("pointermove", move);
	window.addEventListener("pointerup", up);
}

function run_menu(item) {
	open_menu.value = null;
	if (!item.disabled && item.action) item.action();
}

function on_key(e) {
	if (e.key === "Escape") {
		open_menu.value = null;
		about_open.value = false;
		clear_selection();
	}
}

let toast_id = 0;
function toast(text, kind = "info") {
	const id = ++toast_id;
	toasts.value.push({ id, text, kind });
	setTimeout(() => dismiss(id), kind === "error" ? 9000 : 5000);
}
function dismiss(id) {
	toasts.value = toasts.value.filter((t) => t.id !== id);
}

// Per-browser layout and layer list until GIS Projects arrive.
function read_saved() {
	try {
		return JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
	} catch (e) {
		return {};
	}
}
function save() {
	try {
		localStorage.setItem(
			STORAGE_KEY,
			JSON.stringify({
				panels,
				sizes,
				basemap: basemap_on.value,
				layers: layers.value.map((l) => ({ name: l.name, visible: l.visible, opacity: l.opacity, color: l.color })),
			})
		);
	} catch (e) {
		/* storage blocked: layout is not remembered */
	}
}

function clamp(v, min, max) {
	return Math.max(min, Math.min(max, v));
}
</script>
