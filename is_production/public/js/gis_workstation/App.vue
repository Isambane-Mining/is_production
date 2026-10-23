<template>
	<!-- Laid out like QGIS Desktop: menu bar, toolbars, dock panels around the map canvas, status bar. -->
	<div class="gis-app" :class="{ 'is-resizing': resizing }" @contextmenu="on_app_context">
		<MenuBar ref="menubar" :menus="menus" :project-title="current_project ? current_project.title : __('Unsaved map')" />
		<Toolbars :groups="toolbar_groups" />

		<div class="gis-body">
			<!-- left dock: Browser over Layers -->
			<aside v-if="panels.browser || panels.layers" class="gis-dock gis-dock-left" :style="{ width: sizes.left + 'px' }">
				<section v-if="panels.browser" class="gis-dock-pane" :style="{ flex: panels.layers ? `0 0 ${sizes.split}%` : '1 1 auto' }">
					<header class="gis-dock-head">
						<span class="gis-dock-title">{{ __("Browser") }}</span><span class="gis-spacer"></span>
						<button class="gis-icon-btn" :title="__('Close panel')" @click="panels.browser = false; save()"><Icon name="close" :size="14" /></button>
					</header>
					<BrowserPanel
						:datasets="datasets"
						:map-services="map_services"
						:layers="layers"
						:loading="loading_datasets"
						@add="(d) => add_layer(d)"
						@add-service="add_service"
						@refresh="load_datasets"
						@context="browser_context"
					/>
				</section>
				<div v-if="panels.browser && panels.layers" class="gis-splitter horizontal" @pointerdown="start_resize('split', $event)"></div>
				<section v-if="panels.layers" class="gis-dock-pane" style="flex: 1 1 auto">
					<header class="gis-dock-head">
						<span class="gis-dock-title">{{ __("Layers") }}</span><span class="gis-spacer"></span>
						<button class="gis-icon-btn" :title="__('Close panel')" @click="panels.layers = false; save()"><Icon name="close" :size="14" /></button>
					</header>
					<LayersPanel
						:layers="layers"
						:active="active_layer"
						:editing-name="editing ? editing.name : null"
						:legend-url="(name) => map && map.legend_url(name)"
						:map-services="map_services"
						:basemap="basemap"
						:basemap-opacity="basemap_opacity"
						@activate="activate_layer"
						@visible="set_visible"
						@context="layer_context"
						@reorder="reorder_layer"
						@drop-dataset="(name) => add_layer(datasets.find((d) => d.name === name))"
						@set-basemap="choose_basemap"
						@basemap-opacity="set_basemap_opacity"
					/>
				</section>
			</aside>
			<div v-if="panels.browser || panels.layers" class="gis-splitter vertical" @pointerdown="start_resize('left', $event)"></div>

			<!-- map canvas and the attribute table under it -->
			<main class="gis-center">
				<div ref="map_el" class="gis-map" :class="'tool-' + (editing && edit.tool !== 'none' ? 'edit' : tool)"></div>
				<template v-if="panels.table">
					<div class="gis-splitter horizontal" @pointerdown="start_resize('bottom', $event)"></div>
					<section class="gis-dock gis-dock-bottom" :style="{ height: sizes.bottom + 'px' }">
						<AttributeTable
							:layer="layer_by_name(active_layer)"
							:features="table_rows"
							:selected-ids="table_selected_ids"
							:editable="!!editing && editing.name === active_layer"
							:schema="editing && editing.name === active_layer ? edit_schema : null"
							@select="select_from_table"
							@edit="table_edit"
							@close="panels.table = false; save()"
						/>
					</section>
				</template>
			</main>

			<!-- right dock: Processing Toolbox, Identify Results, Processing History (tabs) -->
			<template v-if="right_panels.length">
				<div class="gis-splitter vertical" @pointerdown="start_resize('right', $event)"></div>
				<Dock side="right" :panels="right_panels" :active="right_tab" :style="{ width: sizes.right + 'px' }" @activate="(id) => (right_tab = id)" @close="close_panel">
					<template #toolbox>
						<ToolboxPanel :algorithms="algorithms" :loading="algorithms_loading" :qgis-version="qgis_version" @open="open_algorithm" />
					</template>
					<template #identify>
						<FeaturePanel
							:selection="selection_detail"
							:can-edit="config.can_edit_features"
							:editing="!!editing && edit_items.length > 0"
							:schema="edit_schema"
							:items="edit_items"
							:errors="edit.errors"
							@zoom="zoom_to_selection"
							@clear="clear_selection"
							@set-attribute="(id, field, value) => editing.set_attribute(id, field, value)"
							@set-label="(id, value) => editing.set_label(id, value)"
							@bulk-set="(field, value) => editing.bulk_set(edit.selected, field, value)"
						/>
					</template>
					<template #history>
						<HistoryPanel ref="history_panel" @open-job="(name) => open_algorithm(null, name)" />
					</template>
				</Dock>
			</template>
		</div>

		<StatusBar
			:message="status_message"
			:busy="busy"
			:coordinate="pointer_text"
			:scale="view_info.scale"
			:rotation="view_info.rotation"
			:crs="display_crs"
			:crs-options="crs_options"
			:crs-label="crs_label"
			:health="config.health"
			:health-link="config.can_manage_settings ? '/desk/gis-settings' : null"
			@go="go_to_text"
			@scale="(s) => map.set_scale(s)"
			@rotation="(r) => map.set_rotation(r)"
			@crs="change_crs"
		/>

		<ContextMenu ref="context_menu" />
		<input ref="color_input" type="color" class="gis-hidden-input" @change="color_chosen" />

		<!-- toasts -->
		<div class="gis-toasts">
			<div v-for="t in toasts" :key="t.id" class="gis-toast" :class="'is-' + t.kind" @click="dismiss(t.id)">{{ t.text }}</div>
		</div>

		<!-- dialogs -->
		<ImportDialog
			v-if="dialog === 'import' || dialog === 'import_project'"
			:datasets="datasets.filter((d) => d.spatial)"
			:project="dialog === 'import_project'"
			@close="dialog = null"
			@error="(m) => toast(m, 'error')"
			@add-datasets="add_imported"
			@open-project="open_project"
		/>
		<ExportDialog
			v-if="dialog === 'export' || dialog === 'export_project'"
			:active-layer="active_layer"
			:active-title="(layer_by_name(active_layer) || {}).title"
			:layer-names="layers.filter((l) => l.kind !== 'service').map((l) => l.name)"
			:selected-ids="export_selection"
			:project="dialog === 'export_project' ? current_project && current_project.name : null"
			:display-crs="display_crs"
			@close="dialog = null"
		/>
		<ProjectDialog v-if="dialog === 'open'" @close="dialog = null" @open="open_project" />
		<AlgorithmDialog
			v-if="algorithm_dialog"
			:key="algorithm_dialog.key"
			:algorithm-id="algorithm_dialog.id"
			:job-name="algorithm_dialog.job"
			:datasets="datasets"
			:active-layer="active_layer"
			:selected-ids="export_selection"
			:display-crs="display_crs"
			:map-extent="map_extent_text"
			:map-centre="map_centre_text"
			@close="algorithm_dialog = null; history_panel && history_panel.load()"
			@add-datasets="add_imported"
			@datasets-changed="load_datasets"
		/>
		<PluginManager v-if="dialog === 'plugins'" @close="dialog = null" @changed="plugins_changed" @message="(m) => toast(m, 'info')" />

		<div v-if="dialog === 'shortcuts'" class="gis-modal-backdrop" @click.self="dialog = null">
			<div class="gis-modal">
				<h3>{{ __("Keyboard Shortcuts") }}</h3>
				<table class="gis-kv small">
					<tbody><tr v-for="s in SHORTCUTS" :key="s[0]"><th class="mono">{{ s[0] }}</th><td>{{ s[1] }}</td></tr></tbody>
				</table>
				<div class="gis-modal-actions"><button @click="dialog = null">{{ __("Close") }}</button></div>
			</div>
		</div>

		<div v-if="dialog === 'about'" class="gis-modal-backdrop" @click.self="dialog = null">
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
				<p>{{ __("This workstation is an independent application laid out like QGIS Desktop. It is not an official QGIS product and is not endorsed by or affiliated with the QGIS project.") }}</p>
				<p>{{ __("This app (Isambane Production) is free software under the GNU General Public License, version 3 or later.") }}</p>
				<p class="small">{{ __("Toolbar and menu icons: GIS icons by Robert Szczepanek and Anita Graser, from the QGIS project, under CC-BY-SA 3.0 (see /assets/is_production/qgis_icons/).") }}</p>
				<p class="muted">OpenLayers (BSD-2-Clause) · proj4js (MIT) · Lucide interface icons (ISC) · GDAL/OGR, PROJ (MIT) · {{ __("Map data and imagery: see the attribution on the map") }}</p>
				<div class="gis-modal-actions"><button @click="dialog = null">{{ __("Close") }}</button></div>
			</div>
		</div>
	</div>
