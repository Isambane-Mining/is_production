<template>
	<div class="cad-app" @keydown="on_key" tabindex="-1" ref="root">
		<MenuBar :menus="menus" @command="run">
			<span class="cad-model-title" v-if="model">
				<b>{{ model.model_name }}</b>
				<span class="muted"> · {{ model.name }} · {{ model.crs || __("no CRS") }} · {{ model.linear_units }}</span>
				<span v-if="working_label" class="cad-chip warning">{{ working_label }}</span>
				<span v-if="info.in_review" class="cad-chip info">{{ __("In review") }}</span>
				<span v-if="model.design_standard" class="cad-chip" :title="__('Design standard (Mining → Design Standard)')">{{ model.design_standard }}</span>
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
			<button :class="{ on: orbit_mode }" :title="__('Orbit (ORBIT): left-drag turns the view about the point under the cursor. Also: right-drag in 3D, Shift+middle-drag or Alt+left-drag, arrow keys.')" :disabled="!model" @click="run('toggle_orbit')"><Icon name="view3d" /></button>
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
						@compare="run('plan_compare')"
					/>
				</template>
				<div v-else-if="left_tab !== 'models'" class="muted pad">{{ __("Open a model first.") }}</div>
			</aside>

			<section class="cad-viewport-wrap">
				<div ref="viewport_el" class="cad-viewport" @pointermove="on_pointer_move" @pointerdown="on_pointer_down" @pointerup="on_pointer_up" @dblclick="on_double_click" @contextmenu.prevent="on_context" @pointerleave="cursor = null"></div>
				<ObjectPanel
					v-if="object_panel && panel_entity"
					:entity="panel_entity"
					:layers="session_layers"
					:can_edit="editable"
					:decimals="settings.decimals"
					:mode="object_panel.mode"
					:extrude="object_panel.extrude"
					:targets="object_panel.targets"
					:profiles="object_panel.profiles.length || object_panel.ids.length"
					:editing="Boolean(object_panel.editing)"
					:history="panel_entity && panel_entity.entity_type === 'Solid' ? solid_history(panel_entity).map(history_label) : []"
					@history_edit="edit_history_step"
					@history_remove="remove_history_step"
					:stale="object_panel.stale"
					:units="unit_symbol"
					:worker="config.worker_enabled"
					:busy="object_panel.busy"
					@update="object_update"
					@mode="object_mode"
					@extrude="object_extrude"
					@ok="extrude_ok"
					@close="close_object"
				/>
				<div v-if="extrude_label" class="cad-extrude-label" :style="{ left: extrude_label.x + 'px', top: extrude_label.y + 'px' }">{{ extrude_label.text }}</div>
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
				<div v-if="legend" class="cad-legend-box small">
					<div>{{ legend.mode === "slope" ? __("Slope 0° … 60°+") : __("Elevation") }}</div>
					<div class="cad-ramp" :class="legend.mode"></div>
					<div v-if="legend.mode !== 'slope'" class="cad-row mono"><span>{{ fmt(legend.min) }}</span><span class="cad-grow"></span><span>{{ fmt(legend.max) }}</span></div>
				</div>
				<div v-if="section_on" class="cad-chip info cad-section-chip" @click="run('section_plane_off')">{{ __("Section view — click to turn off") }}</div>
				<div v-if="model" class="cad-nav" :class="{ raised: analysis }">
					<button :title="__('Turn left 15° (←)')" @click="run('orbit', [-15, 0])">↺</button>
					<button :title="__('Tilt up 10° (↑)')" @click="run('orbit', [0, 10])">▲</button>
					<button :title="__('Turn right 15° (→)')" @click="run('orbit', [15, 0])">↻</button>
					<button :title="__('Tilt down 10° (↓)')" @click="run('orbit', [0, -10])">▼</button>
					<button :title="__('Plan (top) view')" @click="run('view', 'top')">{{ __("Top") }}</button>
					<button :title="__('Isometric view')" @click="run('view', 'iso')">{{ __("Iso") }}</button>
					<button :class="{ on: orbit_mode }" :title="__('Orbit with the left mouse button / trackpad (ORBIT)')" @click="run('toggle_orbit')"><Icon name="view3d" /></button>
					<button :title="__('Zoom Extents')" @click="run('zoom_extents')"><Icon name="zoom_extents" /></button>
				</div>
				<AnalysisPanel v-if="analysis" :data="analysis" :decimals="settings.decimals" @close="analysis = null" @zoom="zoom_point" @select="zoom_ids" />
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
					:surface_types="config.surface_types"
					@edit="(id) => open_object([id])"
					@update="update_entities"
					@update_geometry="update_geometry"
					@error="error"
				/>
				<h4 class="cad-subhead">{{ __("Coordinates") }}</h4>
				<div class="cad-props">
					<div class="cad-prop"><span>{{ __("Cursor") }}</span><span class="mono">{{ cursor ? fmt_point(cursor) : "—" }}</span></div>
					<div class="cad-prop"><span>{{ __("Snap") }}</span><span>{{ snap_kind || "—" }}</span></div>
					<div v-if="surface_point" class="cad-prop"><span>{{ __("Surface") }}</span><span class="mono" :title="surface_point.name">Z {{ fmt(surface_point.point[2]) }} · {{ surface_point.name }}</span></div>
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
					@keydown.esc.prevent.stop="on_escape"
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

		<ImportDialog v-if="dialog === 'import'" :model="model" :surface_types="config.surface_types" @close="close_import" @error="error" />
		<ExportDialog v-if="dialog === 'export'" :model="model" :revisions="info.revisions || []" :selected="selected_entities" @close="dialog = null" @error="error" />
		<FormDialog v-if="form" v-bind="form.props" @done="form.resolve" />
	</div>
</template>

<script setup>
import { computed, markRaw, nextTick, onBeforeUnmount, onMounted, reactive, ref, shallowRef, watch } from "vue";

