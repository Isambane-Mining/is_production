<template>
	<div class="cad-app" @keydown="on_key" tabindex="-1" ref="root">
		<MenuBar :menus="menus" @command="run">
			<span class="cad-model-title" v-if="model">
				<b>{{ model.model_name }}</b>
				<span class="muted"> · {{ model.name }} · {{ model.crs || __("no CRS") }} · {{ model.linear_units }}</span>
				<span v-if="working_label" class="cad-chip warning">{{ working_label }}</span>
				<span v-if="info.in_review" class="cad-chip info">{{ __("In review") }}</span>
				<span v-if="!info.can_edit" class="cad-chip">{{ __("Read only") }}</span>
			</span>
			<span v-if="!config.worker_enabled" class="cad-chip error">{{ __("CAD worker off") }}</span>
		</MenuBar>

		<div class="cad-toolbar">
			<button :title="__('Save (Ctrl+S)')" :disabled="!model || !change_count || saving" @click="run('save')"><Icon name="save" /></button>
			<button :title="__('Undo (Ctrl+Z)')" :disabled="!can_undo" @click="run('undo')"><Icon name="undo" /></button>
			<button :title="__('Redo (Ctrl+Y)')" :disabled="!can_redo" @click="run('redo')"><Icon name="redo" /></button>
			<span class="cad-sep"></span>
			<button :class="{ on: !tool_name }" :title="__('Select (Esc) — drag left→right: window, right→left: crossing')" @click="run('cancel')"><Icon name="select" /></button>
			<button v-for="t in toolbar.draw" :key="t" :class="{ on: tool_name === t }" :title="TOOLS[t].label" :disabled="!editable" @click="run('tool', t)"><Icon :name="t" /></button>
			<span class="cad-sep"></span>
			<button v-for="t in toolbar.modify" :key="t" :class="{ on: tool_name === t }" :title="TOOLS[t].label" :disabled="!editable" @click="run('tool', t)"><Icon :name="t" /></button>
			<button :title="__('Erase (Del)')" :disabled="!editable || !selection.length" @click="run('erase')"><Icon name="erase" /></button>
			<span class="cad-sep"></span>
			<button v-for="t in toolbar.annotate" :key="t" :class="{ on: tool_name === t }" :title="TOOLS[t].label" :disabled="!editable" @click="run('tool', t)"><Icon :name="t" /></button>
			<span class="cad-sep"></span>
			<button v-for="t in ['box', 'cylinder', 'extrude']" :key="t" :class="{ on: tool_name === t }" :title="TOOLS[t].label" :disabled="!editable || !config.worker_enabled" @click="run('tool', t)"><Icon :name="t" /></button>
			<span class="cad-sep"></span>
			<button v-for="t in ['distance', 'area', 'id']" :key="t" :class="{ on: tool_name === t }" :title="TOOLS[t].label" :disabled="!model" @click="run('tool', t)"><Icon :name="t" /></button>
			<span class="cad-spacer"></span>
			<button :class="{ on: settings.object_snap }" :title="__('Object Snap (F3) — Settings → Object Snaps')" @click="run('toggle', 'object_snap')"><Icon name="snap" /> <span class="small">OSNAP</span></button>
			<button :class="{ on: settings.grid_snap }" :title="__('Grid Snap (F9)')" @click="run('toggle', 'grid_snap')"><Icon name="grid" /> <span class="small">GRID</span></button>
			<button :class="{ on: settings.ortho }" :title="__('Ortho (F8)')" @click="run('toggle', 'ortho')"><Icon name="ortho" /> <span class="small">ORTHO</span></button>
			<button :class="{ on: settings.polar }" :title="__('Polar tracking (F10), every {0}°', [settings.polar_angle])" @click="run('toggle', 'polar')"><span class="small">POLAR</span></button>
			<button :class="{ on: settings.lineweights }" :title="__('Show line weights')" @click="run('toggle_lineweights')"><Icon name="lineweight" /> <span class="small">LWT</span></button>
			<span class="cad-sep"></span>
			<button :class="{ on: view_mode === '2d' }" @click="run('mode', '2d')">2D</button>
			<button :class="{ on: view_mode === '3d' }" @click="run('mode', '3d')">3D</button>
			<button :title="__('Zoom Extents')" :disabled="!model" @click="run('zoom_extents')"><Icon name="zoom_extents" /></button>
			<button :title="__('Zoom Selection')" :disabled="!selection.length" @click="run('zoom_selection')"><Icon name="zoom_selection" /></button>
		</div>

		<div class="cad-main">
			<aside class="cad-left">
				<div class="cad-tabs">
					<button v-for="tab in left_tabs" :key="tab.id" :class="{ on: left_tab === tab.id }" @click="left_tab = tab.id">{{ tab.label }}</button>
				</div>
				<ModelPanel v-show="left_tab === 'models'" ref="model_panel" :config="config" :current="model && model.name" @open="open_model" @error="error" />
				<template v-if="model">
					<div v-show="left_tab === 'layers'" class="cad-scroll">
						<LayerPanel :layers="layer_rows" :current="current_layer_key" :can_edit="editable" @change="change_layer" @current="(k) => (current_layer_key = k)" />
						<ReferencePanel :references="references" :model="model.name" :can_edit="editable" @change="change_references" @error="error" />
					</div>
					<BlockPanel
						v-show="left_tab === 'blocks'"
						:blocks="block_rows"
						:current="current_block"
						:can_edit="editable"
						:has_selection="selection.length > 0"
						:uses="block_uses"
						@current="(b) => (current_block = b)"
						@insert="(b) => ((current_block = b), run('tool', 'block_insert'))"
						@create="run('tool', 'block_create')"
					/>
					<EntityPanel v-show="left_tab === 'entities'" :rows="entity_rows" :layers="session_layers" :selected="selection" :dirty="dirty_ids" @select="select_ids" @zoom="zoom_ids" />
					<RevisionPanel
						v-show="left_tab === 'revisions'"
						:revisions="info.revisions || []"
						:can_edit="info.can_edit"
						:can_approve="info.can_approve"
						:in_review="info.in_review"
						:unsaved="change_count > 0"
						@submit="submit_revision"
						@approve="approve_revision"
						@reject="reject_revision"
						@restore="restore_revision"
					/>
				</template>
				<div v-else-if="left_tab !== 'models'" class="muted pad">{{ __("Open a model first.") }}</div>
			</aside>

			<section class="cad-viewport-wrap">
				<div ref="viewport_el" class="cad-viewport" @pointermove="on_pointer_move" @pointerdown="on_pointer_down" @pointerup="on_pointer_up" @contextmenu.prevent="on_context" @pointerleave="cursor = null"></div>
				<div v-if="band" class="cad-band" :class="{ crossing: band.crossing }" :style="band_style"></div>
				<div v-if="!model" class="cad-empty">
					<h3>{{ __("CAD Engineering Workstation") }}</h3>
					<p class="muted">{{ __("Open or create a model in the Models panel.") }}</p>
					<p v-if="config.health && config.health.status && config.health.status !== 'OK'" class="is-warning small">
						{{ __("Dependency check: {0}", [config.health.status]) }} — {{ config.health.summary }}
					</p>
				</div>
				<div v-if="loading" class="cad-loading">{{ loading }}</div>
				<div v-if="tool_prompt" class="cad-prompt">{{ tool_prompt }}</div>
			</section>

			<aside class="cad-right">
				<h4 class="cad-subhead">{{ __("Properties") }}</h4>
				<PropertiesPanel
					v-if="model"
					:entities="selected_entities"
					:layers="session_layers"
					:can_edit="editable"
					:decimals="decimals"
					:angular_decimals="config.angular_precision"
					:dirty_ids="dirty_ids"
					@update="update_entities"
					@update_geometry="update_geometry"
					@error="error"
				/>
				<h4 class="cad-subhead">{{ __("Coordinates") }}</h4>
				<div class="cad-props">
					<div class="cad-prop"><span>{{ __("Cursor") }}</span><span class="mono">{{ cursor ? fmt_point(cursor) : "—" }}</span></div>
					<div class="cad-prop"><span>{{ __("Snap") }}</span><span>{{ snap_kind || "—" }}</span></div>
					<div class="cad-prop"><span>{{ __("Last point") }}</span><span class="mono">{{ last_point ? fmt_point(last_point) : "—" }}</span></div>
					<div class="cad-prop">
						<span>{{ __("Work plane Z") }}</span>
						<input class="mono" :value="fmt(settings.elevation)" @change="set_elevation($event.target.value)" />
					</div>
					<div class="cad-prop"><span>{{ __("Grid") }}</span><span class="mono">{{ grid_step ? fmt(grid_step) : "—" }}</span></div>
					<template v-if="model">
						<div class="cad-prop"><span>{{ __("Local origin") }}</span><span class="mono">{{ fmt_point(origin) }}</span></div>
						<div class="cad-prop"><span>{{ __("Plan view") }}</span><span>{{ info.plan_rotated ? __("Turned 180° (westing/southing)") : __("X east, Y north") }}</span></div>
						<div class="cad-prop"><span>{{ __("Annotation") }}</span><span>1:{{ model.annotation_scale || 1000 }} · {{ __("text") }} {{ fmt(settings.text_height) }}</span></div>
						<div class="cad-prop"><span>{{ __("Vertical datum") }}</span><span>{{ model.vertical_datum || __("not recorded") }}</span></div>
					</template>
				</div>
			</aside>
		</div>

		<div class="cad-bottom">
			<div class="cad-messages" ref="messages_el">
				<div v-for="(m, i) in messages" :key="i" :class="'cad-msg-' + m.level"><span class="muted">{{ m.time }}</span> {{ m.text }}</div>
			</div>
			<div class="cad-command">
				<span class="cad-command-prompt">{{ tool_prompt || __("Command") }}:</span>
				<input
					ref="command_el"
					v-model="command_text"
					class="mono cad-grow"
					spellcheck="false"
					autocomplete="off"
					:placeholder="tool_name ? __('x,y[,z]  @dx,dy  @dist<angle  a number, or text') : __('L PL REC POL C A EL SPL XL T MT DAL DLI DAN H M CO RO SC MI S AR O TR EX BR J X F CHA B I E BOX CYL EXT DI AA ANG U SAVE…')"
					@keydown.enter.prevent="submit_command"
					@keydown.esc.prevent="run('cancel')"
				/>
			</div>
		</div>

		<footer class="cad-status">
			<span class="mono">{{ cursor ? fmt_point(cursor) : "" }}</span>
			<span>{{ view_mode === "2d" ? __("Plan") : projection === "perspective" ? __("3D perspective") : __("3D orthographic") }}</span>
			<span v-if="model">{{ entity_rows.length }} {{ __("entities") }}</span>
			<span v-if="selection.length">{{ selection.length }} {{ __("selected") }}</span>
			<span v-if="change_count" class="is-warning">{{ __("{0} unsaved change(s)", [change_count]) }}</span>
			<span v-if="job" :class="{ 'is-error': job.status === 'Failed' }">{{ job.job_type }}: {{ job.status_message || job.status }} {{ job.progress ? `(${job.progress}%)` : "" }}</span>
			<span class="cad-spacer"></span>
			<span class="muted">OCCT {{ (config.health && config.health.occt_version) || "?" }}</span>
		</footer>

		<ImportDialog v-if="dialog === 'import'" :model="model" @close="close_import" @error="error" />
		<ExportDialog v-if="dialog === 'export'" :model="model" :revisions="info.revisions || []" @close="dialog = null" @error="error" />
	</div>