</template>

<script setup>
import { computed, markRaw, nextTick, onBeforeUnmount, onMounted, reactive, ref, shallowRef, watch } from "vue";
import { call } from "./api.js";
import { crs_label, format_coordinate, from_display, load_crs } from "./crs.js";
import { GisMap } from "./map.js";
import { base_kind, typed } from "./edit.js";
import AlgorithmDialog from "./AlgorithmDialog.vue";
import AttributeTable from "./AttributeTable.vue";
import BrowserPanel from "./BrowserPanel.vue";
import ContextMenu from "./ContextMenu.vue";
import Dock from "./Dock.vue";
import ExportDialog from "./ExportDialog.vue";
import FeaturePanel from "./FeaturePanel.vue";
import HistoryPanel from "./HistoryPanel.vue";
import Icon from "./Icon.vue";
import ImportDialog from "./ImportDialog.vue";
import LayersPanel from "./LayersPanel.vue";
import MenuBar from "./MenuBar.vue";
import PluginManager from "./PluginManager.vue";
import ProjectDialog from "./ProjectDialog.vue";
import StatusBar from "./StatusBar.vue";
import ToolboxPanel from "./ToolboxPanel.vue";
import Toolbars from "./Toolbars.vue";

const __ = window.__;
const STORAGE_KEY = "isp-gis-workstation-v2";
const EDIT_SVC = "is_production.geo_planning.services.gis_edit_service.";
const PROC = "is_production.geo_planning.services.gis_processing_service.";

// QGIS Desktop's own shortcuts where it has them
const SHORTCUTS = [
	["Ctrl+N", __("New Project")], ["Ctrl+O", __("Open Project")], ["Ctrl+S", __("Save Project")], ["Ctrl+Shift+S", __("Save Project As")],
	["Ctrl+Shift+I", __("Identify Features")], ["Ctrl+Shift+F", __("Zoom Full")], ["Ctrl+J", __("Zoom to Selection")], ["F5", __("Refresh")],
	["F6", __("Open Attribute Table")], ["Ctrl+Alt+T", __("Processing Toolbox")], ["Ctrl+Shift+A", __("Deselect Features from All Layers")],
	["Ctrl+.", __("Add Feature")], ["Del", __("Delete Selected")], ["Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y", __("Undo / Redo")], ["Ctrl+D", __("Remove Layer")],
	["Ctrl++ / Ctrl+-", __("Zoom In / Zoom Out")], ["Esc", __("Cancel / clear selection")],
];
// Vector / Raster menus: QGIS's menu entries, each opening its Processing algorithm
const VECTOR_MENU = [
	[__("Analysis Tools"), [["native:countpointsinpolygon", __("Count Points in Polygon")], ["native:lineintersections", __("Line Intersections")], ["native:sumlinelengths", __("Sum Line Lengths")], ["native:nearestneighbouranalysis", __("Nearest Neighbour Analysis")], ["native:basicstatisticsforfields", __("Basic Statistics for Fields")], ["native:meancoordinates", __("Mean Coordinate(s)")]]],
	[__("Research Tools"), [["native:creategrid", __("Create Grid")], ["native:randompointsinextent", __("Random Points in Extent")], ["native:randompointsinpolygons", __("Random Points in Polygons")], ["native:extractbylocation", __("Extract by Location")], ["native:polygonfromlayerextent", __("Extract Layer Extent")], ["native:regularpoints", __("Regular Points")]]],
	[__("Geoprocessing Tools"), [["native:buffer", __("Buffer")], ["native:clip", __("Clip")], ["native:convexhull", __("Convex Hull")], ["native:difference", __("Difference")], ["native:dissolve", __("Dissolve")], ["native:intersection", __("Intersection")], ["native:symmetricaldifference", __("Symmetrical Difference")], ["native:union", __("Union")]]],
	[__("Geometry Tools"), [["native:centroids", __("Centroids")], ["native:collect", __("Collect Geometries")], ["native:extractvertices", __("Extract Vertices")], ["native:multiparttosingleparts", __("Multipart to Singleparts")], ["native:polygonstolines", __("Polygons to Lines")], ["native:linestopolygons", __("Lines to Polygons")], ["native:simplifygeometries", __("Simplify")], ["native:densifygeometries", __("Densify by Count")], ["native:voronoipolygons", __("Voronoi Polygons")], ["native:delaunaytriangulation", __("Delaunay Triangulation")], ["native:fixgeometries", __("Fix Geometries")], ["qgis:checkvalidity", __("Check Validity")]]],
	[__("Data Management Tools"), [["native:reprojectlayer", __("Reproject Layer")], ["native:mergevectorlayers", __("Merge Vector Layers")], ["native:joinattributestable", __("Join Attributes by Field Value")], ["native:joinattributesbylocation", __("Join Attributes by Location")]]],
];
const RASTER_MENU = [
	[__("Analysis"), [["gdal:aspect", __("Aspect")], ["gdal:hillshade", __("Hillshade")], ["gdal:slope", __("Slope")], ["gdal:roughness", __("Roughness")], ["gdal:contour", __("Contour")], ["gdal:proximity", __("Proximity (Raster Distance)")]]],
	[__("Conversion"), [["gdal:polygonize", __("Polygonize (Raster to Vector)")], ["gdal:rasterize", __("Rasterize (Vector to Raster)")], ["gdal:translate", __("Translate (Convert Format)")]]],
	[__("Extraction"), [["gdal:cliprasterbyextent", __("Clip Raster by Extent")], ["gdal:cliprasterbymasklayer", __("Clip Raster by Mask Layer")]]],
	[__("Miscellaneous"), [["gdal:merge", __("Merge")], ["gdal:buildvirtualraster", __("Build Virtual Raster")]]],
	[__("Interpolation"), [["qgis:heatmapkerneldensityestimation", __("Heatmap (Kernel Density Estimation)")]]],
];
const TOOLBARS = [
	["project", __("Project Toolbar")], ["data", __("Data Source Toolbar")], ["navigation", __("Map Navigation Toolbar")],
	["attributes", __("Attributes Toolbar")], ["digitizing", __("Digitizing Toolbar")], ["advanced", __("Advanced Digitizing Toolbar")],
	["processing", __("Processing / Plugins Toolbar")],
];

const props = defineProps({ config: { type: Object, required: true } });
const config = props.config;