import AnalysisPanel from "./AnalysisPanel.vue";
import { call, get_mesh, wait_for_job } from "./api";
import BlockPanel from "./BlockPanel.vue";
import { ALIASES, parse_input } from "./command";
import EntityPanel from "./EntityPanel.vue";
import FormDialog from "./FormDialog.vue";
import ExportDialog from "./ExportDialog.vue";
import { closed_loop, entity_extent, format_number, format_point, union } from "./geometry";
import Icon from "./Icon.vue";
import ImportDialog from "./ImportDialog.vue";
import LayerPanel from "./LayerPanel.vue";
import MenuBar from "./MenuBar.vue";
import ObjectPanel from "./ObjectPanel.vue";
import { ExtrudePreview } from "./extrude_preview";
import ModelPanel from "./ModelPanel.vue";
import PropertiesPanel from "./PropertiesPanel.vue";
import ReferencePanel from "./ReferencePanel.vue";
import RevisionPanel from "./RevisionPanel.vue";
import { EditSession } from "./session";
import { CloudCommands } from "./cloud_commands";
import { MiningCommands } from "./mining_commands";
import { PlanningCommands } from "./planning_commands";
import { SurfaceCommands } from "./surface_commands";
import { load_font } from "./text";
import { ToolController, TOOLS } from "./tools";
import { Viewport } from "./viewport";