</template>

<script setup>
import { computed, markRaw, nextTick, onBeforeUnmount, onMounted, reactive, ref, shallowRef } from "vue";

import { call, get_mesh, wait_for_job } from "./api";
import BlockPanel from "./BlockPanel.vue";
import { ALIASES, parse_input } from "./command";
import EntityPanel from "./EntityPanel.vue";
import ExportDialog from "./ExportDialog.vue";
import { entity_extent, format_number, format_point, union } from "./geometry";
import Icon from "./Icon.vue";
import ImportDialog from "./ImportDialog.vue";
import LayerPanel from "./LayerPanel.vue";
import MenuBar from "./MenuBar.vue";
import ModelPanel from "./ModelPanel.vue";
import PropertiesPanel from "./PropertiesPanel.vue";
import ReferencePanel from "./ReferencePanel.vue";
import RevisionPanel from "./RevisionPanel.vue";
import { EditSession } from "./session";
import { load_font } from "./text";
import { ToolController, TOOLS } from "./tools";
import { Viewport } from "./viewport";

const props = defineProps({ config: { type: Object, required: true } });
const config = props.config;
const PREFS_KEY = "isp-cad-workstation-v2";
const SNAP_KINDS = ["endpoint", "midpoint", "centre", "quadrant", "intersection", "perpendicular", "tangent", "nearest", "node", "insertion"];

const root = ref(null);
const viewport_el = ref(null);
const command_el = ref(null);
const messages_el = ref(null);
const model_panel = ref(null);

const session = markRaw(new EditSession());
let viewport = null;
let tools = null;