const map_el = ref(null);
const map = shallowRef(null);
const menubar = ref(null);
const context_menu = ref(null);
const color_input = ref(null);
const history_panel = ref(null);
const datasets = ref([]);
const loading_datasets = ref(false);
const layers = ref([]); // top of the tree first: { name, title, kind, visible, opacity, color, renderer, state, count, dataset, service }
const active_layer = ref(null);
const selection = ref(null); // { dataset, id }: the feature shown in Identify Results
const selected_ids = ref([]); // selected features of `selection_layer` (Select tool), outside editing
const selection_layer = ref(null);
const selection_detail = ref(null);
const table_version = ref(0);
const tool = ref("pan");
const pointer = ref(null);
const view_info = reactive({ scale: 0, rotation: 0 });
const display_crs = ref(config.default_crs);
const extra_crs = ref([]);
const toasts = ref([]);
const can_back = ref(false);
const can_forward = ref(false);
const saved = read_saved();
let identified = null;
const panels = reactive(Object.assign({ browser: true, layers: true, toolbox: true, identify: true, history: false, table: false }, saved.panels));
const toolbars = reactive(Object.assign(Object.fromEntries(TOOLBARS.map(([id]) => [id, true])), saved.toolbars));
const sizes = reactive(Object.assign({ left: 280, right: 320, bottom: 240, split: 38 }, saved.sizes));
const right_tab = ref(saved.right_tab || "toolbox");
const resizing = ref(false);
const editing = shallowRef(null); // EditSession
const edit_schema = ref(null);
const edit_version = ref(0);
const edit = reactive({ tool: "select", kind: "Polygon", selected: [], counts: { adds: 0, updates: 0, deletes: 0, total: 0 }, can_undo: false, can_redo: false, undo_label: "", snap: true, trace: false, default_z: "", saving: false, errors: [] });
let renderer_before_edit = null;
const dialog = ref(null); // import | import_project | export | export_project | open | plugins | shortcuts | about
const algorithm_dialog = ref(null); // { id, job, key }
const current_project = ref(null);
const map_services = ref([]);
const basemap = ref(saved.basemap_service !== undefined ? saved.basemap_service : config.basemap);
const basemap_opacity = ref(saved.basemap_opacity ?? 1);
const algorithms = ref([]);
const algorithms_loading = ref(true);
const qgis_version = ref("");
const status_message = ref(__("Ready"));
let context_target = null;

// ---------------------------------------------------------------- derived state

const crs_options = computed(() => [...new Set([config.default_crs, "EPSG:4326", "EPSG:3857", ...extra_crs.value, display_crs.value])]);
const pointer_text = computed(() => (pointer.value ? format_coordinate(pointer.value, display_crs.value) : ""));
const busy = computed(() => layers.value.some((l) => l.state === "loading"));
const active = computed(() => layer_by_name(active_layer.value));
const can_start_edit = computed(() => !!(active.value && active.value.kind !== "service" && active.value.dataset.spatial && active.value.dataset.status !== "Archived"));
const algorithm_ids = computed(() => new Map(algorithms.value.map((a) => [a.id, a])));
const right_panels = computed(() =>
	[
		{ id: "toolbox", title: __("Processing Toolbox") },
		{ id: "identify", title: editing.value ? __("Edit Feature") : __("Identify Results") },
		{ id: "history", title: __("History") },
	].filter((p) => panels[p.id])
);
watch(right_panels, (list) => {
	if (list.length && !list.some((p) => p.id === right_tab.value)) right_tab.value = list[0].id;
});

const edit_items = computed(() => {
	edit_version.value;
	if (!editing.value) return [];
	return edit.selected
		.map((id) => editing.value.features.get(id))
		.filter(Boolean)
		.map((f) => ({ id: f.getId(), label: f.get("label"), attributes: f.get("attributes") || {}, revision: f.get("revision"), validation_status: f.get("validation_status"), validation_message: f.get("validation_message") }));
});
const export_selection = computed(() => {
	if (editing.value && editing.value.name === active_layer.value) return edit.selected.filter((id) => id > 0);
	return selection_layer.value === active_layer.value ? selected_ids.value : [];
});
const table_selected_ids = computed(() => {
	if (editing.value && editing.value.name === active_layer.value) return edit.selected;
	return selection_layer.value === active_layer.value ? selected_ids.value : [];
});
const table_rows = computed(() => {
	edit_version.value;
	table_version.value;
	return map.value && active_layer.value ? map.value.features(active_layer.value) : [];
});
const has_selection = computed(() => (editing.value ? edit.selected.length > 0 : selected_ids.value.length > 0));
const add_icon = computed(() => {
	const kind = edit_schema.value ? (edit_schema.value.geometry_type === "Geometry" ? edit.kind : base_kind(edit_schema.value.geometry_type)) : null;
	return { Point: "add-point", LineString: "add-line", Polygon: "add-polygon" }[kind] || "add-geometry";
});
const counts_text = computed(() => {
	const c = edit.counts;
	return c.total ? __("Unsaved: {0} added, {1} changed, {2} deleted", [c.adds, c.updates, c.deletes]) : __("No unsaved edits");
});

// ---------------------------------------------------------------- toolbars (QGIS groups)