const props = defineProps({ config: { type: Object, required: true } });
const THREE_DEG = Math.PI / 180;
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
let surface_commands = null;
let mining_commands = null;
let cloud_commands = null;
let planning_commands = null;
let compare_ref = null; // an older revision drawn faded over the model (Compare Revisions)

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
const form = shallowRef(null); // {props, resolve} of the open FormDialog
const analysis = shallowRef(null); // a result shown in the Analysis panel
const surface_point = ref(null);
const surface_mode = ref("elevation");
const surface_wireframe = ref(false);
const section_on = ref(false);
const orbit_mode = ref(false);
const compare_on = ref(false);
const object_panel = ref(null); // the object tool window: {ids, mode, extrude, targets, busy}
const extrude_label = ref(null);
let extrude_preview = null;
const panel_entity = computed(() => (tick.value, object_panel.value ? session.entities.get(object_panel.value.ids[0]) || null : null));
const unit_symbol = computed(() => ({ Metres: "m", Millimetres: "mm", Kilometres: "km", Feet: "ft", "US Survey Feet": "ft" })[model.value && model.value.linear_units] || "");
const legend = computed(() => (tick.value, surface_mode.value, model.value && viewport && viewport.surfaces.items.size && surface_mode.value !== "layer" ? viewport.surfaces.legend() : null));

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
	const surface_ok = solid_ok; // makes or changes entities
	const run_ok = has && config.worker_enabled; // reports only (read access is enough)
	const mine_ok = solid_ok && config.mining_tools; // CAD Settings → Enable Mining Design Tools
	return [
		{
			label: __("File"),
			items: [
				{ label: __("New / Open Model…"), command: "show_models" },
				{ label: __("Save"), command: "save", key: "Ctrl+S", disabled: !has || !change_count.value },
				{ label: __("Reload from Server"), command: "reload", disabled: !has },
				"-",
				{ label: __("Import (DXF, LandXML, CSV, STL, OBJ, LAS, E57, STEP, IFC)…"), command: "import", disabled: !ed || !config.worker_enabled },
				{ label: __("Export…"), command: "export", disabled: !has || !config.worker_enabled },
				"-",
				{ label: __("Validate Model (Open CASCADE)"), command: "validate", disabled: !has || !info.value.can_edit || !config.worker_enabled },
				{ label: __("Submit Revision for Review…"), command: "show_revisions", disabled: !has },
				{ label: __("Compare Revisions…"), command: "plan_compare", disabled: !has },
				{ label: __("Compare Overlay Off"), command: "plan_compare_off", disabled: !compare_on.value },
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
				{ label: __("Orbit with Left Mouse Button"), command: "toggle_orbit", key: "ORBIT", checked: orbit_mode.value, disabled: !has },
				{ label: __("Turn Left 15°"), command: "orbit", args: [-15, 0], key: "←", disabled: !has },
				{ label: __("Turn Right 15°"), command: "orbit", args: [15, 0], key: "→", disabled: !has },
				{ label: __("Tilt Up 10°"), command: "orbit", args: [0, 10], key: "↑", disabled: !has },
				{ label: __("Tilt Down 10°"), command: "orbit", args: [0, -10], key: "↓", disabled: !has },
				"-",
				{ label: __("Zoom Extents"), command: "zoom_extents", disabled: !has },
				{ label: __("Zoom Selection"), command: "zoom_selection", disabled: !selection.value.length },
				{ label: __("Previous View"), command: "previous_view", disabled: !view_history.length },
				{ label: __("Grid"), command: "toggle_grid", key: "F7", checked: settings.show_grid },
				{ label: __("Line Weights"), command: "toggle_lineweights", checked: settings.lineweights },
				"-",
				{ label: __("Section View…"), command: "section_plane", disabled: !has },
				{ label: __("Section View Off"), command: "section_plane_off", disabled: !section_on.value },
				"-",
				{ label: __("Surfaces by Elevation"), command: "surface_colour", args: "elevation", checked: surface_mode.value === "elevation" },
				{ label: __("Surfaces by Slope"), command: "surface_colour", args: "slope", checked: surface_mode.value === "slope" },
				{ label: __("Surfaces by Layer Colour"), command: "surface_colour", args: "layer", checked: surface_mode.value === "layer" },
				{ label: __("Surface Triangles (Wireframe)"), command: "surface_wireframe", checked: surface_wireframe.value },
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
			items: [
				{ label: __("Create Surface from Selection / GIS…"), command: "surface_create", disabled: !surface_ok },
				{ label: __("Repair Surface…"), command: "surface_repair", disabled: !surface_ok },
				{ label: __("Clip Surface to Boundary…"), command: "surface_clip", disabled: !surface_ok },
				{ label: __("Smooth Surface…"), command: "surface_smooth", disabled: !surface_ok },
				{ label: __("Surface Checks"), command: "surface_checks", disabled: !has },
				"-",
				{ label: __("Contours…"), command: "contours", disabled: !surface_ok },
				{ label: __("Intersect Surfaces…"), command: "intersect", disabled: !surface_ok },
				{ label: __("Long Section (Profile)…"), command: "profile", disabled: !run_ok },
				{ label: __("Cross-Sections…"), command: "sections", disabled: !run_ok },
				{ label: __("Slope and Elevation Analysis…"), command: "analyse", disabled: !run_ok },
				"-",
				{ label: __("Cut and Fill Volume…"), command: "volume", disabled: !run_ok },
				{ label: __("Recorded Volumes"), command: "volume_history", disabled: !has },
			],
		},
		{
			label: __("Point Cloud"),
			items: [
				{ label: __("Import Point Cloud (LAS / LAZ / E57 / XYZ)…"), command: "import", disabled: !ed || !config.worker_enabled },
				"-",
				{ label: __("Colour by Elevation"), command: "cloud_colour", args: "elevation", disabled: !has },
				{ label: __("Colour by Classification"), command: "cloud_colour", args: "classification", disabled: !has },
				{ label: __("Colour by Intensity"), command: "cloud_colour", args: "intensity", disabled: !has },
				{ label: __("Colour by RGB"), command: "cloud_colour", args: "rgb", disabled: !has },
				{ label: __("Colour by Deviation"), command: "cloud_colour", args: "deviation", disabled: !has },
				{ label: __("Point Size / Deviation Range…"), command: "cloud_size", disabled: !has },
				"-",
				{ label: __("Crop (outline / elevation)…"), command: "cloud_crop", disabled: !surface_ok },
				{ label: __("Remove Noise…"), command: "cloud_noise", disabled: !surface_ok },
				{ label: __("Decimate…"), command: "cloud_decimate", disabled: !surface_ok },
				{ label: __("Classify Ground…"), command: "cloud_ground", disabled: !surface_ok },
				{ label: __("Set Class (outline / elevation)…"), command: "cloud_set_class", disabled: !surface_ok },
				{ label: __("Keep or Remove Classes…"), command: "cloud_classes", disabled: !surface_ok },
				"-",
				{ label: __("Surface from Point Cloud…"), command: "cloud_surface", disabled: !surface_ok },
				{ label: __("Compare with Surface…"), command: "cloud_compare", disabled: !surface_ok },
				{ label: __("Export Point Cloud (LAZ / LAS / E57 / XYZ)…"), command: "cloud_export", disabled: !has },
			],
		},
		{
			label: __("Solid"),
			items: [
				tool_item("box", solid_ok),
				tool_item("cylinder", solid_ok),
				tool_item("extrude", solid_ok),
				"-",
				{ label: __("Union"), command: "solid_boolean", args: "union", disabled: !solid_ok },
				{ label: __("Subtract…"), command: "solid_boolean", args: "subtract", disabled: !solid_ok },
				{ label: __("Intersect"), command: "solid_boolean", args: "intersect", disabled: !solid_ok },
				"-",
				{ label: __("Revolve (profile + axis line)…"), command: "solid_revolve", disabled: !solid_ok },
				{ label: __("Loft (closed profiles)…"), command: "solid_loft", disabled: !solid_ok },
				{ label: __("Sweep (profile + path)"), command: "solid_sweep", disabled: !solid_ok },
				"-",
				{ label: __("Section Lines through Selection…"), command: "section_cut", disabled: !solid_ok },
				"-",
				{ label: __("Fillet Edges…"), command: "fillet_solid", disabled: !solid_ok || !selection.value.length },
				{ label: __("Chamfer Edges…"), command: "chamfer_solid", disabled: !solid_ok || !selection.value.length },
				{ label: __("Shell (hollow)…"), command: "shell_solid", disabled: !solid_ok || !selection.value.length },
				{ label: __("Edit Extrusion (double-click the solid)"), command: "edit_feature", disabled: !solid_ok || !selection.value.length },
				{ label: __("Rebuild Changed Extrusions"), command: "rebuild_features", disabled: !solid_ok },
			],
		},
		{
			label: __("Mining"),
			items: [
				{ label: __("Design Standard…"), command: "mining_standard", disabled: !has || !info.value.can_edit },
				{ label: __("Design Standards (Desk)"), command: "desk_list", args: "CAD Design Standard" },
				{ label: __("Edit Design (regenerate)…"), command: "edit_design", disabled: !mine_ok || !selection.value.length },
				"-",
				{ label: __("Generate Pit Benches from Floor…"), command: "pit_generate", disabled: !mine_ok },
				{ label: __("Pit Stage / Pushback…"), command: "pit_generate", args: { pushback: true }, disabled: !mine_ok },
				{ label: __("Pit Stages (automatic)…"), command: "pit_stages", disabled: !mine_ok },
				"-",
				{ label: __("Haul Road from Centreline…"), command: "road_design", disabled: !mine_ok },
				"-",
				{ label: __("Mining Blocks…"), command: "blocks_generate", disabled: !mine_ok },
				{ label: __("Subdivide Blocks…"), command: "blocks_subdivide", disabled: !mine_ok },
				{ label: __("Set Block Material…"), command: "blocks_material", disabled: !ed },
				{ label: __("Sequence Blocks…"), command: "blocks_sequence", disabled: !ed },
				{ label: __("Block Report"), command: "blocks_report", disabled: !has },
				"-",
				{ label: __("Validate Design against Standard"), command: "design_validate", disabled: !has || !config.mining_tools },
				{ label: __("Design Validations (Desk)"), command: "desk_list", args: "CAD Design Validation" },
				"-",
				{ label: __("Geology from Geo Planning (grids / drillholes)…"), command: "geology_build", disabled: !mine_ok },
				{ label: __("Block Geology (tonnes, qualities, material)…"), command: "block_geology", disabled: !mine_ok },
				{ label: __("Geology Models (Desk)"), command: "desk_list", args: "CAD Geology Model" },
				{ label: __("Pit Optimisation (nested shells)…"), command: "pit_shells", disabled: !mine_ok },
				{ label: __("Pit Optimisations (Desk)"), command: "desk_list", args: "CAD Pit Optimisation" },
				"-",
				{ label: __("Drill Pattern (layout)…"), command: "drill_pattern", disabled: !mine_ok },
				{ label: __("Drill Pattern Report"), command: "drill_report", disabled: !has },
				"-",
				{ label: __("Publish Approved Design to Geo Planning…"), command: "plan_publish", disabled: !has },
				{ label: __("Schedule Selection from CAD Sequence"), command: "plan_selection", disabled: !has },
				{ label: __("Planning View (status / schedule / % mined)…"), command: "plan_view", disabled: !has },
				{ label: __("Planning View Off"), command: "plan_view_off", disabled: !has },
				{ label: __("Reconcile Survey against Design…"), command: "plan_reconcile", disabled: !has || !config.worker_enabled },
				{ label: __("Publications (Desk)"), command: "desk_list", args: "CAD Design Publication" },
				{ label: __("Survey Reconciliations (Desk)"), command: "desk_list", args: "CAD Survey Reconciliation" },
			],
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
				if (args === "extrude") return start_extrude(); // the Extrude tool window, with a drag handle
				if (tools.start(args)) last_command.value = args;
				break;
			case "cancel":
				tools.cancel(true);
				select_ids([]);
				if (orbit_mode.value) run("toggle_orbit");
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
			case "fillet_solid":
			case "chamfer_solid":
			case "shell_solid":
				await dress_solid(command.split("_")[0]);
				break;
			case "rebuild_features":
				await rebuild_features();
				break;
			case "edit_feature": {
				const solid = selection.value.map((id) => session.entities.get(id)).find((e) => feature_of(e));
				if (!solid) return error(__("Select an extruded solid (made with the Extrude tool window)."));
				open_object([solid.entity_id], "solid");
				break;
			}
			case "toggle_orbit":
				orbit_mode.value = !orbit_mode.value;
				viewport.orbit_mode = orbit_mode.value;
				message(orbit_mode.value ? __("Orbit on: left-drag turns the view (selection is off until you switch it off again or press Esc).") : __("Orbit off."));
				break;
			case "orbit": {
				// args: [degrees about the vertical, degrees of tilt]
				if (view_mode.value === "2d") view_mode.value = "3d";
				viewport.orbit(THREE_DEG * args[0], THREE_DEG * args[1]);
				break;
			}
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
				else if (surface_commands && (await surface_commands.run(command, args))) after_surface_command(command);
				else if (mining_commands && (await mining_commands.run(command, args))) break;
				else if (cloud_commands && (await cloud_commands.run(command, args))) break;
				else if (planning_commands) await planning_commands.run(command, args);
		}
	} catch (e) {
		error(e.message);
	} finally {
		tool_state.value++;
		tick.value++;
	}
}