const tick = ref(0); // bumped whenever the session changes; derived lists depend on it
const model = ref(null);
const info = ref({});
const selection = ref([]);
const left_tab = ref("models");
const current_layer_key = ref(null);
const current_block = ref(null);
const cursor = ref(null);
const snap_kind = ref(null);
const last_point = ref(null);
const messages = ref([]);
const command_text = ref("");
const loading = ref("");
const saving = ref(false);
const job = shallowRef(null);
const view_mode = ref("2d");
const projection = ref("orthographic");
const grid_step = ref(null);
const origin = ref([0, 0, 0]);
const tool_state = ref(0);
const last_command = ref(null);
const dialog = ref(null);
const band = ref(null);
const view_history = [];

const settings = reactive({
	object_snap: true,
	snaps: ["endpoint", "midpoint", "centre", "quadrant", "intersection", "perpendicular", "node", "insertion"],
	grid_snap: false,
	ortho: false,
	polar: false,
	polar_angle: 15,
	aperture: 12,
	show_grid: true,
	lineweights: false,
	elevation: 0,
	text_height: 2.5,
	decimals: config.coordinate_precision ?? 3,
});
load_prefs();

const decimals = computed(() => settings.decimals);
const toolbar = {
	draw: ["point", "line", "polyline", "rectangle", "polygon", "circle", "arc", "ellipse", "spline", "text", "mtext"],
	modify: ["move", "copy", "rotate", "scale", "mirror", "offset", "trim", "extend", "fillet"],
	annotate: ["dim_aligned", "dim_linear", "dim_angular", "dim_radius", "hatch"],
};
const left_tabs = [
	{ id: "models", label: __("Models") },
	{ id: "layers", label: __("Layers") },
	{ id: "blocks", label: __("Blocks") },
	{ id: "entities", label: __("Entities") },
	{ id: "revisions", label: __("Revisions") },
];

const editable = computed(() => Boolean(model.value && info.value.can_edit && !info.value.in_review));
const entity_rows = computed(() => (tick.value, [...session.entities.values()]));
const session_layers = computed(() => (tick.value, new Map(session.layers)));
const layer_rows = computed(() => (tick.value, [...session.layers.entries()].map(([key, l]) => ({ ...l, name: l.name || key }))));
const block_rows = computed(() => (tick.value, [...session.blocks.values()]));
const block_uses = computed(() => {
	tick.value;
	const uses = {};
	for (const e of session.entities.values()) if (e.entity_type === "Block Reference") uses[e.geometry.block] = (uses[e.geometry.block] || 0) + 1;
	return uses;
});
const references = computed(() => (tick.value, session.references));
const dirty_ids = computed(() => (tick.value, new Set(session.dirty)));
const change_count = computed(() => (tick.value, session.change_count));
const can_undo = computed(() => (tick.value, session.undo_stack.length > 0));
const can_redo = computed(() => (tick.value, session.redo_stack.length > 0));
const selected_entities = computed(() => (tick.value, selection.value.map((id) => session.entities.get(id)).filter(Boolean)));
const tool_name = computed(() => (tool_state.value, tools && tools.name));
const tool_prompt = computed(() => (tool_state.value, tools && tools.prompt));
const working_label = computed(() => {
	const r = (info.value.revisions || []).find((x) => x.revision_status === "Working");
	return r ? __("Working {0}", [r.revision_label]) : "";
});
const band_style = computed(() => {
	const b = band.value;
	if (!b) return {};
	return { left: `${Math.min(b.x0, b.x1)}px`, top: `${Math.min(b.y0, b.y1)}px`, width: `${Math.abs(b.x1 - b.x0)}px`, height: `${Math.abs(b.y1 - b.y0)}px` };
});

// --------------------------------------------------------------------- menus