const toolbar_groups = computed(() => {
	const e = editing.value;
	const s = edit_schema.value || {};
	const multi = /^Multi|^Geometry$/.test(s.geometry_type || "");
	const etool = (id) => !!e && edit.tool === id;
	const groups = [
		{ id: "project", title: __("Project"), items: [
			{ id: "new", icon: "file-new", title: __("New Project"), shortcut: "Ctrl+N", action: new_project },
			{ id: "open", icon: "folder-open", title: __("Open Project…"), shortcut: "Ctrl+O", action: () => (dialog.value = "open") },
			{ id: "save", icon: "save", title: __("Save Project"), shortcut: "Ctrl+S", action: () => save_project(false) },
			{ id: "qgis", icon: "package", title: __("Export Project to QGIS…"), disabled: !current_project.value, action: () => (dialog.value = "export_project") },
		] },
		{ id: "data", title: __("Data Source"), items: [
			{ id: "import", icon: "file-up", title: __("Import Dataset…"), action: () => (dialog.value = "import") },
			{ id: "export", icon: "file-down", title: __("Export…"), disabled: !layers.value.some((l) => l.kind !== "service"), action: () => (dialog.value = "export") },
		] },
		{ id: "navigation", title: __("Map Navigation"), items: [
			{ id: "pan", icon: "pan", title: __("Pan Map"), active: tool.value === "pan" && !(e && edit.tool !== "none"), action: () => set_map_tool("pan") },
			{ id: "zin", icon: "zoom-in", title: __("Zoom In (click or drag a box)"), shortcut: "Ctrl++", active: tool.value === "zoom_in" && !(e && edit.tool !== "none"), action: () => set_map_tool("zoom_in") },
			{ id: "zout", icon: "zoom-out", title: __("Zoom Out"), shortcut: "Ctrl+-", active: tool.value === "zoom_out" && !(e && edit.tool !== "none"), action: () => set_map_tool("zoom_out") },
			"|",
			{ id: "full", icon: "zoom-full", title: __("Zoom Full"), shortcut: "Ctrl+Shift+F", action: zoom_full },
			{ id: "zsel", icon: "zoom-selection", title: __("Zoom to Selection"), shortcut: "Ctrl+J", disabled: !has_selection.value && !selection.value, action: zoom_to_selection },
			{ id: "zlayer", icon: "zoom-layer", title: __("Zoom to Layer"), disabled: !active.value, action: () => zoom_to_layer(active_layer.value) },
			{ id: "last", icon: "arrow-left", title: __("Zoom Last"), disabled: !can_back.value, action: () => map.value.go(-1) },
			{ id: "next", icon: "arrow-right", title: __("Zoom Next"), disabled: !can_forward.value, action: () => map.value.go(1) },
			{ id: "refresh", icon: "refresh", title: __("Refresh"), shortcut: "F5", action: () => map.value.reload() },
		] },
		{ id: "attributes", title: __("Attributes"), items: [
			{ id: "identify", icon: "identify", title: __("Identify Features"), shortcut: "Ctrl+Shift+I", active: tool.value === "identify" && !(e && edit.tool !== "none"), action: () => set_map_tool("identify") },
			{ id: "select", icon: "select-box", title: __("Select Features by Area or Single Click"), active: (tool.value === "select" && !e) || etool("select"), action: () => (e ? editing.value.set_tool("select") : set_map_tool("select")) },
			{ id: "deselect", icon: "deselect", title: __("Deselect Features from All Layers"), shortcut: "Ctrl+Shift+A", disabled: !has_selection.value && !selection.value, action: clear_selection },
			{ id: "table", icon: "table", title: __("Open Attribute Table"), shortcut: "F6", active: panels.table, disabled: !active.value, action: open_table },
		] },
		{ id: "digitizing", title: __("Digitizing"), items: [
			{ id: "toggle", icon: e ? "edit-off" : "edit", title: e ? __("Stop Editing") : __("Toggle Editing"), active: !!e, disabled: !config.can_edit_features || (!e && !can_start_edit.value), action: toggle_edit },
			{ id: "saveedits", icon: "save-edits", title: __("Save Layer Edits") + (e ? ` — ${counts_text.value}` : ""), disabled: !e || !edit.counts.total || edit.saving, action: edit_save },
			"|",
			{ id: "add", icon: add_icon.value, title: __("Add Feature"), shortcut: "Ctrl+.", active: etool("add"), disabled: !e || !s.can_create, action: () => editing.value.set_tool("add") },
			{ id: "vertex", icon: "vertex", title: __("Vertex Tool (drag; click an edge to add; Alt+click to remove)"), active: etool("vertex"), disabled: !e || !s.can_write, action: () => editing.value.set_tool("vertex") },
			{ id: "move", icon: "move", title: __("Move Feature(s)"), active: etool("move"), disabled: !e || !s.can_write, action: () => editing.value.set_tool("move") },
			{ id: "delete", icon: "delete", title: __("Delete Selected"), shortcut: "Del", disabled: !e || !edit.selected.length, action: () => editing.value.delete_selected() },
			"|",
			{ id: "undo", icon: "undo", title: __("Undo") + (edit.undo_label ? `: ${edit.undo_label}` : ""), shortcut: "Ctrl+Z", disabled: !e || !edit.can_undo, action: () => editing.value.undo() },
			{ id: "redo", icon: "redo", title: __("Redo"), shortcut: "Ctrl+Shift+Z", disabled: !e || !edit.can_redo, action: () => editing.value.redo() },
		] },
		{ id: "advanced", title: __("Advanced Digitizing"), items: [
			{ id: "rotate", icon: "rotate", title: __("Rotate Feature(s)…"), disabled: !e || !edit.selected.length || !s.can_write, action: edit_rotate },
			{ id: "split", icon: "split", title: __("Split Features (draw the cutting line)"), active: etool("split"), disabled: !e || !s.can_write || !s.can_create || /Point/.test(s.geometry_type || ""), action: () => editing.value.set_tool("split") },
			{ id: "merge", icon: "merge", title: __("Merge Selected Features"), disabled: !e || edit.selected.length < 2 || !s.can_write, action: () => editing.value.merge_selected() },
			{ id: "dup", icon: "copy", title: __("Duplicate Features"), disabled: !e || !edit.selected.length || !s.can_create, action: () => editing.value.duplicate_selected() },
			{ id: "part", icon: "add-part", title: multi ? __("Add Part") : __("Add Part (Multi or Geometry datasets only)"), active: etool("add_part"), disabled: !e || !s.can_write || !multi, action: () => editing.value.set_tool("add_part") },
			"|",
			{ id: "snap", icon: "magnet", title: __("Snapping to visible layers"), active: !!e && edit.snap, disabled: !e, action: () => editing.value.set_snap(!edit.snap) },
			{ id: "trace", icon: "trace", title: __("Tracing along existing features"), active: !!e && edit.trace, disabled: !e, action: () => editing.value.set_trace(!edit.trace) },
			...(e && s.geometry_type === "Geometry"
				? [{ id: "kind", kind: "select", title: __("What Add Feature draws"), value: edit.kind, options: [{ value: "Point", label: __("Point") }, { value: "LineString", label: __("Line") }, { value: "Polygon", label: __("Polygon") }], change: (v) => ((edit.kind = v), editing.value.set_kind(v)) }]
				: []),
			...(e && s.has_z ? [{ id: "z", kind: "input", type: "number", label: "Z", title: __("Elevation for new vertices that cannot be interpolated"), value: edit.default_z, change: set_default_z }] : []),
			{ id: "validate", icon: "check", title: __("Validate edited geometries"), disabled: !e || !edit.counts.total || edit.saving, action: edit_validate },
			{ id: "discard", icon: "discard", title: __("Discard unsaved edits"), disabled: !e || !edit.counts.total, action: edit_discard },
		] },
		{ id: "processing", title: __("Processing and Plugins"), items: [
			{ id: "toolbox", icon: "processing", title: __("Processing Toolbox"), shortcut: "Ctrl+Alt+T", active: panels.toolbox, action: () => toggle_panel("toolbox") },
			{ id: "history", icon: "history", title: __("Processing History"), active: panels.history, action: () => toggle_panel("history") },
			{ id: "plugins", icon: "plugin", title: __("Manage and Install Plugins…"), action: () => (dialog.value = "plugins") },
		] },
	];
	return groups.map((g) => ({ ...g, visible: toolbars[g.id] }));
});

// ---------------------------------------------------------------- menus (QGIS menu bar)

function algorithm_items(sections) {
	return sections.map(([label, items]) => ({
		label,
		items: items.map(([id, title]) => {
			const a = algorithm_ids.value.get(id);
			return { label: title, disabled: !a || !a.supported, hint: !a ? __("not installed") : !a.supported ? __("not available") : "", action: () => open_algorithm(id) };
		}),
	}));
}