// FormDialog as a promise: the values, or null when cancelled.
function ask(props) {
	return new Promise((resolve) => {
		form.value = {
			props,
			resolve: (values) => {
				form.value = null;
				nextTick(() => root.value && root.value.focus({ preventScroll: true })); // keys (Ctrl+S, Esc) work again
				resolve(values);
			},
		};
	});
}

function show_result(result) {
	analysis.value = result;
}

function after_surface_command(command) {
	surface_mode.value = viewport.surfaces.mode;
	surface_wireframe.value = viewport.surfaces.wireframe;
	section_on.value = Boolean(viewport.section);
	if (command === "section_plane" && section_on.value && view_mode.value === "2d") run("view", "iso");
}

function zoom_point(p) {
	const r = Math.max(10 * viewport.world_per_pixel(), 5);
	push_view();
	viewport.zoom_to([p[0] - r, p[1] - r, p[2], p[0] + r, p[1] + r, p[2]]);
}

// Surface display tiles: a surface without tiles gets them made (once per session), then reloads.
const tiles_requested = new Set();
async function on_surface_loaded(event) {
	if (event.stats) return tick.value++;
	const e = event.missing;
	if (!e || !config.worker_enabled || tiles_requested.has(e.geometry_ref)) return;
	tiles_requested.add(e.geometry_ref);
	const done = await run_job("surface_job", { model: model.value.name, operation: "tiles", params: { geometry_ref: e.geometry_ref } });
	if (done) {
		viewport.surfaces.remove(e.entity_id);
		viewport.sync(session, [e.entity_id]);
	}
}

// Point-cloud display tiles: a cloud without tiles gets them made (once per session), then reloads.
async function on_cloud_loaded(event) {
	if (event.stats) return tick.value++;
	const e = event.missing;
	if (!e || !config.worker_enabled || tiles_requested.has(e.geometry_ref)) return;
	tiles_requested.add(e.geometry_ref);
	const done = await cloud_commands.make_tiles(e);
	if (done) {
		viewport.clouds.remove(e.entity_id);
		viewport.sync(session, [e.entity_id]);
	}
}

// Elevation of the surface under the cursor, at most every 80 ms (ray cast against the display tiles).
let surface_probe_at = 0;
function probe_surface(event) {
	const now = performance.now();
	if (now - surface_probe_at < 80 || !viewport.surfaces.items.size) {
		if (!viewport.surfaces.items.size) surface_point.value = null;
		return;
	}
	surface_probe_at = now;
	const hit = viewport.surface_at(event);
	const e = hit && session.entities.get(hit.entity_id);
	surface_point.value = e ? { point: hit.point, name: (e.geometry && e.geometry.name) || e.entity_id } : null;
}

// --------------------------------------------------------------------- object tool window

const FILLABLE = new Set(["Polyline", "Circle", "Ellipse"]);

// outline / filled for closed shapes (null: the object has no inside)
function shape_mode(e) {
	if (!e || !FILLABLE.has(e.entity_type)) return null;
	const g = e.geometry || {};
	const closed = e.entity_type === "Circle" || (e.entity_type === "Polyline" && g.closed) || (e.entity_type === "Ellipse" && Math.abs(Math.abs((g.end_param ?? 6.283185307179586) - (g.start_param ?? 0)) - 2 * Math.PI) < 1e-9);
	if (!closed) return null;
	return g.fill ? "filled" : "outline";
}

// the object went away (undo, erase, reload): so does its window
watch(panel_entity, (e) => object_panel.value && !e && close_object());

// An extruded solid remembers how it was made (metadata.feature): its outlines (by id, and a copy of each loop),
// distance, direction, taper, and the body it was joined to / cut from. Editing it re-runs the extrusion with the
// outlines as they are now; if an outline changed since, the window says so.
function feature_of(e) {
	const f = e && e.metadata && e.metadata.feature;
	return f && f.type === "extrude" ? f : null;
}