const later = (phase) => ({ disabled: true, phase });
const tool_item = (t, enabled) => ({ label: TOOLS[t].label, command: "tool", args: t, disabled: !enabled });
const menus = computed(() => {
	const has = Boolean(model.value);
	const ed = editable.value;
	const solid_ok = ed && config.worker_enabled;
	return [
		{
			label: __("File"),
			items: [
				{ label: __("New / Open Model…"), command: "show_models" },
				{ label: __("Save"), command: "save", key: "Ctrl+S", disabled: !has || !change_count.value },
				{ label: __("Reload from Server"), command: "reload", disabled: !has },
				"-",
				{ label: __("Import DXF…"), command: "import", disabled: !ed || !config.worker_enabled },
				{ label: __("Export DXF…"), command: "export", disabled: !has || !config.worker_enabled },
				{ label: __("Import LandXML / STL / OBJ / STEP…"), ...later(__("Phase 3")) },
				"-",
				{ label: __("Validate Model (Open CASCADE)"), command: "validate", disabled: !has || !info.value.can_edit || !config.worker_enabled },
				{ label: __("Submit Revision for Review…"), command: "show_revisions", disabled: !has },
				"-",
				{ label: __("Open Model in Desk"), command: "desk", disabled: !has },
				{ label: __("Import / Export History"), command: "desk_list", args: "CAD Import Export" },
			],
		},
		{
			label: __("Edit"),
			items: [
				{ label: __("Undo"), command: "undo", key: "Ctrl+Z", disabled: !can_undo.value },
				{ label: __("Redo"), command: "redo", key: "Ctrl+Y", disabled: !can_redo.value },
				"-",
				{ label: __("Select All"), command: "select_all", key: "Ctrl+A", disabled: !has },
				{ label: __("Select Same Type and Layer"), command: "select_similar", disabled: selection.value.length !== 1 },
				{ label: __("Clear Selection"), command: "cancel", key: "Esc" },
				{ label: __("Erase"), command: "erase", key: "Del", disabled: !ed || !selection.value.length },
			],
		},
		{
			label: __("View"),
			items: [
				{ label: __("2D Plan"), command: "mode", args: "2d", checked: view_mode.value === "2d" },
				{ label: __("3D View"), command: "mode", args: "3d", checked: view_mode.value === "3d" },
				{ label: __("Perspective"), command: "projection", checked: projection.value === "perspective", disabled: view_mode.value !== "3d" },
				"-",
				{ label: __("Top"), command: "view", args: "top" },
				{ label: __("Front"), command: "view", args: "front" },
				{ label: __("Back"), command: "view", args: "back" },
				{ label: __("Left"), command: "view", args: "left" },
				{ label: __("Right"), command: "view", args: "right" },
				{ label: __("Isometric (SE)"), command: "view", args: "iso" },
				{ label: __("Isometric (SW)"), command: "view", args: "iso_sw" },
				"-",
				{ label: __("Zoom Extents"), command: "zoom_extents", disabled: !has },
				{ label: __("Zoom Selection"), command: "zoom_selection", disabled: !selection.value.length },
				{ label: __("Previous View"), command: "previous_view", disabled: !view_history.length },
				{ label: __("Grid"), command: "toggle_grid", key: "F7", checked: settings.show_grid },
				{ label: __("Line Weights"), command: "toggle_lineweights", checked: settings.lineweights },
				"-",
				{ label: __("Section View"), ...later(__("Phase 3")) },
				{ label: __("Split Views"), ...later(__("Phase 3")) },
			],
		},
		{
			label: __("Draw"),
			items: [
				...["point", "line", "polyline", "rectangle", "polygon", "circle", "arc", "arc3", "ellipse", "spline", "xline"].map((t) => tool_item(t, ed)),
				"-",
				...["text", "mtext"].map((t) => tool_item(t, ed)),
				"-",
				...["dim_aligned", "dim_linear", "dim_angular", "dim_radius", "dim_diameter"].map((t) => tool_item(t, ed)),
				tool_item("hatch", ed),
				"-",
				tool_item("block_create", ed),
				tool_item("block_insert", ed),
			],
		},
		{
			label: __("Modify"),
			items: [
				...["move", "copy", "rotate", "scale", "mirror", "stretch"].map((t) => tool_item(t, ed)),
				"-",
				...["offset", "trim", "extend", "break", "join", "fillet", "chamfer", "explode"].map((t) => tool_item(t, ed)),
				"-",
				tool_item("array_rect", ed),
				tool_item("array_polar", ed),
				{ label: __("Erase"), command: "erase", disabled: !ed || !selection.value.length },
			],
		},
		{
			label: __("Surface"),
			items: [__("TIN from Points / Breaklines"), __("Contours"), __("Cross-Sections"), __("Cut and Fill Volumes")].map((label) => ({ label, ...later(__("Phase 3")) })),
		},
		{
			label: __("Solid"),
			items: [
				tool_item("box", solid_ok),
				tool_item("cylinder", solid_ok),
				tool_item("extrude", solid_ok),
				"-",
				...[__("Union"), __("Subtract"), __("Intersect"), __("Revolve"), __("Loft"), __("Sweep")].map((label) => ({ label, ...later(__("Phase 3")) })),
			],
		},
		{
			label: __("Mining"),
			items: [__("Pit Design"), __("Bench Generation"), __("Haul Road Alignment"), __("Mining Blocks"), __("Drill Pattern Layout")].map((label) => ({ label, ...later(__("Phase 4")) })),
		},
		{
			label: __("Tools"),
			items: [
				...["distance", "area", "angle", "id"].map((t) => tool_item(t, has)),
				"-",
				{ label: __("Processing Jobs"), command: "desk_list", args: "CAD Processing Job" },
				{ label: __("GIS Workstation"), command: "open_url", args: "/portal_gis" },
			],
		},
		{
			label: __("Settings"),
			items: [
				{ label: __("Object Snap"), command: "toggle", args: "object_snap", key: "F3", checked: settings.object_snap },
				...SNAP_KINDS.map((k) => ({ label: `   ${__(k[0].toUpperCase() + k.slice(1))}`, command: "toggle_snap", args: k, checked: settings.snaps.includes(k), disabled: !settings.object_snap })),
				{ label: __("Snap Aperture (pixels)…"), command: "ask_setting", args: "aperture" },
				"-",
				{ label: __("Grid Snap"), command: "toggle", args: "grid_snap", key: "F9", checked: settings.grid_snap },
				{ label: __("Ortho"), command: "toggle", args: "ortho", key: "F8", checked: settings.ortho },
				{ label: __("Polar Tracking"), command: "toggle", args: "polar", key: "F10", checked: settings.polar },
				{ label: __("Polar Angle (degrees)…"), command: "ask_setting", args: "polar_angle" },
				"-",
				{ label: __("Text Height…"), command: "ask_setting", args: "text_height" },
				{ label: __("Display Decimals…"), command: "ask_setting", args: "decimals" },
				"-",
				{ label: __("CAD Settings"), command: "desk_settings", disabled: !config.can_manage_settings },
			],
		},
		{
			label: __("Help"),
			items: [
				{ label: __("Commands and Coordinate Input"), command: "help" },
				{ label: __("About and Licences"), command: "about" },
			],
		},
	];
});

// --------------------------------------------------------------------- messages

function message(text, level = "info") {
	const time = new Date().toTimeString().slice(0, 8);
	messages.value.push({ text, level, time });
	if (messages.value.length > 300) messages.value.splice(0, messages.value.length - 300);
	nextTick(() => messages_el.value && (messages_el.value.scrollTop = messages_el.value.scrollHeight));
}

function error(text) {
	message(text, "error");
}

function fmt(v) {
	return format_number(v, settings.decimals);
}

function fmt_point(p) {
	return format_point(p, settings.decimals);
}

// --------------------------------------------------------------------- commands