const menus = computed(() => [
	{ label: __("Project"), items: [
		{ label: __("New"), icon: "file-new", shortcut: "Ctrl+N", action: new_project },
		{ label: __("Open…"), icon: "folder-open", shortcut: "Ctrl+O", action: () => (dialog.value = "open") },
		"-",
		{ label: __("Save"), icon: "save", shortcut: "Ctrl+S", disabled: current_project.value && !current_project.value.can_write, action: () => save_project(false) },
		{ label: __("Save As…"), shortcut: "Ctrl+Shift+S", action: () => save_project(true) },
		"-",
		{ label: __("Import QGIS Project Package…"), icon: "file-up", action: () => (dialog.value = "import_project") },
		{ label: __("Export Project to QGIS…"), icon: "package", disabled: !current_project.value, hint: current_project.value ? "" : __("save first"), action: () => (dialog.value = "export_project") },
		"-",
		{ label: __("Properties…"), disabled: !current_project.value, action: () => window.open(`/desk/gis-project/${current_project.value.name}`, "_blank") },
	] },
	{ label: __("Edit"), items: [
		{ label: __("Undo"), icon: "undo", shortcut: "Ctrl+Z", disabled: !editing.value || !edit.can_undo, action: () => editing.value.undo() },
		{ label: __("Redo"), icon: "redo", shortcut: "Ctrl+Shift+Z", disabled: !editing.value || !edit.can_redo, action: () => editing.value.redo() },
		"-",
		{ label: __("Select"), items: [
			{ label: __("Select Features by Area or Single Click"), action: () => (editing.value ? editing.value.set_tool("select") : set_map_tool("select")) },
			{ label: __("Deselect Features from All Layers"), action: clear_selection },
		] },
		"-",
		{ label: __("Add Feature"), icon: add_icon.value, shortcut: "Ctrl+.", disabled: !editing.value || !edit_schema.value.can_create, action: () => editing.value.set_tool("add") },
		{ label: __("Vertex Tool"), icon: "vertex", disabled: !editing.value, action: () => editing.value.set_tool("vertex") },
		{ label: __("Move Feature(s)"), icon: "move", disabled: !editing.value, action: () => editing.value.set_tool("move") },
		{ label: __("Rotate Feature(s)…"), icon: "rotate", disabled: !editing.value || !edit.selected.length, action: edit_rotate },
		{ label: __("Delete Selected"), icon: "delete", shortcut: "Del", disabled: !editing.value || !edit.selected.length, action: () => editing.value.delete_selected() },
		"-",
		{ label: __("Split Features"), icon: "split", disabled: !editing.value, action: () => editing.value.set_tool("split") },
		{ label: __("Merge Selected Features"), icon: "merge", disabled: !editing.value || edit.selected.length < 2, action: () => editing.value.merge_selected() },
		{ label: __("Duplicate Features"), icon: "copy", disabled: !editing.value || !edit.selected.length, action: () => editing.value.duplicate_selected() },
		{ label: __("Add Part"), icon: "add-part", disabled: !editing.value, action: () => editing.value.set_tool("add_part") },
	] },
	{ label: __("View"), items: [
		{ label: __("Pan Map"), icon: "pan", checked: tool.value === "pan", action: () => set_map_tool("pan") },
		{ label: __("Zoom In"), icon: "zoom-in", shortcut: "Ctrl++", action: () => set_map_tool("zoom_in") },
		{ label: __("Zoom Out"), icon: "zoom-out", shortcut: "Ctrl+-", action: () => set_map_tool("zoom_out") },
		{ label: __("Identify Features"), icon: "identify", shortcut: "Ctrl+Shift+I", action: () => set_map_tool("identify") },
		"-",
		{ label: __("Zoom Full"), icon: "zoom-full", shortcut: "Ctrl+Shift+F", action: zoom_full },
		{ label: __("Zoom to Selection"), icon: "zoom-selection", shortcut: "Ctrl+J", action: zoom_to_selection },
		{ label: __("Zoom to Layer"), icon: "zoom-layer", disabled: !active.value, action: () => zoom_to_layer(active_layer.value) },
		{ label: __("Zoom Last"), icon: "arrow-left", disabled: !can_back.value, action: () => map.value.go(-1) },
		{ label: __("Zoom Next"), icon: "arrow-right", disabled: !can_forward.value, action: () => map.value.go(1) },
		{ label: __("Go to Coordinate…"), action: go_to_coordinate },
		{ label: __("Refresh"), icon: "refresh", shortcut: "F5", action: () => map.value.reload() },
		"-",
		{ label: __("Panels"), items: [
			{ label: __("Browser"), checked: panels.browser, action: () => toggle_panel("browser") },
			{ label: __("Layers"), checked: panels.layers, action: () => toggle_panel("layers") },
			{ label: __("Processing Toolbox"), checked: panels.toolbox, action: () => toggle_panel("toolbox") },
			{ label: __("Identify Results"), checked: panels.identify, action: () => toggle_panel("identify") },
			{ label: __("Processing History"), checked: panels.history, action: () => toggle_panel("history") },
			{ label: __("Attribute Table"), checked: panels.table, action: () => toggle_panel("table") },
		] },
		{ label: __("Toolbars"), items: TOOLBARS.map(([id, label]) => ({ label, checked: toolbars[id], action: () => ((toolbars[id] = !toolbars[id]), save()) })) },
		{ label: __("Basemap"), items: [
			...map_services.value.filter((s) => s.use_as === "Basemap" && !s.error).map((s) => ({ label: s.title, checked: basemap.value === s.name, action: () => choose_basemap(s.name) })),
			{ label: __("No Basemap"), checked: !basemap.value, action: () => choose_basemap(null) },
		] },
	] },
	{ label: __("Layer"), items: [
		{ label: __("Add Layer"), icon: "layers", items: [
			{ label: __("From the Browser panel (double-click or drag)"), action: () => toggle_panel("browser", true) },
			{ label: __("Import Dataset…"), action: () => (dialog.value = "import") },
			...map_services.value.filter((s) => s.use_as === "Overlay" && !s.error).map((s) => ({ label: __("Map Service: {0}", [s.title]), action: () => add_service(s) })),
		] },
		{ label: __("New Dataset…"), action: () => window.open("/desk/gis-dataset/new", "_blank") },
		"-",
		{ label: __("Open Attribute Table"), icon: "table", shortcut: "F6", disabled: !active.value, action: open_table },
		{ label: editing.value ? __("Stop Editing") : __("Toggle Editing"), icon: "edit", disabled: !config.can_edit_features || (!editing.value && !can_start_edit.value), action: toggle_edit },
		{ label: __("Save Layer Edits"), icon: "save-edits", disabled: !editing.value || !edit.counts.total, action: edit_save },
		"-",
		{ label: __("Export"), icon: "file-down", items: [
			{ label: __("Save Features As…"), disabled: !can_start_edit.value, action: () => (dialog.value = "export") },
			{ label: __("Save Selected Features As…"), disabled: !export_selection.value.length, action: () => (dialog.value = "export") },
		] },
		{ label: __("Remove Layer"), shortcut: "Ctrl+D", disabled: !active.value, action: () => remove_layer(active_layer.value) },
		"-",
		{ label: __("Layer Properties…"), disabled: !active.value || active.value.kind === "service", action: () => window.open(`/desk/gis-dataset/${encodeURIComponent(active_layer.value)}`, "_blank") },
	] },
	{ label: __("Settings"), items: [
		{ label: __("GIS Settings…"), icon: "settings", disabled: !config.can_manage_settings, action: () => window.open("/desk/gis-settings", "_blank") },
		{ label: __("Map Services…"), icon: "globe", disabled: !config.can_manage_map_services, action: () => window.open("/desk/gis-map-service", "_blank") },
		{ label: __("Plugin Settings…"), icon: "plugin", disabled: !config.can_manage_plugins, action: () => window.open("/desk/gis-plugin-settings", "_blank") },
		"-",
		{ label: __("Keyboard Shortcuts…"), action: () => (dialog.value = "shortcuts") },
	] },
	{ label: __("Plugins"), items: [
		{ label: __("Manage and Install Plugins…"), icon: "plugin", action: () => (dialog.value = "plugins") },
		"-",
		...(config.plugins.length ? config.plugins.map((p) => ({ label: `${p.title} ${p.version || ""}`, hint: p.status === "Error" ? __("error") : p.category.replace("QGIS ", ""), disabled: true })) : [{ label: __("No plugins enabled"), disabled: true }]),
	] },
	{ label: __("Vector"), items: algorithm_items(VECTOR_MENU) },
	{ label: __("Raster"), items: algorithm_items(RASTER_MENU) },
	{ label: __("Processing"), items: [
		{ label: __("Toolbox"), icon: "processing", shortcut: "Ctrl+Alt+T", checked: panels.toolbox, action: () => toggle_panel("toolbox") },
		{ label: __("History…"), icon: "history", checked: panels.history, action: () => toggle_panel("history") },
		"-",
		{ label: __("Refresh Algorithm List"), action: load_algorithms },
	] },
	{ label: __("Help"), items: [
		{ label: __("QGIS User Guide"), action: () => window.open("https://docs.qgis.org/latest/en/docs/user_manual/", "_blank") },
		{ label: __("Keyboard Shortcuts…"), action: () => (dialog.value = "shortcuts") },
		"-",
		{ label: __("About / Attributions…"), action: () => (dialog.value = "about") },
	] },
]);

// ---------------------------------------------------------------- startup

onMounted(async () => {
	map.value = markRaw(
		new GisMap(map_el.value, config, {
			pointer: (c) => (pointer.value = c),
			view: (v) => {
				view_info.scale = v.scale;
				view_info.rotation = v.rotation;
				update_history();
			},
			identify: (hit) => {
				identified = hit;
				if (hit) {
					selection_layer.value = hit.dataset;
					selected_ids.value = [hit.id];
					selection.value = { dataset: hit.dataset, id: hit.id };
					active_layer.value = hit.dataset;
					show_panel("identify");
				} else clear_selection();
			},
			select: (dataset, ids, additive) => {
				const current = additive && selection_layer.value === dataset ? selected_ids.value : [];
				const next = additive ? [...new Set([...current.filter((id) => !ids.includes(id)), ...ids.filter((id) => !current.includes(id))])] : ids;
				selection_layer.value = dataset;
				selected_ids.value = next;
				map.value.select(next.length ? { dataset, ids: next } : null);
				selection.value = next.length ? { dataset, id: next[next.length - 1] } : null;
				status_message.value = __("{0} feature(s) selected in {1}", [next.length, (layer_by_name(dataset) || {}).title || dataset]);
			},
			loading: (name, state, count) => {
				const layer = layer_by_name(name);
				if (layer) Object.assign(layer, { state, count: count ?? layer.count });
				if (state !== "loading") table_version.value++;
				if (state === "truncated" && layer) toast(__("{0}: only the first {1} features in this view are shown. Zoom in to see all.", [layer.title, config.max_features]), "warning");
			},
			error: (message) => toast(message, "error"),
			tool: (t) => (tool.value = t),
		})
	);
	map.value.set_tool("pan");
	try {
		await load_crs(config.default_crs);
	} catch (e) {
		toast(e.message, "error");
	}
	try {
		map_services.value = await call("is_production.geo_planning.services.gis_map_service.list_map_services");
	} catch (e) {
		toast(e.message, "error");
	}
	apply_basemap();
	await load_datasets();
	const wanted = new URLSearchParams(window.location.search).get("project") || saved.project;
	if (wanted) await open_project(wanted, { quiet: !new URLSearchParams(window.location.search).get("project") });
	else restore_layers();
	load_algorithms();
	window.addEventListener("keydown", on_key);
	window.addEventListener("beforeunload", warn_unsaved);
});