function same_loop(a, b) {
	const key = (l) => JSON.stringify([(l.points || []).map((p) => p.map((v) => Math.round(v * 1e6) / 1e6)), (l.bulges || []).map((v) => Math.round(v * 1e9) / 1e9)]);
	return key(a) === key(b);
}

// the outlines of a feature now: the outline objects if they still exist, else the copies kept in the feature
function feature_profiles(f) {
	return f.profiles.map((p) => {
		const e = p.entity_id && session.entities.get(p.entity_id);
		const now = e && closed_loop(e);
		return { entity_id: p.entity_id, loop: now || p.loop, changed: Boolean(now && !same_loop(now, p.loop)), missing: !now };
	});
}

function open_object(ids, mode = null) {
	close_object();
	const e = session.entities.get(ids[0]);
	if (!e) return;
	const f = feature_of(e);
	if (f && (mode === null || mode === "solid")) {
		// edit the extrusion that made this solid
		const profiles = feature_profiles(f);
		object_panel.value = reactive({ ids, mode: "solid", extrude: null, targets: [], busy: false, editing: e.entity_id, stale: profiles.some((p) => p.changed), profiles });
		return start_preview({ distance: f.distance, direction: f.direction, taper: f.taper, operation: f.operation, keep: true });
	}
	object_panel.value = reactive({ ids, mode: mode || shape_mode(e), extrude: null, targets: [], busy: false, editing: null, stale: false, profiles: [] });
	if (object_panel.value.mode === "solid") start_preview();
}

function close_object() {
	if (extrude_preview) extrude_preview.dispose();
	extrude_preview = null;
	extrude_label.value = null;
	if (object_panel.value) nextTick(() => root.value && root.value.focus({ preventScroll: true })); // keys (Ctrl+S, Esc) work again
	object_panel.value = null;
}

function object_update(values) {
	const ids = object_panel.value.ids;
	session.apply(__("Properties"), (w) => {
		for (const id of ids) {
			const e = session.entities.get(id);
			if (e && !session.is_locked(e)) w.put({ ...e, ...values });
		}
	});
	tick.value++;
}

function object_mode(mode) {
	const panel = object_panel.value;
	if (mode === "solid") {
		const profiles = panel.ids.map((id) => session.entities.get(id)).filter((e) => closed_loop(e));
		if (!profiles.length) return error(__("Only closed polylines and circles can be extruded (not ellipses)."));
		panel.mode = "solid";
		return start_preview();
	}
	if (extrude_preview) extrude_preview.dispose();
	extrude_preview = null;
	extrude_label.value = null;
	panel.mode = mode;
	session.apply(mode === "filled" ? __("Fill") : __("Outline"), (w) => {
		for (const id of panel.ids) {
			const e = session.entities.get(id);
			if (!e || !shape_mode(e)) continue;
			const geometry = { ...e.geometry };
			if (mode === "filled") geometry.fill = true;
			else delete geometry.fill;
			w.put({ ...e, geometry, validation_status: "Not Checked" });
		}
	});
	tick.value++;
}

let last_extrude = null;

function start_extrude() {
	const ids = selection.value.filter((id) => closed_loop(session.entities.get(id)));
	if (!ids.length) return error(__("Extrude: select closed polylines or circles (or draw one: its tool window has Solid)."));
	open_object(ids, "solid");
}

function start_preview(preset = null) {
	const panel = object_panel.value;
	if (!panel.editing) {
		const outlines = panel.ids.map((id) => session.entities.get(id)).filter((e) => closed_loop(e));
		panel.ids = outlines.map((e) => e.entity_id);
		panel.profiles = outlines.map((e) => ({ entity_id: e.entity_id, loop: closed_loop(e) }));
	}
	const loops = panel.profiles.map((p) => p.loop);
	if (loops.some((l) => l.points.some((p) => Math.abs((p[2] || 0) - (l.points[0][2] || 0)) > 1e-9))) {
		panel.mode = panel.editing ? null : shape_mode(session.entities.get(panel.ids[0]));
		return error(__("Extrude needs flat (horizontal) outlines."));
	}
	const xs = loops.flatMap((l) => l.points.map((p) => p[0])), ys = loops.flatMap((l) => l.points.map((p) => p[1]));
	const box = [Math.min(...xs), Math.min(...ys), 0, Math.max(...xs), Math.max(...ys), 0];
	const size = Math.max(box[3] - box[0], box[4] - box[1]);
	const step = Math.pow(10, Math.floor(Math.log10(Math.max(size, 1e-6) / 4)));
	const distance = preset ? preset.distance : last_extrude ? last_extrude.distance : Math.max(step, Math.round(size / 4 / step) * step);
	// solids under or over the outlines can be joined, cut or intersected
	const targets = panel.editing
		? []
		: [...session.entities.values()]
				.filter((e) => e.entity_type === "Solid" && e.geometry_ref)
				.filter((e) => {
					const b = entity_extent(e);
					return b && b[0] <= box[3] && b[3] >= box[0] && b[1] <= box[4] && b[4] >= box[1];
				})
				.map((e) => ({ entity_id: e.entity_id, label: `${__("Solid")} ${e.entity_id}${e.volume ? ` · ${format_number(e.volume, 1)}` : ""}` }));
	panel.targets = targets;
	panel.extrude = reactive({ distance, direction: "one", taper: 0, operation: "new", target: targets.length ? targets[0].entity_id : null, keep: true, error: "", ...(preset || {}) });
	if (view_mode.value === "2d") run("orbit", [0, -35]); // the handle is dragged up and down: look at it from the side
	extrude_preview = markRaw(
		new ExtrudePreview(viewport, loops, (d) => {
			panel.extrude.distance = d;
			place_extrude_label();
		})
	);
	extrude_preview.set({ distance, symmetric: panel.extrude.direction === "symmetric", taper: panel.extrude.taper });
	place_extrude_label();
}

function object_extrude(change) {
	const ex = object_panel.value.extrude;
	Object.assign(ex, change, { error: "" });
	if (extrude_preview) extrude_preview.set({ distance: ex.distance, symmetric: ex.direction === "symmetric", taper: ex.taper });
	place_extrude_label();
}