async function run(command, args) {
	try {
		switch (command) {
			case "tool":
				if (!model.value) return error(__("Open a model first."));
				if (tools.start(args)) last_command.value = args;
				break;
			case "cancel":
				tools.cancel(true);
				select_ids([]);
				break;
			case "save":
				return await save();
			case "undo": {
				tools.cancel(true);
				const label = session.undo();
				if (label) message(__("Undone: {0}", [label]));
				break;
			}
			case "redo": {
				const label = session.redo();
				if (label) message(__("Redone: {0}", [label]));
				break;
			}
			case "erase":
				erase();
				break;
			case "select_all":
				select_ids(visible_entities().filter((e) => !session.is_locked(e)).map((e) => e.entity_id));
				break;
			case "select_similar": {
				const e = session.entities.get(selection.value[0]);
				select_ids([...session.entities.values()].filter((x) => x.entity_type === e.entity_type && x.cad_layer === e.cad_layer).map((x) => x.entity_id));
				break;
			}
			case "mode":
				view_mode.value = args;
				viewport.set_mode(args);
				break;
			case "projection":
				projection.value = projection.value === "perspective" ? "orthographic" : "perspective";
				viewport.set_projection(projection.value);
				break;
			case "view":
				push_view();
				if (args !== "top") view_mode.value = "3d";
				viewport.set_view(args); // a non-plan view switches the viewport to 3D itself
				break;
			case "zoom_extents":
				push_view();
				viewport.zoom_to(model_extent());
				break;
			case "zoom_selection":
				zoom_ids(selection.value);
				break;
			case "previous_view":
				previous_view();
				break;
			case "toggle_grid":
				settings.show_grid = !settings.show_grid;
				viewport.set_grid_visible(settings.show_grid);
				break;
			case "toggle_lineweights":
				settings.lineweights = !settings.lineweights;
				viewport.show_lineweights = settings.lineweights;
				viewport.sync(session);
				save_prefs();
				break;
			case "toggle":
				settings[args] = !settings[args];
				if (args === "ortho" && settings.ortho) settings.polar = false;
				if (args === "polar" && settings.polar) settings.ortho = false;
				message(`${args.replace("_", " ")}: ${settings[args] ? __("on") : __("off")}`);
				save_prefs();
				break;
			case "toggle_snap": {
				const set = new Set(settings.snaps);
				set.has(args) ? set.delete(args) : set.add(args);
				settings.snaps = [...set];
				save_prefs();
				break;
			}
			case "ask_setting": {
				const labels = { decimals: __("Display decimals (0–9)"), text_height: __("Text height (model units)"), aperture: __("Snap aperture (pixels)"), polar_angle: __("Polar tracking angle (degrees)") };
				const value = window.prompt(labels[args], settings[args]);
				const n = Number(value);
				if (value !== null && Number.isFinite(n) && n > 0) settings[args] = args === "decimals" ? Math.min(9, Math.round(n)) : n;
				viewport.decimals = settings.decimals;
				save_prefs();
				break;
			}
			case "reload":
				if (change_count.value && !window.confirm(__("Discard {0} unsaved change(s)?", [change_count.value]))) return;
				return await open_model(model.value.name, true);
			case "validate": {
				const started = await call("validate_model", { model: model.value.name });
				follow_job(started, true);
				break;
			}
			case "import":
				if (!model.value) return error(__("Open a model first."));
				if (change_count.value) return error(__("Save your changes before importing."));
				dialog.value = "import";
				break;
			case "export":
				if (!model.value) return error(__("Open a model first."));
				if (change_count.value) message(__("Unsaved changes are not exported."), "warning");
				dialog.value = "export";
				break;
			case "show_models":
				left_tab.value = "models";
				break;
			case "show_revisions":
				left_tab.value = "revisions";
				break;
			case "desk":
				window.open(`/desk/cad-model/${encodeURIComponent(model.value.name)}`, "_blank");
				break;
			case "desk_list":
				window.open(`/desk/${args.toLowerCase().replace(/ /g, "-")}`, "_blank");
				break;
			case "desk_settings":
				window.open("/desk/cad-settings", "_blank");
				break;
			case "open_url":
				window.open(args, "_blank");
				break;
			case "help":
				show_help();
				break;
			case "about":
				show_about();
				break;
			default:
				if (TOOLS[command]) tools.start(command);
		}
	} catch (e) {
		error(e.message);
	} finally {
		tool_state.value++;
		tick.value++;
	}
}

function submit_command() {
	const text = command_text.value;
	command_text.value = "";
	if (!model.value && text.trim()) return error(__("Open a model first."));
	if (tools.active) {
		message(`${tools.prompt}: ${text}`, "echo");
		const parsed = parse_input(text, { last: tools.last_point, cursor: cursor.value, elevation: settings.elevation });
		let result;
		if (parsed.kind === "empty") result = tools.finish();
		else if (parsed.kind === "point") {
			ensure_origin(parsed.point);
			last_point.value = parsed.point;
			result = tools.input_point(parsed.point);
		} else if (parsed.kind === "number") result = tools.input_number(parsed.value, parsed.point);
		else if (parsed.kind === "text") result = tools.input_text(parsed.text);
		else error(parsed.message);
		Promise.resolve(result).finally(() => {
			tool_state.value++;
			tick.value++;
		});
		return;
	}
	const word = text.trim().toUpperCase();
	if (!word) {
		if (last_command.value) run("tool", last_command.value);
		return;
	}
	const command = ALIASES[word];
	message(`${__("Command")}: ${word}`, "echo");
	if (!command) return error(__("Unknown command {0}. See Help → Commands.", [word]));
	if (TOOLS[command]) run("tool", command);
	else run(command);
}

function on_key(event) {
	const in_input = ["INPUT", "SELECT", "TEXTAREA"].includes(event.target.tagName) && event.target !== command_el.value;
	const ctrl = event.ctrlKey || event.metaKey;
	if (ctrl && event.key.toLowerCase() === "s") return event.preventDefault(), run("save");
	if (in_input || dialog.value) return;
	if (ctrl && event.key.toLowerCase() === "z") return event.preventDefault(), run("undo");
	if (ctrl && event.key.toLowerCase() === "y") return event.preventDefault(), run("redo");
	if (ctrl && event.key.toLowerCase() === "a" && event.target !== command_el.value) return event.preventDefault(), run("select_all");
	if (event.key === "Escape") return run("cancel");
	if (event.key === "F3") return event.preventDefault(), run("toggle", "object_snap");
	if (event.key === "F7") return event.preventDefault(), run("toggle_grid");
	if (event.key === "F8") return event.preventDefault(), run("toggle", "ortho");
	if (event.key === "F9") return event.preventDefault(), run("toggle", "grid_snap");
	if (event.key === "F10") return event.preventDefault(), run("toggle", "polar");
	if (event.key === "Delete" && event.target !== command_el.value) return run("erase");
	// typing anywhere goes to the command line
	if (event.target !== command_el.value && event.key.length === 1 && !ctrl && !event.altKey) command_el.value.focus();
}

// --------------------------------------------------------------------- pointer

let down = null;

function snap_options() {
	return {
		kinds: settings.object_snap ? new Set(settings.snaps) : new Set(),
		grid_snap: settings.grid_snap,
		grid: viewport.grid_step || 1,
		from: tools.last_point,
		aperture: settings.aperture,
	};
}

function local_xy(event) {
	const rect = viewport_el.value.getBoundingClientRect();
	return [event.clientX - rect.left, event.clientY - rect.top];
}

function on_pointer_move(event) {
	if (!model.value) return;
	if (down && down.button === 0 && !tools.active) {
		const [x, y] = local_xy(event);
		if (band.value || Math.hypot(x - down.x, y - down.y) > 4) band.value = { x0: down.x, y0: down.y, x1: x, y1: y, crossing: x < down.x };
		return;
	}
	const snapped = viewport.snap(event, session, snap_options());
	if (!snapped) return;
	cursor.value = snapped.point;
	snap_kind.value = snapped.kind;
	tools.hover(snapped.point, snapped.kind ? snapped : null);
	tool_state.value++;
}

function on_pointer_down(event) {
	const [x, y] = local_xy(event);
	down = { x, y, button: event.button };
	root.value.focus({ preventScroll: true });
}