onBeforeUnmount(() => {
	window.removeEventListener("keydown", on_key);
	window.removeEventListener("beforeunload", warn_unsaved);
});

function warn_unsaved(e) {
	if (editing.value && edit.counts.total) {
		e.preventDefault();
		e.returnValue = "";
	}
}

async function load_datasets() {
	loading_datasets.value = true;
	try {
		datasets.value = await call("is_production.geo_planning.services.gis_workstation_service.list_datasets");
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

async function load_algorithms() {
	algorithms_loading.value = true;
	try {
		const data = await call(PROC + "list_algorithms");
		algorithms.value = data.algorithms;
		qgis_version.value = data.qgis_version;
	} catch (e) {
		/* the toolbox shows nothing; Processing may be unavailable for this user */
	} finally {
		algorithms_loading.value = false;
	}
}

function plugins_changed() {
	load_algorithms();
}

// ---------------------------------------------------------------- layers

function layer_by_name(name) {
	return layers.value.find((l) => l.name === name) || null;
}

function activate_layer(name) {
	active_layer.value = name;
	map.value.active = name;
}
watch(active_layer, (name) => map.value && (map.value.active = name));

async function add_layer(dataset, options = {}) {
	if (!dataset) return;
	if (layer_by_name(dataset.name)) return activate_layer(dataset.name);
	if (!dataset.spatial) return toast(__("{0} has no geometry to show on the map.", [dataset.title]), "warning");
	try {
		await load_crs(dataset.crs);
		if (!extra_crs.value.includes(dataset.crs)) extra_crs.value.push(dataset.crs);
	} catch (e) {
		/* the readout falls back to the other CRSs */
	}
	const layer = reactive({
		name: dataset.name, title: dataset.title, kind: "dataset",
		renderer: dataset.published && options.renderer !== "browser" ? "qgis" : "browser",
		visible: options.visible ?? true, opacity: options.opacity ?? 1, color: null, state: "loading", count: 0, dataset,
	});
	layers.value.unshift(layer);
	layer.color = map.value.add_layer(dataset, { visible: layer.visible, opacity: layer.opacity, color: options.color, renderer: layer.renderer });
	activate_layer(dataset.name);
	apply_order();
	if (!options.restoring && layers.value.length === 1) zoom_to_layer(dataset.name);
	save();
}

function add_service(service, options = {}) {
	const key = `service:${service.name}`;
	if (layer_by_name(key)) return;
	const layer = reactive({
		name: key, title: service.title, kind: "service", service, renderer: "browser",
		visible: options.visible ?? true, opacity: options.opacity ?? service.opacity ?? 1, color: null, state: "ready", count: 0,
		dataset: { name: key, title: service.title, spatial: false },
	});
	layers.value.unshift(layer);
	map.value.add_service_layer(key, service, { visible: layer.visible, opacity: layer.opacity });
	apply_order();
	save();
}

function remove_layer(name) {
	if (!name) return;
	if (editing.value && editing.value.name === name && !stop_edit()) return;
	map.value.remove_layer(name);
	layers.value = layers.value.filter((l) => l.name !== name);
	if (selection_layer.value === name) clear_selection();
	if (active_layer.value === name) active_layer.value = layers.value.length ? layers.value[0].name : null;
	save();
}

function set_visible(name, visible) {
	const layer = layer_by_name(name);
	layer.visible = visible;
	map.value.set_visible(name, visible);
	save();
}

function set_renderer(name, renderer) {
	layer_by_name(name).renderer = renderer;
	map.value.set_renderer(name, renderer);
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

function reorder_layer(name, index) {
	const list = layers.value.filter((l) => l.name !== name);
	list.splice(Math.min(index, list.length), 0, layer_by_name(name));
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
		if (item.kind === "service") {
			const service = map_services.value.find((s) => s.name === item.service && !s.error);
			if (service) add_service(service, item);
			continue;
		}
		const dataset = datasets.value.find((d) => d.name === item.name);
		if (dataset) add_layer(dataset, { ...item, restoring: true });
	}
	if (saved.active && layer_by_name(saved.active)) activate_layer(saved.active);
}

// ---------------------------------------------------------------- basemap

function apply_basemap() {
	const service = map_services.value.find((s) => s.name === basemap.value && !s.error) || null;
	map.value.set_basemap(service, basemap_opacity.value);
}

function choose_basemap(name) {
	basemap.value = name;
	apply_basemap();
	save();
}

function set_basemap_opacity(value) {
	basemap_opacity.value = value;
	map.value.set_basemap_opacity(value);
	save();
}

// ---------------------------------------------------------------- context menus

function layer_context(event, layer) {
	const percent = (p) => ({ label: `${p}%`, checked: Math.round(layer.opacity * 100) === p, action: () => set_opacity(layer.name, p / 100) });
	const opacity = { label: __("Opacity"), items: [100, 75, 50, 25].map(percent) };
	const order = { label: __("Move to Top"), action: () => reorder_layer(layer.name, 0) };
	const remove = { label: __("Remove Layer"), shortcut: "Ctrl+D", action: () => remove_layer(layer.name) };
	if (layer.kind === "service") return context_menu.value.show(event, [opacity, order, "-", remove]);
	const is_editing = editing.value && editing.value.name === layer.name;
	context_menu.value.show(event, [
		{ label: __("Zoom to Layer"), icon: "zoom-layer", action: () => zoom_to_layer(layer.name) },
		{ label: __("Zoom to Selection"), icon: "zoom-selection", disabled: !(selection_layer.value === layer.name && selected_ids.value.length), action: zoom_to_selection },
		"-",
		{ label: __("Open Attribute Table"), icon: "table", action: () => (activate_layer(layer.name), open_table()) },
		{ label: is_editing ? __("Stop Editing") : __("Toggle Editing"), icon: "edit", disabled: !config.can_edit_features || (!!editing.value && !is_editing), action: () => (activate_layer(layer.name), toggle_edit()) },
		"-",
		{ label: __("Export"), icon: "file-down", items: [
			{ label: __("Save Features As…"), action: () => (activate_layer(layer.name), (dialog.value = "export")) },
			{ label: __("Save Selected Features As…"), disabled: !(selection_layer.value === layer.name && selected_ids.value.length), action: () => (activate_layer(layer.name), (dialog.value = "export")) },
		] },
		{ label: __("Drawn By"), items: [
			{ label: __("QGIS Server"), checked: layer.renderer === "qgis", disabled: !layer.dataset.published || is_editing, action: () => set_renderer(layer.name, "qgis") },
			{ label: __("Browser"), checked: layer.renderer === "browser", action: () => set_renderer(layer.name, "browser") },
		] },
		opacity,
		{ label: __("Change Colour…"), disabled: layer.renderer !== "browser", action: () => pick_color(layer.name) },
		"-",
		order,
		remove,
		"-",
		{ label: __("Properties…"), icon: "settings", action: () => window.open(`/desk/gis-dataset/${encodeURIComponent(layer.name)}`, "_blank") },
	]);
}

function browser_context(event, { kind, item }) {
	if (kind === "service") return context_menu.value.show(event, [{ label: __("Add Layer to Map"), icon: "layers", action: () => add_service(item) }]);
	context_menu.value.show(event, [
		{ label: __("Add Layer to Map"), icon: "layers", disabled: !item.spatial, action: () => add_layer(item) },
		{ label: __("Export…"), icon: "file-down", action: async () => (await add_layer(item), activate_layer(item.name), (dialog.value = "export")) },
		"-",
		{ label: __("Properties…"), icon: "settings", action: () => window.open(`/desk/gis-dataset/${encodeURIComponent(item.name)}`, "_blank") },
	]);
}

function on_app_context(event) {
	// the map canvas keeps the browser's own menu off, as QGIS does
	if (map_el.value && map_el.value.contains(event.target)) event.preventDefault();
}

function pick_color(name) {
	context_target = name;
	color_input.value.value = layer_by_name(name).color || "#2f80ed";
	color_input.value.click();
}

function color_chosen(event) {
	if (context_target) set_color(context_target, event.target.value);
	context_target = null;
}

// ---------------------------------------------------------------- panels

function toggle_panel(id, force) {
	panels[id] = force ?? !panels[id];
	if (panels[id] && ["toolbox", "identify", "history"].includes(id)) right_tab.value = id;
	save();
	nextTick(() => map.value.update_size());
}

function show_panel(id) {
	panels[id] = true;
	right_tab.value = id;
}

function close_panel(id) {
	panels[id] = false;
	save();
	nextTick(() => map.value.update_size());
}

function open_table() {
	panels.table = true;
	save();
	nextTick(() => map.value.update_size());
}

// ---------------------------------------------------------------- map tools

function set_map_tool(name) {
	if (editing.value) editing.value.set_tool("none");
	map.value.set_tool(name);
	status_message.value = {
		pan: __("Pan: drag the map"),
		zoom_in: __("Zoom In: click, or drag a box"),
		zoom_out: __("Zoom Out: click, or drag a box"),
		identify: __("Identify: click a feature"),
		select: __("Select: click or drag a box on the active layer (Shift adds)"),
	}[name];
}

// ---------------------------------------------------------------- selection

watch(selection, (value) => {
	if (!editing.value) map.value && map.value.select(value && selection_layer.value === value.dataset ? { dataset: value.dataset, ids: selected_ids.value } : value);
	if (!value) return (selection_detail.value = null);
	const feature = map.value.find(value.dataset, value.id);
	const layer = layer_by_name(value.dataset);
	const base = { id: value.id, dataset: value.dataset, dataset_title: layer ? layer.title : value.dataset, crs: layer ? layer.dataset.crs : "" };
	if (feature) {
		selection_detail.value = { ...base, label: feature.get("label"), geometry_type: feature.get("geometry_type"), revision: feature.get("revision"), validation_status: feature.get("validation_status"), attributes: feature.get("attributes") || {} };
	} else if (identified && identified.dataset === value.dataset && identified.id === value.id && identified.properties) {
		const { name, feature_label, revision, validation_status, min_z, max_z, ...attributes } = identified.properties;
		selection_detail.value = { ...base, label: feature_label, geometry_type: layer ? layer.dataset.geometry_type : "", revision, validation_status, attributes };
	} else selection_detail.value = null;
});

function select_from_table(id, additive = false) {
	if (editing.value && editing.value.name === active_layer.value) {
		editing.value.select_ids([id], additive);
		if (!additive) map.value.zoom_to_feature(active_layer.value, id);
		return;
	}
	map.value.on.select(active_layer.value, [id], additive);
	if (!additive) map.value.zoom_to_feature(active_layer.value, id);
}

function zoom_to_selection() {
	const ids = editing.value ? edit.selected : selected_ids.value;
	const layer = editing.value ? editing.value.name : selection_layer.value;
	if (layer && ids.length) map.value.zoom_to_feature(layer, ids[ids.length - 1]);
	else if (selection.value) map.value.zoom_to_feature(selection.value.dataset, selection.value.id);
}

function clear_selection() {
	if (editing.value) editing.value.select_ids([]);
	selected_ids.value = [];
	selection_layer.value = null;
	selection.value = null;
	map.value && map.value.select(null);
}

function table_edit(id, key, value) {
	if (!editing.value) return;
	if (key === "label") return editing.value.set_label(id, value);
	const attribute = edit_schema.value.attributes.find((a) => a.field_name === key);
	editing.value.set_attribute(id, key, attribute ? typed(attribute, value) : value);
}

// ---------------------------------------------------------------- projects

function clear_map() {
	if (editing.value && !stop_edit()) return false;
	for (const layer of [...layers.value]) remove_layer(layer.name);
	return true;
}

function new_project() {
	if (layers.value.length && !window.confirm(__("Start a new, empty map? Unsaved changes to the layer list are lost."))) return;
	if (!clear_map()) return;
	current_project.value = null;
	save();
}

async function open_project(name, { quiet = false } = {}) {
	dialog.value = null;
	let project;
	try {
		project = await call("is_production.geo_planning.services.gis_project_service.get_project", { name });
	} catch (e) {
		if (!quiet) toast(e.message, "error");
		current_project.value = null;
		restore_layers();
		return;
	}
	if (!clear_map()) return;
	await load_datasets();
	current_project.value = { name: project.name, title: project.title, can_write: project.can_write };
	if (project.display_crs) {
		try {
			await load_crs(project.display_crs);
			display_crs.value = project.display_crs;
		} catch (e) {
			/* keep the current display CRS */
		}
	}
	basemap.value = project.basemap || null;
	apply_basemap();
	for (const item of [...project.layers].reverse()) {
		if (item.map_service) {
			const service = map_services.value.find((s) => s.name === item.map_service);
			if (service) add_service(service, { visible: !!item.visible, opacity: item.opacity });
			continue;
		}
		const dataset = datasets.value.find((d) => d.name === item.dataset);
		if (dataset) await add_layer(dataset, { ...item, visible: !!item.visible, restoring: true });
	}
	if (project.extent) map.value.fit(project.extent, 22);
	else zoom_full();
	if (project.hidden_layers) toast(__("{0} layer(s) of this project are hidden: you cannot read their datasets.", [project.hidden_layers]), "warning");
	status_message.value = __("Opened project {0}", [project.title]);
	save();
}

async function save_project(as_new) {
	let title = current_project.value && !as_new ? current_project.value.title : null;
	if (!title) {
		title = window.prompt(__("Project title:"), current_project.value ? __("{0} (copy)", [current_project.value.title]) : "");
		if (!title) return;
	}
	const view = map.value.map.getView();
	try {
		const result = await call("is_production.geo_planning.services.gis_project_service.save_project", {
			name: as_new || !current_project.value ? null : current_project.value.name,
			title,
			display_crs: display_crs.value,
			basemap: basemap.value,
			extent: view.calculateExtent(map.value.map.getSize()),
			layers: layers.value.map((l) =>
				l.kind === "service"
					? { map_service: l.service.name, visible: l.visible ? 1 : 0, opacity: l.opacity }
					: { dataset: l.name, visible: l.visible ? 1 : 0, opacity: l.opacity, color: l.color, renderer: l.renderer }
			),
		});
		current_project.value = { name: result.name, title: result.title, can_write: true };
		toast(__("Project {0} saved.", [result.title]), "info");
		save();
	} catch (e) {
		toast(e.message, "error");
	}
}

async function add_imported(names) {
	await load_datasets();
	for (const name of names) {
		const dataset = datasets.value.find((d) => d.name === name);
		if (dataset) await add_layer(dataset);
	}
	if (names.length) zoom_to_layer(names[0]);
}

// ---------------------------------------------------------------- processing

function open_algorithm(id, job = null) {
	algorithm_dialog.value = { id, job, key: `${id || ""}:${job || ""}:${Date.now()}` };
}

function map_extent_text() {
	const [xmin, ymin, xmax, ymax] = map.value.map.getView().calculateExtent(map.value.map.getSize());
	return `${xmin.toFixed(2)},${xmax.toFixed(2)},${ymin.toFixed(2)},${ymax.toFixed(2)} [EPSG:3857]`;
}

function map_centre_text() {
	const [x, y] = map.value.map.getView().getCenter();
	return `${x.toFixed(2)},${y.toFixed(2)} [EPSG:3857]`;
}

// ---------------------------------------------------------------- editing

async function toggle_edit() {
	if (editing.value) return stop_edit();
	const name = active_layer.value;
	let schema;
	try {
		schema = await call(EDIT_SVC + "get_edit_schema", { dataset: name });
	} catch (e) {
		return toast(e.message, "error");
	}
	if (!schema.can_create && !schema.can_write && !schema.can_delete) return toast(__("You cannot edit this dataset."), "error");
	const layer = layer_by_name(name);
	renderer_before_edit = layer.renderer;
	if (layer.renderer !== "browser") set_renderer(name, "browser"); // QGIS cannot show unsaved edits
	if (!layer.visible) set_visible(name, true);
	clear_selection();
	edit_schema.value = schema;
	Object.assign(edit, { tool: "select", selected: [], errors: [], saving: false, default_z: "" });
	editing.value = markRaw(
		map.value.start_edit(name, schema, {
			change: refresh_edit,
			selection: (ids) => {
				edit.selected = ids;
				selection.value = ids.length ? { dataset: name, id: ids[ids.length - 1] } : null;
				edit_version.value++;
			},
			message: (text, kind) => toast(text, kind),
		})
	);
	edit.kind = editing.value.kind;
	map.value.set_tool("pan");
	show_panel("identify");
	status_message.value = __("Editing {0}", [layer.title]);
	refresh_edit();
}

function stop_edit() {
	const session = editing.value;
	if (!session) return true;
	if (edit.counts.total && !window.confirm(__("Discard {0} unsaved changes?", [edit.counts.total]))) return false;
	session.discard();
	const name = session.name;
	map.value.stop_edit();
	editing.value = null;
	edit_schema.value = null;
	edit.selected = [];
	if (renderer_before_edit && layer_by_name(name)) set_renderer(name, renderer_before_edit);
	renderer_before_edit = null;
	table_version.value++;
	status_message.value = __("Ready");
	return true;
}

function refresh_edit() {
	const s = editing.value;
	if (!s) return;
	Object.assign(edit, {
		tool: s.tool, snap: s.snap_on, trace: s.trace_on, counts: s.counts(),
		can_undo: s.undo_stack.length > 0, can_redo: s.redo_stack.length > 0,
		undo_label: s.undo_stack.length ? s.undo_stack[s.undo_stack.length - 1].label : "",
	});
	edit_version.value++;
	table_version.value++;
}

function set_default_z(value) {
	edit.default_z = value;
	editing.value.default_z = value;
}

function edit_rotate() {
	const text = window.prompt(__("Rotate the selection by how many degrees (clockwise)?"), "90");
	if (text === null) return;
	const degrees = Number(text);
	if (!Number.isFinite(degrees)) return toast(__("Enter a number of degrees."), "error");
	editing.value.rotate_selected(degrees);
}

async function edit_validate() {
	try {
		const results = await editing.value.validate();
		const invalid = results.filter((r) => !r.valid);
		edit.errors = invalid.map((r) => ({ ref: r.ref, message: r.message }));
		toast(invalid.length ? __("{0} of {1} edited geometries are invalid.", [invalid.length, results.length]) : __("All {0} edited geometries are valid.", [results.length]), invalid.length ? "warning" : "info");
		if (invalid.length) editing.value.select_ids(invalid.map((r) => Number(r.ref)));
	} catch (e) {
		toast(e.message, "error");
	}
}

async function edit_save() {
	edit.saving = true;
	try {
		const result = await editing.value.save();
		if (result.nothing) return toast(__("Nothing to save."), "info");
		if (result.ok) {
			edit.errors = [];
			toast(__("Saved: {0} added, {1} changed, {2} deleted.", [Object.keys(result.created).length, result.updated.length, result.deleted.length]), "info");
			map.value.reload();
			load_datasets();
		} else {
			edit.errors = result.errors;
			show_panel("identify");
			const conflicts = result.errors.filter((e) => e.conflict).length;
			toast(__("Nothing was saved: {0} change(s) failed{1}. See Identify Results.", [result.errors.length, conflicts ? __(" ({0} conflicts with other users' edits)", [conflicts]) : ""]), "error");
		}
	} catch (e) {
		toast(e.message, "error");
	} finally {
		edit.saving = false;
		refresh_edit();
	}
}

function edit_discard() {
	if (!window.confirm(__("Discard {0} unsaved changes?", [edit.counts.total]))) return;
	editing.value.discard();
	edit.errors = [];
	map.value.reload();
}

// ---------------------------------------------------------------- view, CRS, coordinates

function update_history() {
	nextTick(() => {
		can_back.value = map.value.can_go(-1);
		can_forward.value = map.value.can_go(1);
	});
}

async function change_crs(code) {
	try {
		const definition = await load_crs(code);
		display_crs.value = definition.code;
		if (!extra_crs.value.includes(definition.code)) extra_crs.value.push(definition.code);
	} catch (e) {
		toast(e.message, "error");
	}
}

function go_to_text(text) {
	const xy = String(text || "").replace(/°/g, "").split(/[\s,;]+/).filter(Boolean).map(Number);
	const target = xy.length === 2 && xy.every(Number.isFinite) ? from_display(xy, display_crs.value) : null;
	if (!target || !target.every(Number.isFinite)) return toast(__("Enter two numbers, x and y."), "error");
	map.value.go_to(target);
}

function go_to_coordinate() {
	const text = window.prompt(__("Go to coordinate in {0} (x, y):", [crs_label(display_crs.value)]));
	if (text) go_to_text(text);
}

function start_resize(panel, event) {
	event.preventDefault();
	resizing.value = true;
	const start = { x: event.clientX, y: event.clientY, size: sizes[panel] };
	const dock = event.target.parentElement;
	const move = (e) => {
		if (panel === "left") sizes.left = clamp(start.size + e.clientX - start.x, 200, 600);
		if (panel === "right") sizes.right = clamp(start.size - (e.clientX - start.x), 220, 700);
		if (panel === "bottom") sizes.bottom = clamp(start.size - (e.clientY - start.y), 100, window.innerHeight - 200);
		if (panel === "split") sizes.split = clamp(start.size + ((e.clientY - start.y) / dock.clientHeight) * 100, 15, 85);
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

// ---------------------------------------------------------------- keyboard (QGIS shortcuts)

function on_key(e) {
	const typing = /^(INPUT|TEXTAREA|SELECT)$/.test((e.target && e.target.tagName) || "");
	if (typing) return;
	const mod = e.ctrlKey || e.metaKey;
	const key = e.key.toLowerCase();
	const run = (fn) => (e.preventDefault(), fn());
	if (editing.value) {
		if (mod && key === "z" && !e.shiftKey) return run(() => editing.value.undo());
		if (mod && (key === "y" || (key === "z" && e.shiftKey))) return run(() => editing.value.redo());
		if (e.key === "Delete") return run(() => editing.value.delete_selected());
		if (mod && key === ".") return run(() => editing.value.set_tool("add"));
		if (e.key === "Escape") return run(() => (editing.value.cancel_sketch(), editing.value.select_ids([])));
	}
	if (mod && e.shiftKey && key === "s") return run(() => save_project(true));
	if (mod && e.shiftKey && key === "i") return run(() => set_map_tool("identify"));
	if (mod && e.shiftKey && key === "f") return run(zoom_full);
	if (mod && e.shiftKey && key === "a") return run(clear_selection);
	if (mod && e.altKey && key === "t") return run(() => toggle_panel("toolbox"));
	if (mod && !e.shiftKey && key === "n") return run(new_project);
	if (mod && !e.shiftKey && key === "o") return run(() => (dialog.value = "open"));
	if (mod && !e.shiftKey && key === "s") return run(() => save_project(false));
	if (mod && key === "j") return run(zoom_to_selection);
	if (mod && key === "d") return run(() => remove_layer(active_layer.value));
	if (mod && (key === "+" || key === "=")) return run(() => map.value.zoom_by(1));
	if (mod && key === "-") return run(() => map.value.zoom_by(-1));
	if (e.key === "F5") return run(() => map.value.reload());
	if (e.key === "F6") return run(open_table);
	if (e.key === "Escape") {
		menubar.value && menubar.value.close();
		context_menu.value && context_menu.value.close();
		if (dialog.value === "shortcuts" || dialog.value === "about") dialog.value = null;
		clear_selection();
	}
}

// ---------------------------------------------------------------- messages and saved layout

let toast_id = 0;
function toast(text, kind = "info") {
	const id = ++toast_id;
	toasts.value.push({ id, text, kind });
	status_message.value = text;
	setTimeout(() => dismiss(id), kind === "error" ? 9000 : 5000);
}
function dismiss(id) {
	toasts.value = toasts.value.filter((t) => t.id !== id);
}

// Per-browser layout (panels, toolbars, sizes) and the last map when it is not a saved project.
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
				panels, toolbars, sizes, right_tab: right_tab.value,
				basemap_service: basemap.value, basemap_opacity: basemap_opacity.value,
				project: current_project.value ? current_project.value.name : null,
				active: active_layer.value,
				layers: layers.value.map((l) => ({ kind: l.kind, service: l.service ? l.service.name : undefined, name: l.name, visible: l.visible, opacity: l.opacity, color: l.color, renderer: l.renderer })),
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