function place_extrude_label() {
	if (!extrude_preview || !object_panel.value || !object_panel.value.extrude) return (extrude_label.value = null);
	const [x, y] = viewport.screen_of(extrude_preview.handle_model_point());
	const ex = object_panel.value.extrude;
	extrude_label.value = { x, y, text: `${format_number(ex.distance, Math.min(settings.decimals, 3))} ${unit_symbol.value}${ex.taper ? ` · ${ex.taper}°` : ""}` };
}

// Build an extrusion (and its Boolean with a base body): [{geometry_ref, …, feature}] or {error}.
async function build_extrusion(profiles, params, base) {
	const d = Number(params.distance);
	if (!d) return { error: __("The distance must not be zero.") };
	const shift = params.direction === "symmetric" ? -d / 2 : 0;
	const items = profiles.map((p, n) => ({ key: p.entity_id || `P${n}`, op: "extrude", profile: p.loop.points.map((q) => [q[0], q[1], (q[2] || 0) + shift]), bulges: p.loop.bulges || null, vector: [0, 0, d], taper: params.taper || 0 }));
	let results = await build_solids(items);
	if (!results) return { error: __("The CAD worker could not build it.") };
	const failed = results.filter((r) => !r.geometry_ref);
	if (failed.length) return { error: failed.map((r) => r.message || __("invalid geometry")).join(" ") };
	const feature = (ps) => ({ type: "extrude", profiles: ps.map((p) => ({ entity_id: p.entity_id || null, loop: { points: p.loop.points, ...(p.loop.bulges ? { bulges: p.loop.bulges } : {}) } })), distance: d, direction: params.direction, taper: params.taper || 0, operation: params.operation || "new", base: base || null });
	if (params.operation && params.operation !== "new") {
		const kind = { join: "union", cut: "subtract", intersect: "intersect" }[params.operation];
		const out = await build_solids([{ key: kind, op: "boolean", kind, geometry_refs: [base.geometry_ref, ...results.map((r) => r.geometry_ref)] }]);
		if (!out || !out[0].geometry_ref) return { error: (out && out[0].message) || __("Open CASCADE could not {0} these solids.", [params.operation]) };
		return [{ ...out[0], feature: feature(profiles) }];
	}
	return results.map((r, n) => ({ ...r, feature: feature([profiles[n]]) })); // one solid (and feature) per outline
}

function solid_values(r) {
	return { geometry: { source: r.source }, geometry_ref: r.geometry_ref, bbox: r.bbox, volume: r.volume, area: r.area, validation_status: r.valid ? "Valid" : "Invalid" };
}

async function extrude_ok() {
	const panel = object_panel.value;
	const ex = panel.extrude;
	panel.busy = true;
	try {
		if (panel.editing) {
			const solid = session.entities.get(panel.editing);
			const f = feature_of(solid);
			const built = await build_extrusion(panel.profiles, { ...ex, operation: f.operation }, f.base);
			if (built.error) return (ex.error = built.error);
			const r = await apply_history(built[0], solid_history(solid)); // the solid's fillets / chamfers / shell again
			if (r.error) return (ex.error = r.error);
			session.apply(__("Edit Extrude"), (w) => w.put({ ...solid, ...solid_values(r), metadata: { ...(solid.metadata || {}), feature: built[0].feature } }));
			last_extrude = { distance: Number(ex.distance) };
			message(__("Extrusion rebuilt: volume {0}.", [format_number(r.volume, 3)]), "result");
			return close_object();
		}
		const profiles = panel.profiles;
		let base = null;
		if (ex.operation !== "new") {
			const target = session.entities.get(ex.target);
			if (!target) return (ex.error = __("Choose the body to {0}.", [ex.operation]));
			base = { entity_id: target.entity_id, geometry_ref: target.geometry_ref, layer: target.cad_layer };
		}
		const built = await build_extrusion(profiles, ex, base);
		if (built.error) return (ex.error = built.error);
		const remove = ex.keep ? [] : profiles.map((p) => p.entity_id).filter(Boolean);
		if (base) remove.push(base.entity_id);
		const label = { new: __("Extrude"), join: __("Extrude (join)"), cut: __("Extrude (cut)"), intersect: __("Extrude (intersect)") }[ex.operation];
		last_extrude = { distance: Number(ex.distance) };
		const layer = session.entities.get(profiles[0].entity_id)?.cad_layer || current_layer_key.value;
		surface_commands.add_solids(label, built, layer, remove, (r) => ({ metadata: { feature: r.feature } })); // each solid keeps its feature
		close_object();
	} finally {
		if (object_panel.value) object_panel.value.busy = false;
		tick.value++;
	}
}

// Rebuild every extruded solid whose outlines changed since it was made (Solid → Rebuild Changed Extrusions).
async function rebuild_features() {
	const stale = [...session.entities.values()].filter((e) => feature_of(e) && feature_profiles(feature_of(e)).some((p) => p.changed));
	if (!stale.length) return message(__("Every extrusion is up to date with its outlines."), "result");
	let done = 0;
	for (const solid of stale) {
		const f = feature_of(solid);
		const built = await build_extrusion(feature_profiles(f), f, f.base);
		const r = built.error ? built : await apply_history(built[0], solid_history(solid));
		if (r.error) {
			error(__("{0}: {1}", [solid.entity_id, r.error]));
			continue;
		}
		session.apply(__("Rebuild Extrusion"), (w) => w.put({ ...session.entities.get(solid.entity_id), ...solid_values(r), metadata: { ...(solid.metadata || {}), feature: built[0].feature } }));
		done++;
	}
	message(__("{0} of {1} extrusion(s) rebuilt from their changed outlines.", [done, stale.length]), "result");
	tick.value++;
}

// --------------------------------------------------------------------- finishing operations (feature history)

// A solid's finishing operations, in order (metadata.history): fillet / chamfer / shell, applied after its
// extrusion (metadata.feature) or, for other solids, after the shape it had first (metadata.base_ref).
const DRESS = {
	fillet: { label: __("Fillet"), size: "radius", size_label: __("Radius") },
	chamfer: { label: __("Chamfer"), size: "distance", size_label: __("Distance") },
	shell: { label: __("Shell"), size: "thickness", size_label: __("Wall thickness") },
};