function on_pointer_up(event) {
	if (!down || event.button !== 0 || !model.value) {
		if (event.button !== 2) down = null;
		return;
	}
	const [x, y] = local_xy(event);
	const moved = Math.hypot(x - down.x, y - down.y) > 4;
	down = null;
	if (band.value) {
		const b = band.value;
		band.value = null;
		const ids = viewport.select_in_rect(session, [Math.min(b.x0, b.x1), Math.min(b.y0, b.y1), Math.max(b.x0, b.x1), Math.max(b.y0, b.y1)], b.crossing);
		select_ids(ids, event.shiftKey);
		message(__("{0}: {1} object(s).", [b.crossing ? __("Crossing selection") : __("Window selection"), ids.length]));
		return;
	}
	if (moved) return;
	if (tools.active) {
		const snapped = viewport.snap(event, session, snap_options());
		if (!snapped) return;
		const point = tools.constrain(snapped.point);
		let result;
		if (tools.wants_pick) {
			const id = viewport.pick(event);
			if (!id) return message(__("Nothing there to pick."), "warning");
			result = tools.input_pick(id, viewport.cursor_point(event) || point);
		} else {
			last_point.value = point;
			ensure_origin(point);
			result = tools.input_point(point);
		}
		Promise.resolve(result).finally(() => {
			tool_state.value++;
			tick.value++;
		});
		tool_state.value++;
		return;
	}
	const id = viewport.pick(event);
	if (id) select_ids([id], event.shiftKey);
	else if (!event.shiftKey) select_ids([]);
}

function on_context(event) {
	// a right-click without dragging = Enter (right-drag pans or orbits)
	const [x, y] = local_xy(event);
	const was = down;
	down = null;
	if (was && Math.hypot(x - was.x, y - was.y) > 4) return;
	if (tools.active) {
		Promise.resolve(tools.finish()).finally(() => {
			tool_state.value++;
			tick.value++;
		});
	}
}

// --------------------------------------------------------------------- selection

function select_ids(ids, add = false) {
	let next = ids;
	if (add) {
		const set = new Set(selection.value);
		for (const id of ids) set.has(id) ? set.delete(id) : set.add(id);
		next = [...set];
	}
	selection.value = next;
	viewport.set_selection(next, session);
}

function zoom_ids(ids) {
	const boxes = ids.map((id) => session.entities.get(id)).filter(Boolean).map((e) => entity_extent(e, viewport.blocks));
	const box = union(boxes);
	if (!box) return;
	push_view();
	viewport.zoom_to(box);
	select_ids(ids);
}

function visible_entities() {
	return [...session.entities.values()].filter((e) => {
		const layer = session.layer(e.cad_layer) || session.layer_by_name(e.cad_layer);
		return !layer || layer.visible;
	});
}

function model_extent() {
	const boxes = [...session.entities.values()].filter((e) => e.entity_type !== "Construction Line").map((e) => entity_extent(e, viewport.blocks));
	const box = union(boxes);
	if (box) return box;
	const o = origin.value;
	return [o[0] - 50, o[1] - 50, o[2], o[0] + 50, o[1] + 50, o[2]];
}

function push_view() {
	view_history.push({ position: viewport.camera.position.clone(), target: viewport.controls.target.clone(), zoom: viewport.camera.zoom, mode: view_mode.value });
	if (view_history.length > 30) view_history.shift();
}

function previous_view() {
	const v = view_history.pop();
	if (!v) return;
	if (v.mode !== view_mode.value) run("mode", v.mode);
	viewport.camera.position.copy(v.position);
	viewport.controls.target.copy(v.target);
	viewport.camera.zoom = v.zoom;
	viewport.camera.updateProjectionMatrix();
	viewport.controls.update();
}

// --------------------------------------------------------------------- precision

// A new model has no local origin until its first save. As soon as geometry far from 0,0,0 appears
// (typed, clicked or drawn), draw relative to it, or float32 buffers would round survey coordinates.
const FAR = 10000;

function ensure_origin(point) {
	if (!point || !model.value) return;
	const o = origin.value;
	if (Math.max(Math.abs(point[0] - o[0]), Math.abs(point[1] - o[1]), Math.abs(point[2] - o[2])) < FAR) return;
	const next = [Math.round(point[0] / 100) * 100, Math.round(point[1] / 100) * 100, Math.round(point[2] / 10) * 10];
	if (!session.entities.size || (session.entities.size === 1 && !settings.elevation)) {
		settings.elevation = point[2]; // the first geometry also sets the work plane
		viewport.elevation = point[2];
	}
	origin.value = next;
	viewport.rebase(next);
	viewport.sync(session);
}

// --------------------------------------------------------------------- editing

function erase() {
	const ids = selection.value.filter((id) => session.entities.has(id));
	const locked = ids.filter((id) => session.is_locked(session.entities.get(id)));
	if (locked.length) return error(__("{0} object(s) are on locked layers.", [locked.length]));
	if (!ids.length) return;
	session.apply(__("Erase"), (w) => ids.forEach((id) => w.remove(id)));
	select_ids([]);
	message(__("Erased {0} object(s).", [ids.length]));
}

function update_entities(ids, values) {
	session.apply(__("Properties"), (w) => {
		for (const id of ids) {
			const e = session.entities.get(id);
			if (e && !session.is_locked(e)) w.put({ ...e, ...values });
		}
	});
	tick.value++;
}

function update_geometry(id, geometry) {
	const e = session.entities.get(id);
	session.apply(__("Edit geometry"), (w) => w.put({ ...e, geometry, validation_status: "Not Checked" }));
	tick.value++;
}

function change_layer(key, values, view_only = false) {
	if (view_only && !editable.value) {
		// visibility is also a viewing preference: apply locally without saving when read-only
		const layer = session.layers.get(key);
		if (layer) Object.assign(layer, values);
	} else session.set_layer(key, values);
	viewport.sync(session);
	tick.value++;
}

async function change_references(rows) {
	session.set_references(rows);
	await load_references();
	tick.value++;
}

function define_block(block) {
	session.define_block(block);
	viewport.set_blocks([...session.blocks.values()]);
	tick.value++;
}

function set_elevation(text) {
	const value = Number(text);
	if (!Number.isFinite(value)) return error(__("Enter a number."));
	settings.elevation = value;
	viewport.elevation = value;
	viewport.update_grid();
	viewport.render();
}

async function run_job(method, args) {
	try {
		const started = await call(method, args);
		const done = await follow_job(started);
		if (done.status !== "Completed") {
			error(__("{0}: {1}", [done.job_type, done.status_message]));
			return null;
		}
		return done;
	} catch (e) {
		error(e.message);
		return null;
	}
}

async function build_solids(items) {
	const done = await run_job("build_solids", { model: model.value.name, items });
	if (!done) return null;
	const results = done.results.items;
	await load_meshes(results.filter((r) => r.geometry_ref).map((r) => r.geometry_ref));
	return results;
}

async function offset_curves(items) {
	const done = await run_job("offset_curves", { model: model.value.name, items });
	return done ? done.results.items : null;
}

async function follow_job(started, announce = false) {
	job.value = started;
	const done = await wait_for_job(started.name, (j) => (job.value = j));
	if (announce || done.status !== "Completed") message(`${done.job_type}: ${done.status_message}`, done.status === "Completed" ? "result" : "error");
	if (done.job_type === "Validate Model" && done.status === "Completed") await refresh_validation();
	setTimeout(() => job.value && job.value.name === done.name && (job.value = null), 6000);
	return done;
}

async function load_meshes(refs) {
	const missing = [];
	await Promise.all(
		[...new Set(refs)].filter((r) => r && !viewport.mesh_cache.has(r)).map(async (ref) => {
			try {
				const mesh = await get_mesh(model.value.name, ref);
				if (mesh) viewport.mesh_cache.set(ref, mesh);
				else missing.push(ref);
			} catch (e) {
				error(e.message);
			}
		})
	);
	if (missing.length && config.worker_enabled) {
		const started = await call("request_meshes", { model: model.value.name, geometry_refs: missing });
		await follow_job(started);
		for (const ref of missing) {
			const mesh = await get_mesh(model.value.name, ref).catch(() => null);
			if (mesh) viewport.mesh_cache.set(ref, mesh);
		}
	}
}

async function save() {
	if (!model.value || !change_count.value || saving.value) return;
	tools.cancel(true);
	saving.value = true;
	try {
		const result = await call("save_edits", { model: model.value.name, edit_version: model.value.edit_version, changes: session.changes() });
		session.saved(result);
		if (result.blocks) viewport.set_blocks(result.blocks);
		Object.assign(model.value, result.model, { edit_version: result.edit_version, working_revision: result.working_revision });
		if (result.layers) current_layer_key.value = fix_layer_key(current_layer_key.value);
		message(__("Saved {0} change(s) in {1}.", [result.entities.length + result.deleted.length, result.working_revision]), "result");
		await refresh_info();
		if (result.validation_job) follow_job({ name: result.validation_job, job_type: "Validate Model", status: "Queued" });
	} catch (e) {
		error(e.exc_type === "EditConflict" ? `${e.message}` : __("Not saved: {0}", [e.message]));
	} finally {
		saving.value = false;
		tick.value++;
	}
}

function fix_layer_key(key) {
	if (session.layers.has(key)) return key;
	const layer = session.layer_by_name(key);
	return layer ? layer.name : [...session.layers.keys()][0];
}

async function refresh_validation() {
	// validation results (status, measures) for entities without local changes
	const fresh = await fetch_all_entities();
	for (const row of fresh) {
		const local = session.entities.get(row.entity_id);
		if (local && !session.dirty.has(row.entity_id) && local.entity_version === row.entity_version) {
			Object.assign(local, { validation_status: row.validation_status, validation_message: row.validation_message, length: row.length, area: row.area, volume: row.volume });
		}
	}
	tick.value++;
}

async function fetch_all_entities() {
	const rows = [];
	let start = 0;
	for (;;) {
		const page = await call("get_entities", { model: model.value.name, start, limit: 5000 });
		rows.push(...page.entities);
		if (rows.length > 5000) loading.value = __("Loading entities… {0}", [rows.length]);
		if (!page.more) break;
		start = page.next_start;
	}
	return rows;
}

async function load_references() {
	const refs = [];
	for (const r of session.references) {
		if (!r.visible) continue;
		try {
			const content = await call("get_reference_content", { model: model.value.name, reference_model: r.reference_model, revision: r.reference_revision || null });
			refs.push({ name: r.reference_model, offset: [r.offset_x || 0, r.offset_y || 0, r.offset_z || 0], content });
		} catch (e) {
			// a reference added in this session is only readable after Save
			if (!session.references_dirty) error(__("Reference {0}: {1}", [r.reference_model, e.message]));
			else message(__("Save to show the new reference {0}.", [r.reference_model]), "warning");
		}
	}
	viewport.set_references(refs, session);
}

// --------------------------------------------------------------------- models and revisions

async function open_model(name, force = false) {
	if (!force && model.value && model.value.name !== name && change_count.value && !window.confirm(__("Discard {0} unsaved change(s)?", [change_count.value]))) return;
	loading.value = __("Opening {0}…", [name]);
	try {
		tools.cancel(true);
		const data = await call("get_model", { model: name });
		info.value = data;
		model.value = data.model;
		session.load(data.model, data.layers, [], data.blocks || [], data.references || []);
		viewport.set_blocks(data.blocks || []);
		viewport.plan_rotated = Boolean(data.plan_rotated);
		settings.text_height = data.text_height || settings.text_height;
		const m = data.model;
		const has_origin = [m.local_origin_x, m.local_origin_y, m.local_origin_z].some((v) => Number(v));
		origin.value = has_origin
			? [m.local_origin_x, m.local_origin_y, m.local_origin_z].map(Number)
			: m.entity_count
				? [(m.min_x + m.max_x) / 2, (m.min_y + m.max_y) / 2, (m.min_z + m.max_z) / 2].map(Math.round)
				: [0, 0, 0];
		viewport.set_origin(origin.value);
		viewport.mesh_cache.clear();
		settings.elevation = has_origin ? Number(m.local_origin_z) || 0 : 0;
		viewport.elevation = settings.elevation;
		const rows = await fetch_all_entities();
		session.add_loaded(rows);
		current_layer_key.value = (session.layer_by_name("0") || {}).name || [...session.layers.keys()][0];
		current_block.value = null;
		selection.value = [];
		viewport.selected = new Set();
		viewport.set_mode(view_mode.value);
		viewport.sync(session);
		viewport.zoom_to(model_extent());
		await load_references();
		await load_meshes(rows.filter((r) => r.entity_type === "Solid").map((r) => r.geometry_ref));
		viewport.sync(session, rows.filter((r) => r.entity_type === "Solid").map((r) => r.entity_id));
		if (left_tab.value === "models") left_tab.value = "layers";
		const url = new URL(window.location.href);
		url.searchParams.set("model", name);
		window.history.replaceState(null, "", url);
		message(__("Opened {0}: {1} entities.", [name, rows.length]));
		if (data.in_review) message(__("{0} is in review; the model is read-only until it is approved or rejected.", [data.in_review]), "warning");
	} catch (e) {
		error(e.message);
	} finally {
		loading.value = "";
		tick.value++;
	}
}