function solid_history(e) {
	return (e && e.metadata && e.metadata.history) || [];
}

function history_label(h) {
	const d = DRESS[h.op];
	if (h.op === "shell") return `${d.label} ${format_number(h.thickness, 3)} · ${h.open === "top" ? __("open top") : __("closed")}`;
	return `${d.label} ${format_number(h[d.size], 3)} · ${__({ all: "all edges", vertical: "vertical edges", top: "top edges", bottom: "bottom edges" }[h.edges] || h.edges)}`;
}

// apply operations one after another to a built (or stored) solid: the last result, or {error}
async function apply_history(start, history) {
	let current = start;
	for (const h of history) {
		const out = await build_solids([{ key: h.op, op: h.op, geometry_ref: current.geometry_ref, ...h }]);
		if (!out || !out[0].geometry_ref) return { error: `${history_label(h)}: ${(out && out[0].message) || __("failed")}` };
		current = out[0];
	}
	return current;
}

// the solid rebuilt from its base with this history
async function rebuild_solid(solid, history, label) {
	let start;
	const f = feature_of(solid);
	if (f) {
		const built = await build_extrusion(feature_profiles(f), f, f.base);
		if (built.error) return error(built.error);
		start = built[0];
	} else {
		start = { geometry_ref: (solid.metadata && solid.metadata.base_ref) || solid.geometry_ref };
	}
	const r = await apply_history(start, history);
	if (r.error) return error(r.error);
	session.apply(label, (w) => w.put({ ...session.entities.get(solid.entity_id), ...solid_values(r), metadata: { ...(solid.metadata || {}), history } }));
	message(__("{0}: volume {1}.", [label, format_number(r.volume, 3)]), "result");
	tick.value++;
	return true;
}

async function ask_dress(op, preset = {}) {
	const d = DRESS[op];
	const fields = [{ key: d.size, label: d.size_label, type: "number", default: preset[d.size] ?? 1, required: true }];
	if (op === "shell") fields.push({ key: "open", label: __("Faces"), type: "select", options: [{ value: "top", label: __("Open at the top (flat top face removed)") }, { value: "none", label: __("Closed (hollow inside)") }], default: preset.open || "top" });
	else fields.push({ key: "edges", label: __("Edges"), type: "select", options: [{ value: "all", label: __("All edges") }, { value: "vertical", label: __("Vertical edges") }, { value: "top", label: __("Top edges") }, { value: "bottom", label: __("Bottom edges") }], default: preset.edges || "all" });
	const values = await ask({ title: d.label, fields, submit_label: preset[d.size] !== undefined ? __("Update") : __("Apply"), check: (v) => (v[d.size] > 0 ? null : __("{0} must be greater than zero.", [d.size_label])) });
	return values ? { op, ...values } : null;
}

// Solid → Fillet / Chamfer / Shell: a new step at the end of the selected solid's history
async function dress_solid(op) {
	const solid = selection.value.map((id) => session.entities.get(id)).find((e) => e && e.entity_type === "Solid" && e.geometry_ref);
	if (!solid) return error(__("Select a solid."));
	const step = await ask_dress(op);
	if (!step) return;
	const r = await apply_history({ geometry_ref: solid.geometry_ref }, [step]);
	if (r.error) return error(r.error);
	const metadata = { ...(solid.metadata || {}), history: [...solid_history(solid), step] };
	if (!feature_of(solid) && !metadata.base_ref) metadata.base_ref = solid.geometry_ref; // what the history starts from
	session.apply(DRESS[op].label, (w) => w.put({ ...solid, ...solid_values(r), metadata }));
	message(__("{0}: volume {1}.", [DRESS[op].label, format_number(r.volume, 3)]), "result");
	if (object_panel.value && object_panel.value.ids[0] === solid.entity_id) open_object([solid.entity_id]);
	tick.value++;
}

async function edit_history_step(index) {
	const solid = session.entities.get(object_panel.value.ids[0]);
	const history = [...solid_history(solid)];
	const step = await ask_dress(history[index].op, history[index]);
	if (!step) return;
	history[index] = step;
	if (await rebuild_solid(solid, history, __("Edit {0}", [DRESS[step.op].label]))) open_object([solid.entity_id]);
}

async function remove_history_step(index) {
	const solid = session.entities.get(object_panel.value.ids[0]);
	const history = solid_history(solid).filter((_h, i) => i !== index);
	if (await rebuild_solid(solid, history, __("Remove {0}", [DRESS[solid_history(solid)[index].op].label]))) open_object([solid.entity_id]);
}

// Esc: one thing at a time — the running tool, then the object tool window, then the selection
function on_escape() {
	if (!tools.active && object_panel.value) return close_object();
	run("cancel");
}

function on_double_click(event) {
	if (!model.value || tools.active) return;
	const id = viewport.pick(event);
	if (!id) return;
	select_ids([id]);
	// a pit, road, block or drill design reopens its generator (prefilled); anything else its tool window
	const design = mining_commands && mining_commands.design_of(session.entities.get(id));
	if (design && design.params && editable.value) return run("edit_design", id);
	open_object([id]);
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
	if (in_input || dialog.value || form.value) return;
	if (event.key === "Escape") return on_escape();
	if (ctrl && event.key.toLowerCase() === "z") return event.preventDefault(), run("undo");
	if (ctrl && event.key.toLowerCase() === "y") return event.preventDefault(), run("redo");
	if (ctrl && event.key.toLowerCase() === "a" && event.target !== command_el.value) return event.preventDefault(), run("select_all");
	if (event.key === "F3") return event.preventDefault(), run("toggle", "object_snap");
	if (event.key === "F7") return event.preventDefault(), run("toggle_grid");
	if (event.key === "F8") return event.preventDefault(), run("toggle", "ortho");
	if (event.key === "F9") return event.preventDefault(), run("toggle", "grid_snap");
	if (event.key === "F10") return event.preventDefault(), run("toggle", "polar");
	if (event.key === "Delete" && event.target !== command_el.value) return run("erase");
	// arrow keys turn the view (not while typing a command)
	const arrows = { ArrowLeft: [-15, 0], ArrowRight: [15, 0], ArrowUp: [0, 10], ArrowDown: [0, -10] };
	if (arrows[event.key] && model.value && !(event.target === command_el.value && command_text.value)) {
		event.preventDefault();
		const step = event.shiftKey ? 1 / 3 : 1;
		return run("orbit", arrows[event.key].map((v) => v * step));
	}
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
	probe_surface(event);
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
	if (viewport.orbit_just_ended()) return; // that right-drag turned the view
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
	// the view was still on the old origin (kilometres away): look at the new geometry, or Top / orbit swing away
	const target = viewport.to_model(viewport.controls.target);
	if (Math.max(Math.abs(target[0] - point[0]), Math.abs(target[1] - point[1])) > FAR) viewport.look_at_point(point);
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
	if (compare_ref) refs.push(compare_ref);
	viewport.set_references(refs, session);
}

// --------------------------------------------------------------------- models and revisions

async function open_model(name, force = false) {
	if (!force && model.value && model.value.name !== name && change_count.value && !window.confirm(__("Discard {0} unsaved change(s)?", [change_count.value]))) return;
	loading.value = __("Opening {0}…", [name]);
	try {
		tools.cancel(true);
		analysis.value = null;
		if (viewport.section) surface_commands.set_section(null);
		section_on.value = false;
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
		// a link from Geo Planning (Mining Block → Open in CAD Workstation): show that object
		const wanted = url.searchParams.get("entity");
		if (wanted && session.entities.has(wanted)) zoom_ids([wanted]);
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
		__("Turning the view (3D): right-drag, Shift+middle-drag or Alt+left-drag turns it about the point under the cursor (marked while you drag). The orbit button (or ORBIT) makes plain left-drag orbit, for trackpads; Esc ends it. Arrow keys turn 15° / tilt 10° (Shift: finer); the buttons at the bottom right do the same, plus Top and Iso."),
		__("Surfaces: Surface → Create Surface builds a TIN from the selected points, lines (as breaklines), surfaces and GIS datasets, inside an optional boundary. Save before running analyses: they use the saved surfaces. Volumes are exact (TIN to TIN) and recorded."),
		__("Solids: select two or more saved solids for Union / Subtract / Intersect; a closed polyline + a line for Revolve; closed polylines at different elevations for Loft; a closed polyline + a path for Sweep."),
		__("Mining: choose the model's Design Standard, select a closed floor outline → Generate Pit Benches; a centreline → Haul Road; a closed limit → Mining Blocks; a blast boundary → Drill Pattern (hole layout only). Save, then Validate Design against Standard: the result is recorded with the standard version."),
		__("Shapes: drawing a closed shape (or double-clicking an object, or Properties → Edit…) opens its tool window: layer, colour, line weight, line type, and Outline / Filled / Solid. Solid extrudes it: drag the orange arrow or type the distance; direction, taper and New body / Join / Cut / Intersect; OK builds it with Open CASCADE. EXT opens it for the selected outlines."),
		__("Keys: Esc cancel · Enter finish · Del erase · Ctrl+S save · Ctrl+Z/Y undo/redo · F3 object snap · F7 grid · F8 ortho · F9 grid snap · F10 polar tracking."),
	].forEach((t) => message(t, "help"));
}

function show_about() {
	[
		__("CAD Engineering Workstation — part of is_production (GPL-3.0-or-later), Isambane Mining (Pty) Ltd."),
		__("Geometry: Open CASCADE Technology {0} (LGPL-2.1 with exception) via cadquery-ocp (Apache-2.0), run in an isolated worker. DXF: ezdxf (MIT); reprojection: pyproj/PROJ (MIT).", [(config.health && config.health.occt_version) || ""]),
		__("Surfaces: PythonCDT / CDT (MPL-2.0), NumPy and SciPy (BSD), Matplotlib point location (PSF-based), laspy (BSD-2-Clause) and lazrs (MIT) for LAS/LAZ."),
		__("Mining design outlines: Shapely / GEOS (BSD-3-Clause / LGPL-2.1)."),
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
	viewport = markRaw(
		new Viewport(viewport_el.value, {
			on_view_change: (v) => {
				grid_step.value = v.grid_step;
				if (extrude_preview) place_extrude_label();
			},
			on_mode_change: (mode) => (view_mode.value = mode), // an orbit started in plan switches to 3D
		})
	);
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
			on_created: (e) => shape_mode(e) && open_object([e.entity_id]), // a drawn shape: outline / filled / solid
			define_block,
			visible_entities,
			cutters: () => [...visible_entities(), ...(viewport.reference_entities || [])],
		})
	);
	surface_commands = markRaw(
		new SurfaceCommands({
			session,
			viewport,
			config,
			model: () => model.value,
			selection: () => selection.value,
			select: (ids) => select_ids(ids),
			message,
			ask,
			run_job,
			build_solids,
			current_layer: () => current_layer_key.value,
			show_result,
		})
	);
	mining_commands = markRaw(
		new MiningCommands({
			session,
			viewport,
			config,
			model: () => model.value,
			selection: () => selection.value,
			select: (ids) => select_ids(ids),
			message,
			ask,
			run_job,
			current_layer: () => current_layer_key.value,
			show_result,
			zoom: (ids) => zoom_ids(ids),
			surfaces: surface_commands,
		})
	);
	cloud_commands = markRaw(
		new CloudCommands({
			session,
			viewport,
			config,
			model: () => model.value,
			selection: () => selection.value,
			select: (ids) => select_ids(ids),
			message,
			ask,
			run_job,
			show_result,
			surfaces: surface_commands,
		})
	);
	planning_commands = markRaw(
		new PlanningCommands({
			session,
			viewport,
			config,
			model: () => model.value,
			selection: () => selection.value,
			select: (ids) => select_ids(ids),
			message,
			ask,
			run_job,
			show_result,
				set_compare_overlay: (ref) => {
				compare_ref = ref;
				compare_on.value = Boolean(ref);
				load_references();
			},
		})
	);
	viewport.surfaces.on_loaded = on_surface_loaded;
	viewport.clouds.on_loaded = on_cloud_loaded;
	window.__cad = { viewport, session, tools, surface_commands, mining_commands, cloud_commands, select: select_ids, run }; // for the browser console and automated UI tests
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