async function refresh_info() {
	const data = await call("get_model", { model: model.value.name });
	info.value = data;
	Object.assign(model.value, data.model);
	if (model_panel.value) model_panel.value.reload();
}

function close_import(imported) {
	dialog.value = null;
	if (imported) open_model(model.value.name, true);
}

async function submit_revision() {
	const description = window.prompt(__("Describe this revision (what changed and why):"));
	if (description === null) return;
	try {
		const name = await call("submit_revision", { model: model.value.name, description });
		message(__("{0} submitted for review; its geometry is frozen.", [name]), "result");
		await refresh_info();
	} catch (e) {
		error(e.message);
	}
	tick.value++;
}

async function approve_revision(revision) {
	const notes = window.prompt(__("Approval notes (optional):"), "");
	if (notes === null) return;
	try {
		await call("approve_revision", { revision, notes });
		message(__("{0} approved.", [revision]), "result");
		await refresh_info();
	} catch (e) {
		error(e.message);
	}
	tick.value++;
}

async function reject_revision(revision) {
	const notes = window.prompt(__("Why is it rejected?"));
	if (!notes) return;
	try {
		await call("reject_revision", { revision, notes });
		message(__("{0} rejected.", [revision]), "warning");
		await refresh_info();
	} catch (e) {
		error(e.message);
	}
	tick.value++;
}

async function restore_revision(revision) {
	if (!window.confirm(__("Replace the model's current working state with {0}? The current state stays in its own revision history only if it was submitted.", [revision]))) return;
	try {
		const working = await call("restore_revision", { revision });
		message(__("{0} restored into {1}.", [revision, working]), "result");
		await open_model(model.value.name, true);
	} catch (e) {
		error(e.message);
	}
}

// --------------------------------------------------------------------- help

function show_help() {
	[
		__("Coordinates: x,y or x,y,z (absolute) · @dx,dy[,dz] (relative to the last point) · @distance<angle (polar; degrees counter-clockwise from +X) · a number alone = distance along the cursor direction."),
		__("Draw: L PL (A arc / L line, C close) REC POL C A A3 EL SPL XL PO T MT · Dimensions: DAL DLI DAN DRA DDI · H(atch) of selected closed objects · B(lock) / I(nsert)."),
		__("Modify: M CO RO SC MI S(tretch) AR(ray) ARRAYPOLAR O(ffset) TR(im) EX(tend) BR(eak) J(oin) X (explode) F(illet) CHA(mfer) E(rase). Measure: DI AA ANG ID. Enter alone repeats the last command."),
		__("Mouse: left = pick/select (Shift adds), drag left→right = window, right→left = crossing · wheel = zoom at cursor · middle-drag = pan · right-drag = pan (2D) / orbit (3D) · right-click = Enter."),
		__("Keys: Esc cancel · Enter finish · Del erase · Ctrl+S save · Ctrl+Z/Y undo/redo · F3 object snap · F7 grid · F8 ortho · F9 grid snap · F10 polar tracking."),
	].forEach((t) => message(t, "help"));
}

function show_about() {
	[
		__("CAD Engineering Workstation — part of is_production (GPL-3.0-or-later), Isambane Mining (Pty) Ltd."),
		__("Geometry: Open CASCADE Technology {0} (LGPL-2.1 with exception) via cadquery-ocp (Apache-2.0), run in an isolated worker. DXF: ezdxf (MIT); reprojection: pyproj/PROJ (MIT).", [(config.health && config.health.occt_version) || ""]),
		__("Viewer: three.js (MIT) · Vue (MIT) · icons: Lucide (ISC) · text: Droid Sans (Apache-2.0). No affiliation with or endorsement by these projects."),
	].forEach((t) => message(t, "help"));
}

// --------------------------------------------------------------------- preferences

const PREF_KEYS = ["object_snap", "snaps", "grid_snap", "ortho", "polar", "polar_angle", "aperture", "show_grid", "lineweights", "decimals"];

function load_prefs() {
	try {
		const saved = JSON.parse(window.localStorage.getItem(PREFS_KEY) || "{}");
		for (const key of PREF_KEYS) if (key in saved) settings[key] = saved[key];
	} catch (e) {
		/* private window or blocked storage: defaults */
	}
}

function save_prefs() {
	try {
		window.localStorage.setItem(PREFS_KEY, JSON.stringify(Object.fromEntries(PREF_KEYS.map((k) => [k, settings[k]]))));
	} catch (e) {
		/* ignore */
	}
}

function before_unload(event) {
	if (change_count.value) {
		event.preventDefault();
		event.returnValue = "";
	}
}

// --------------------------------------------------------------------- lifecycle

let unsubscribe = null;
let theme_observer = null;

onMounted(async () => {
	viewport = markRaw(new Viewport(viewport_el.value, { on_view_change: (v) => (grid_step.value = v.grid_step) }));
	viewport.set_grid_visible(settings.show_grid);
	viewport.show_lineweights = settings.lineweights;
	viewport.decimals = settings.decimals;
	viewport.angular_decimals = config.angular_precision ?? 4;
	tools = markRaw(
		new ToolController({
			session,
			viewport,
			settings,
			message,
			current_layer: () => current_layer_key.value,
			current_block: () => current_block.value,
			selection: () => selection.value,
			select: (ids) => select_ids(ids),
			build_solids,
			offset_curves,
			define_block,
			visible_entities,
			cutters: () => [...visible_entities(), ...(viewport.reference_entities || [])],
		})
	);
	window.__cad = { viewport, session, tools }; // for the browser console and automated UI tests
	unsubscribe = session.on_change((ids) => {
		const first = ids.length && session.entities.get(ids[0]);
		const box = first && entity_extent(first, viewport.blocks);
		if (box && box.every(Number.isFinite) && first.entity_type !== "Construction Line") ensure_origin(box.slice(0, 3));
		viewport.sync(session, ids.length ? ids : null);
		tick.value++;
	});
	theme_observer = new MutationObserver(() => viewport.read_theme());
	theme_observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "class"] });
	window.addEventListener("beforeunload", before_unload);
	load_font()
		.then(() => model.value && viewport.sync(session))
		.catch((e) => error(__("The text font could not be loaded: {0}", [e.message])));
	if (!config.enabled) error(__("The CAD Workstation is switched off (CAD Settings)."));
	const requested = new URL(window.location.href).searchParams.get("model");
	if (requested) await open_model(requested);
	else message(__("Open or create a model in the Models panel. Help → Commands lists the command line."));
	tool_state.value++;
});

onBeforeUnmount(() => {
	if (unsubscribe) unsubscribe();
	if (theme_observer) theme_observer.disconnect();
	window.removeEventListener("beforeunload", before_unload);
	viewport.dispose();
});
</script>
