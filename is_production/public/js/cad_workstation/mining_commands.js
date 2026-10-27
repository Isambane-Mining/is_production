// Phase 4 commands (Mining menu): the model's design standard, open-pit bench generation and pushbacks, haul roads,
// mining blocks (generate, subdivide, classify, sequence, report) and design validation. Geometry is made on the
// server (Mining Design jobs); the results are added to the model as one undoable edit, as with surfaces.

import { call } from "./api";
import { closed_loop, new_entity_id } from "./geometry";
import { entity_label } from "./surface_commands";

const MATERIAL_COLOURS = ["#c9a227", "#6b8e23", "#8b5a2b", "#4682b4", "#a0522d", "#708090", "#9acd32", "#cd853f", "#5f9ea0", "#b8860b"];

export function material_colour(material, materials = []) {
	if (!material) return null;
	const i = materials.indexOf(material);
	let h = i >= 0 ? i : [...material].reduce((a, c) => a + c.charCodeAt(0), 0);
	return MATERIAL_COLOURS[h % MATERIAL_COLOURS.length];
}

export class MiningCommands {
	// ctx: as SurfaceCommands, plus surfaces (the SurfaceCommands instance), refresh_model()
	constructor(ctx) {
		this.ctx = ctx;
		this.materials = [];
	}

	get session() {
		return this.ctx.session;
	}

	get sc() {
		return this.ctx.surfaces;
	}

	async run(command, args) {
		const handler = {
			mining_standard: () => this.choose_standard(),
			pit_generate: () => this.pit(args || {}),
			pit_stages: () => this.stages(),
			road_design: () => this.road(),
			blocks_generate: () => this.blocks(),
			blocks_subdivide: () => this.subdivide(),
			blocks_material: () => this.material(),
			blocks_sequence: () => this.sequence(),
			blocks_report: () => this.report(),
			design_validate: () => this.validate(),
			drill_pattern: () => this.drill(),
			edit_design: () => this.edit_design(args),
			drill_report: () => this.drill_report(),
			geology_build: () => this.geology(),
			block_geology: () => this.block_geology(),
			pit_shells: () => this.pit_shells(),
		}[command];
		if (!handler) return false;
		await handler();
		return true;
	}

	async standard() {
		return call("get_design_standard", { model: this.ctx.model().name });
	}

	// -------------------------------------------------------------- standard

	async choose_standard() {
		const [rows, current] = await Promise.all([call("list_design_standards"), this.standard()]);
		const active = rows.filter((r) => r.status === "Active");
		if (!active.length) throw new Error(__("There is no Active CAD Design Standard. Make one in the Desk (CAD Design Standard) and activate it."));
		const values = await this.ctx.ask({
			title: __("Design Standard"),
			intro: current ? __("The model is designed against {0} ({1}, version {2}).", [current.name, current.standard_name, current.version]) : __("The model has no design standard yet."),
			fields: [
				{
					key: "standard",
					label: __("Standard"),
					type: "select",
					options: [{ value: "", label: __("None") }, ...active.map((r) => ({ value: r.name, label: `${r.name} · ${r.standard_name}${r.applicable_location ? " · " + r.applicable_location : ""}` }))],
					default: current ? current.name : active[0].name,
				},
			],
			submit_label: __("Use"),
		});
		if (!values) return;
		const std = await call("set_design_standard", { model: this.ctx.model().name, standard: values.standard || null });
		this.ctx.model().design_standard = std ? std.name : null;
		this.ctx.message(std ? __("Design standard: {0} — bench {1} m, face {2}°, berm {3} m, road {4} m at ≤ {5} %.", [std.name, std.bench_height, std.face_angle, std.berm_width, std.road_width, std.maximum_gradient]) : __("No design standard."), "result");
	}

	surface_choices(none_label) {
		return [{ value: "", label: none_label }, ...this.sc.surfaces().map((e) => ({ value: e.geometry_ref, label: entity_label(e) }))];
	}

	saved_surface(ref) {
		if (!ref) return;
		const e = this.sc.surfaces().find((x) => x.geometry_ref === ref);
		if (e) this.sc.require_saved([e]);
	}

	ensure_layer(name, color) {
		return this.sc.ensure_layer(name, color);
	}

	surface_entity(name, surface_type, layer, result, design) {
		const v = result.stats.validation || {};
		return {
			entity_id: new_entity_id(),
			entity_type: "Surface",
			cad_layer: layer,
			geometry: { name, surface_type, source: result.source },
			geometry_ref: result.geometry_ref,
			bbox: result.stats.bbox,
			area: result.stats.surface_area,
			validation_status: v.ok === false ? "Warning" : "Valid",
			metadata: design || null,
			_stats: result.stats,
		};
	}

	polyline(points, closed, layer, metadata, color = null) {
		return { entity_id: new_entity_id(), entity_type: "Polyline", cad_layer: layer, color, validation_status: "Not Checked", geometry: { points, closed }, metadata };
	}

	// -------------------------------------------------------------- editable designs

	// A generator's form; when editing a design, prefilled with the values it was made with.
	ask(edit, form) {
		if (edit) {
			for (const f of form.fields) if (edit.params[f.key] !== undefined && edit.params[f.key] !== null) f.default = edit.params[f.key];
			form.submit_label = __("Regenerate");
			form.intro = __("Regenerates the whole design from these values and the source outline as it is now; the old one is replaced (one undo step).") + (form.intro ? " " + form.intro : "");
		}
		return this.ctx.ask(form);
	}

	// the saved entity a design was made from (floor, centreline, boundary)
	source(id) {
		const e = this.session.entities.get(id);
		if (!e) throw new Error(__("The outline this design was made from ({0}) no longer exists.", [id]));
		this.sc.require_saved([e]);
		return [e];
	}

	// put the new design's entities in, and take the old design's out, as one undo step
	replace(edit, label, entities) {
		this.session.apply(edit ? __("Regenerate {0}", [label]) : label, (w) => {
			for (const id of (edit && edit.group) || []) w.remove(id);
			entities.forEach((e) => w.put(e));
		});
	}

	// What design an entity belongs to: {kind, label, group (entity ids), params} or null.
	design_of(entity) {
		const m = (entity && entity.metadata) || {};
		const all = [...this.session.entities.values()];
		const group = (key, test) => all.filter((e) => e.metadata && e.metadata[key] && test(e.metadata[key])).map((e) => e.entity_id);
		const params_in = (key, ids) => ids.map((id) => this.session.entities.get(id).metadata[key].params).find(Boolean);
		if (m.pit_shell) {
			const ids = group("pit_shell", (d) => d.optimisation === m.pit_shell.optimisation);
			return { kind: "pit_shells", label: __("Pit Shell"), group: ids, params: params_in("pit_shell", ids) };
		}
		if (m.geology) {
			const ids = group("geology", (d) => d.model === m.geology.model);
			return { kind: "geology", label: __("Geology {0}", [m.geology.geo_project]), group: ids, params: params_in("geology", ids) };
		}
		if (m.pit_design && m.pit_design.run) {
			const ids = group("pit_design", (d) => d.run === m.pit_design.run);
			return { kind: "stages", label: __("Pit Stages"), group: ids, params: params_in("pit_design", ids) };
		}
		if (m.pit_design) {
			const ids = group("pit_design", (d) => d.stage === m.pit_design.stage);
			return { kind: "pit", label: __("Pit {0}", [m.pit_design.stage]), group: ids, params: params_in("pit_design", ids) };
		}
		if (m.haul_road) {
			const ids = group("haul_road", (d) => d.name === m.haul_road.name);
			return { kind: "road", label: m.haul_road.name, group: ids, params: params_in("haul_road", ids) };
		}
		if (m.mining_block) {
			const run = m.mining_block.run;
			const ids = run ? group("mining_block", (d) => d.run === run) : [entity.entity_id];
			return { kind: "blocks", label: __("Mining Blocks"), group: ids, params: run ? params_in("mining_block", ids) : null, run };
		}
		if (m.drill_hole) {
			const ids = group("drill_hole", (d) => d.pattern === m.drill_hole.pattern);
			return { kind: "drill", label: m.drill_hole.pattern, group: ids, params: params_in("drill_hole", ids) };
		}
		return null;
	}

	async edit_design(entity_id = null) {
		const id = entity_id || this.ctx.selection()[0];
		const d = this.design_of(this.session.entities.get(id));
		if (!d) throw new Error(__("Select a pit, road, block or drill design object."));
		if (!d.params) throw new Error(__("{0} was made before designs became editable: generate it again to be able to edit it.", [d.label]));
		const dirty = d.group.filter((x) => this.session.dirty.has(x));
		if (d.kind === "blocks" && dirty.length) this.ctx.message(__("Regenerating replaces the blocks; their materials are kept by block id, other edits (subdivisions, sequence) are not."), "warning");
		const edit = { group: d.group, params: d.params, run: d.run };
		if (d.kind === "pit") return this.pit({ edit });
		if (d.kind === "stages") return this.stages(edit);
		if (d.kind === "geology") return this.geology(edit);
		if (d.kind === "pit_shells") return this.pit_shells(edit);
		if (d.kind === "road") return this.road(edit);
		if (d.kind === "blocks") return this.blocks(edit);
		return this.drill(edit);
	}

	// -------------------------------------------------------------- open pit

	async pit({ pushback, edit }) {
		const floors = edit ? this.source(edit.params.floor) : this.sc.selected((e) => closed_loop(e) || (e.entity_type === "Polyline" && e.geometry.closed));
		if (floors.length !== 1) throw new Error(pushback ? __("Select the floor outline of the previous stage (a closed polyline).") : __("Select the pit floor outline (one closed polyline at the floor elevation)."));
		this.sc.require_saved(floors);
		const std = (await this.standard()) || {};
		const stages = new Set([...this.session.entities.values()].map((e) => e.metadata && e.metadata.pit_design && e.metadata.pit_design.stage).filter(Boolean));
		let n = stages.size + 1;
		while (stages.has(__("Stage {0}", [n]))) n++;
		const ground = this.sc.surfaces().find((e) => /survey|terrain|ground/i.test(`${e.geometry.surface_type} ${e.geometry.name}`));
		const values = await this.ask(edit, {
			title: edit ? __("Edit Pit {0}", [edit.params.stage]) : pushback ? __("Pit Stage / Pushback") : __("Generate Pit Benches"),
			intro: std.name
				? __("From the floor outward and upward, with {0}: bench {1} m, face {2}°, berm {3} m. Change any value for this design only; the validation checks it against the standard.", [std.name, std.bench_height, std.face_angle, std.berm_width])
				: __("The model has no design standard (Mining → Design Standard): give the bench geometry here."),
			fields: [
				{ key: "stage", label: __("Stage name"), default: __("Stage {0}", [n]), required: true },
				{ key: "ground", label: __("Ground surface"), type: "select", options: this.surface_choices(__("None")), default: ground ? ground.geometry_ref : "", help: __("For the top elevation, the daylight line, the post-mining surface and the pit volume.") },
				{ key: "top_elevation", label: __("Top elevation"), type: "number", help: __("Empty: the highest point of the ground surface.") },
				{ key: "floor_elevation", label: __("Floor elevation"), type: "number", help: __("Empty: the outline's own elevation.") },
				{ key: "floor_offset", label: pushback ? __("Push back the floor by (m)") : __("Expand the floor by (m)"), type: "number", default: pushback ? std.berm_width || 20 : 0 },
				{ key: "snap_to_benches", label: __("Bench levels"), type: "check", check_label: __("Put the floor on the standard's bench levels"), default: Boolean(std.name) },
				{ key: "bench_height", label: __("Bench height (m)"), type: "number", default: std.bench_height || 10 },
				{ key: "face_angle", label: __("Face angle (°)"), type: "number", default: std.face_angle || 70 },
				{ key: "berm_width", label: __("Berm width (m)"), type: "number", default: std.berm_width ?? 8 },
				{ key: "geotech_every", label: __("Geotechnical berm every (benches)"), type: "number", default: std.geotechnical_berm_every || 0 },
				{ key: "geotech_width", label: __("Geotechnical berm width (m)"), type: "number", default: std.geotechnical_berm_width || 0 },
				{
					key: "corners",
					label: __("Corners"),
					type: "select",
					options: [
						{ value: "round", label: __("Rounded (constant berm width)") },
						{ value: "mitre", label: __("Square (mitred)") },
					],
					default: "round",
				},
				{ key: "post_mining", label: __("Post-mining surface"), type: "check", check_label: __("Make the ground-after-mining surface (needs the ground)"), default: true },
				...this.ramp_fields(std),
			],
			submit_label: __("Generate"),
			check: (v) => (v.bench_height > 0 && v.face_angle > 0 && v.face_angle < 90 ? null : __("Give a bench height and a face angle between 0° and 90°.")),
		});
		if (!values) return;
		this.saved_surface(values.ground);
		const params = { floor: floors[0].entity_id, ...values, ground: values.ground || null };
		for (const k of ["top_elevation", "floor_elevation"]) if (values[k] === "" || values[k] === null) delete params[k];
		const done = await this.ctx.run_job("mining_job", { model: this.ctx.model().name, operation: "pit", params });
		if (!done) return;
		const r = done.results;
		const entities = this.pit_entities(r, params);
		this.replace(edit, __("Pit {0}", [r.stage]), entities);
		this.ctx.select(entities.filter((e) => e.entity_type === "Surface").map((e) => e.entity_id));
		this.ctx.show_result({ kind: "pit_report", title: __("Pit {0}", [r.stage]), result: r });
		this.ctx.message(done.status_message, "result");
	}

	// the in-pit ramp: a road spiralling down the wall, cut into the benches
	ramp_fields(std) {
		const on = (v) => Boolean(v.ramp);
		return [
			{ key: "ramp", label: __("In-pit ramp"), type: "check", check_label: __("Cut a ramp into the wall, from the daylight line down to the floor"), default: false },
			{ key: "ramp_width", label: __("Ramp width (m)"), type: "number", default: std.road_width || 25, show_if: on },
			{ key: "ramp_gradient", label: __("Ramp gradient (%)"), type: "number", default: std.maximum_gradient || 10, show_if: on },
			{ key: "ramp_bearing", label: __("Ramp entry (bearing from the floor centre, °)"), type: "number", default: 0, show_if: on },
			{
				key: "ramp_direction",
				label: __("Going down"),
				type: "select",
				options: [
					{ value: "clockwise", label: __("Clockwise") },
					{ value: "anticlockwise", label: __("Anticlockwise") },
				],
				default: "clockwise",
				show_if: on,
			},
			{ key: "ramp_batter", label: __("Ramp batters (°)"), type: "number", help: __("Empty: the face angle."), show_if: on },
		];
	}

	// a pit job's result as model entities: rings, daylight, ramp, pit and post-mining surfaces
	pit_entities(r, params, extra = {}) {
		const stage = r.stage;
		const base = `PIT ${stage.toUpperCase()}`;
		const toe = this.ensure_layer(`${base} TOE`, "#c97b3a");
		const crest = this.ensure_layer(`${base} CREST`, "#e8b04a");
		const surf = this.ensure_layer(base, "#d9a441");
		const day = this.ensure_layer(`${base} DAYLIGHT`, "#e5534b");
		const design = (kind, more = {}) => ({ pit_design: { stage, kind, standard: r.standard, ...extra, ...more } });
		const entities = r.rings.map((ring) => this.polyline(ring.points, true, ring.kind === "crest" ? crest : toe, design(ring.kind, { bench: ring.bench, elevation: ring.elevation })));
		entities.push(...r.daylight.map((line) => this.polyline(line.points, line.closed, day, design("daylight"))));
		if (r.ramp) {
			const ramp = this.ensure_layer(`${base} RAMP`, "#bdbdbd");
			const L = r.ramp.lines;
			const road = { haul_road: { name: __("Ramp {0}", [stage]), role: "centreline", width: r.ramp.width, standard: r.standard } };
			entities.push(this.polyline(L.centre, false, ramp, { ...design("ramp"), ...road }, "#ffffff"));
			entities.push(this.polyline(L.left_edge, false, ramp, design("ramp_edge")), this.polyline(L.right_edge, false, ramp, design("ramp_edge")));
			entities.push(this.polyline(L.left_daylight, false, day, design("ramp_daylight")), this.polyline(L.right_daylight, false, day, design("ramp_daylight")));
		}
		entities.push(this.surface_entity(__("Pit {0}", [stage]), "Pit Surface", surf, r.surface, design("surface", { params })));
		if (r.post_mining) entities.push(this.surface_entity(__("Post-mining {0}", [stage]), "Design Surface", surf, r.post_mining, design("post_mining")));
		return entities;
	}

	// -------------------------------------------------------------- pit stages

	async stages(edit = null) {
		const floors = edit ? this.source(edit.params.floor) : this.sc.selected((e) => closed_loop(e) || (e.entity_type === "Polyline" && e.geometry.closed));
		if (floors.length !== 1) throw new Error(__("Select the final pit floor outline (one closed polyline)."));
		this.sc.require_saved(floors);
		const std = (await this.standard()) || {};
		const ground = this.sc.surfaces().find((e) => /survey|terrain|ground/i.test(`${e.geometry.surface_type} ${e.geometry.name}`));
		const outline_z = (floors[0].geometry.points || [[0, 0, 0]])[0][2] || 0;
		const values = await this.ask(edit, {
			title: edit ? __("Edit Pit Stages") : __("Pit Stages (automatic)"),
			intro: __("A run of nested pits from one floor outline, each deeper (and wider) than the one before: every stage is a full pit design. The report gives each stage's incremental volume, whether it contains the stage before and the narrowest pushback."),
			fields: [
				{ key: "prefix", label: __("Stage names"), default: __("Stage"), required: true, help: __("Numbered: Stage 1, Stage 2, …") },
				{ key: "count", label: __("Number of stages"), type: "number", default: 3 },
				{ key: "ground", label: __("Ground surface"), type: "select", options: this.surface_choices(__("Choose…")), default: ground ? ground.geometry_ref : "" },
				{ key: "top_elevation", label: __("Top elevation"), type: "number", help: __("Empty: the highest point of the ground surface.") },
				{ key: "first_floor", label: __("First stage floor elevation"), type: "number", default: outline_z + 3 * (std.bench_height || 10) },
				{ key: "final_floor", label: __("Final floor elevation"), type: "number", default: outline_z },
				{ key: "first_offset", label: __("First stage floor offset (m)"), type: "number", default: 0, help: __("Negative shrinks the first stage's floor; the offset grows evenly to 0 at the final stage (pushbacks).") },
				{ key: "min_pushback_width", label: __("Minimum pushback width (m)"), type: "number", default: std.minimum_floor_width || 40 },
				{ key: "snap_to_benches", label: __("Bench levels"), type: "check", check_label: __("Put each floor on the standard's bench levels"), default: Boolean(std.name) },
				{ key: "bench_height", label: __("Bench height (m)"), type: "number", default: std.bench_height || 10 },
				{ key: "face_angle", label: __("Face angle (°)"), type: "number", default: std.face_angle || 70 },
				{ key: "berm_width", label: __("Berm width (m)"), type: "number", default: std.berm_width ?? 8 },
				{ key: "geotech_every", label: __("Geotechnical berm every (benches)"), type: "number", default: std.geotechnical_berm_every || 0 },
				{ key: "geotech_width", label: __("Geotechnical berm width (m)"), type: "number", default: std.geotechnical_berm_width || 0 },
				{
					key: "corners",
					label: __("Corners"),
					type: "select",
					options: [
						{ value: "round", label: __("Rounded (constant berm width)") },
						{ value: "mitre", label: __("Square (mitred)") },
					],
					default: "round",
				},
				...this.ramp_fields(std),
			],
			submit_label: __("Generate Stages"),
			check: (v) => {
				if (!v.ground) return __("Choose the ground surface: stage volumes are measured from it.");
				if (!(v.count >= 1 && v.count <= 12)) return __("Give 1 to 12 stages.");
				if (v.count > 1 && !(Number(v.first_floor) > Number(v.final_floor))) return __("The first stage's floor must be above the final floor.");
				return v.bench_height > 0 && v.face_angle > 0 && v.face_angle < 90 ? null : __("Give a bench height and a face angle between 0° and 90°.");
			},
		});
		if (!values) return;
		this.saved_surface(values.ground);
		const n = Math.round(values.count);
		const stages = Array.from({ length: n }, (_x, i) => {
			const t = n > 1 ? i / (n - 1) : 1;
			return { stage: `${values.prefix} ${i + 1}`, floor_elevation: Number(values.first_floor) + (Number(values.final_floor) - Number(values.first_floor)) * t, floor_offset: Number(values.first_offset || 0) * (1 - t) };
		});
		const params = { floor: floors[0].entity_id, ...values, stages };
		if (values.top_elevation === "" || values.top_elevation === null) delete params.top_elevation;
		const done = await this.ctx.run_job("mining_job", { model: this.ctx.model().name, operation: "stages", params });
		if (!done) return;
		const r = done.results;
		const entities = r.stages.flatMap((st) => this.pit_entities(st, params, { run: r.run }));
		this.replace(edit, __("Pit Stages"), entities);
		this.ctx.select(entities.filter((e) => e.entity_type === "Surface" && e.metadata.pit_design.kind === "post_mining").map((e) => e.entity_id));
		this.ctx.show_result({ kind: "stages_report", title: __("Pit Stages"), result: r });
		this.ctx.message(done.status_message, "result");
	}

	// -------------------------------------------------------------- haul roads

	async road(edit = null) {
		const lines = edit ? this.source(edit.params.centreline) : this.sc.selected((e) => e.entity_type === "Line" || (e.entity_type === "Polyline" && !e.geometry.closed));
		if (lines.length !== 1) throw new Error(__("Select the road centreline (one line or open polyline)."));
		this.sc.require_saved(lines);
		const std = (await this.standard()) || {};
		const ground = this.sc.surfaces().find((e) => /survey|terrain|ground|post/i.test(`${e.geometry.surface_type} ${e.geometry.name}`));
		const values = await this.ask(edit, {
			title: __("Haul Road from Centreline"),
			intro: __("Curves of the turning radius are fitted at every bend; edges, superelevation, batters to the ground, the road surface and its cut and fill follow."),
			fields: [
				{ key: "name", label: __("Road name"), default: __("Haul Road {0}", [new Set([...this.session.entities.values()].map((e) => e.metadata && e.metadata.haul_road && e.metadata.haul_road.name).filter(Boolean)).size + 1]), required: true },
				{ key: "ground", label: __("Ground surface"), type: "select", options: this.surface_choices(__("None (no batters or volumes)")), default: ground ? ground.geometry_ref : "" },
				{
					key: "vertical",
					label: __("Vertical alignment"),
					type: "select",
					options: [
						{ value: "keep", label: __("Keep the centreline's elevations") },
						{ value: "drape", label: __("On the ground surface") },
						{ value: "grade", label: __("Constant grade from a start elevation") },
						{ value: "linear", label: __("Straight between start and end elevations") },
						{ value: "profile", label: __("Vertical intersection points (PVIs)") },
					],
					default: "keep",
				},
				{ key: "pvis", label: __("PVIs (chainage:elevation, …)"), default: "", show_if: (v) => v.vertical === "profile", help: __("e.g. 0:1500, 250:1480, 600:1452") },
				{ key: "vertical_curve_length", label: __("Vertical curve length (m)"), type: "number", default: std.vertical_curve_length ?? 0, show_if: (v) => ["keep", "profile"].includes(v.vertical), help: __("A parabola at every grade change (keep: at the centreline's vertices). 0: none.") },
				{ key: "start_elevation", label: __("Start elevation"), type: "number", show_if: (v) => ["grade", "linear"].includes(v.vertical) },
				{ key: "gradient", label: __("Grade (%, + rising)"), type: "number", default: std.maximum_gradient ? -std.maximum_gradient : -8, show_if: (v) => v.vertical === "grade" },
				{ key: "end_elevation", label: __("End elevation"), type: "number", show_if: (v) => v.vertical === "linear" },
				{ key: "width", label: __("Road width (m)"), type: "number", default: std.road_width || 25 },
				{ key: "fit_radius", label: __("Curve radius (m)"), type: "number", default: std.minimum_turning_radius || 40, help: __("0: keep the polyline's own bends.") },
				{ key: "crossfall", label: __("Crossfall (%)"), type: "number", default: std.normal_crossfall ?? 2 },
				{ key: "superelevation", label: __("Maximum superelevation (%)"), type: "number", default: std.maximum_superelevation ?? 0 },
				{ key: "cut_batter", label: __("Cut batter (°)"), type: "number", default: std.cut_batter_angle || 45 },
				{ key: "fill_batter", label: __("Fill batter (°)"), type: "number", default: std.fill_batter_angle || 34 },
			],
			submit_label: __("Design Road"),
			check: (v) => {
				if (["grade", "linear"].includes(v.vertical) && (v.start_elevation === "" || v.start_elevation === null)) return __("Give the start elevation.");
				if (v.vertical === "profile" && (v.pvis || "").split(/[,;\n]/).filter((x) => x.trim()).length < 2) return __("Give at least two PVIs.");
				return null;
			},
		});
		if (!values) return;
		this.saved_surface(values.ground);
		const params = { centreline: lines[0].entity_id, ...values, ground: values.ground || null };
		const done = await this.ctx.run_job("mining_job", { model: this.ctx.model().name, operation: "road", params });
		if (!done) return;
		const r = done.results;
		const layer = this.ensure_layer(`ROAD ${r.name.toUpperCase()}`, "#bdbdbd");
		const design = (role) => ({ haul_road: { name: r.name, role, width: r.options.width, standard: r.standard } });
		const L = r.lines;
		const entities = [this.polyline(L.centre, false, layer, { haul_road: { ...design("centreline").haul_road, params, min_k: r.min_k } }, "#ffffff"), this.polyline(L.left_edge, false, layer, design("edge")), this.polyline(L.right_edge, false, layer, design("edge"))];
		if (L.left_daylight) entities.push(this.polyline(L.left_daylight, false, layer, design("daylight"), "#e5534b"), this.polyline(L.right_daylight, false, layer, design("daylight"), "#e5534b"));
		entities.push(this.surface_entity(r.name, "Design Surface", layer, r.surface, design("surface")));
		this.replace(edit, r.name, entities);
		this.ctx.select([entities[0].entity_id]);
		this.ctx.show_result({ kind: "road_report", title: r.name, result: r, standard: await this.standard() });
		this.ctx.message(done.status_message, "result");
	}

	// -------------------------------------------------------------- geology (Phase 6)

	// Seam roof / floor surfaces (and the topography, and drillholes) from a Geo Project's model grids or drillholes.
	async geology(edit = null) {
		const projects = await call("list_geo_projects");
		if (!projects.length) throw new Error(__("There is no Geo Project to read geology from (Geo Planning)."));
		let project = edit && edit.params.geo_project;
		if (!project) {
			const first = await this.ctx.ask({
				title: __("Geology from Geo Planning"),
				intro: __("Seam surfaces from a Geo Project's model grids (Geo Model Points) or its drillholes."),
				fields: [{ key: "geo_project", label: __("Geo Project"), type: "select", options: projects.map((p) => ({ value: p.name, label: p.name })), default: projects[0].name }],
				submit_label: __("Next"),
			});
			if (!first) return;
			project = first.geo_project;
		}
		const info = await call("geology_sources", { model: this.ctx.model().name, geo_project: project });
		const grids = info.seams.length > 0;
		const holes = info.drillholes.holes > 0;
		if (!grids && !holes) throw new Error(__("{0} has no seam grids (roof and floor) and no drillholes with seams.", [project]));
		const seam_fields = info.seams.map((s) => ({
			key: `seam_${s.key}`,
			label: s.key,
			type: "check",
			check_label: `${s.label || s.key} — ${[s.thickness && __("thickness"), s.density && __("density"), ...Object.keys(s.qualities)].filter(Boolean).join(", ") || __("roof and floor only")}${s.part_of ? " · " + __("inside {0}: off, so its coal is not counted twice", [s.part_of]) : ""}`,
			default: !s.part_of,
			show_if: (v) => v.source === "Model Grids",
		}));
		const hole_seams = info.drillholes.seams.map((s) => s.seam);
		const values = await this.ask(edit, {
			title: edit ? __("Edit Geology") : __("Geology: {0}", [project]),
			intro: [
				grids ? __("{0} seams in the model grids ({1} variables).", [info.seams.length, info.variables]) : "",
				holes ? __("{0} drillholes, seams {1}.", [info.drillholes.holes, hole_seams.join(", ") || "—"]) : "",
				info.axis === "Negate X and Y" ? __("The data's y values are negative: its x/y look like easting/northing of this south-orientated model, so X and Y are negated.") : "",
			].filter(Boolean).join(" "),
			fields: [
				{ key: "source", label: __("Source"), type: "select", options: [...(grids ? ["Model Grids"] : []), ...(holes ? ["Drillholes"] : [])], default: grids ? "Model Grids" : "Drillholes" },
				...seam_fields,
				{ key: "topography", label: __("Topography"), type: "select", options: [{ value: "", label: __("None") }, ...info.others.map((o) => ({ value: o.variable, label: o.variable }))], default: info.topography || "", show_if: (v) => v.source === "Model Grids" },
				{ key: "qualities", label: __("Qualities"), type: "check", check_label: __("Density, thickness and quality grids (for tonnes and block qualities)"), default: true, show_if: (v) => v.source === "Model Grids" },
				{ key: "drill_seams", label: __("Seams"), default: hole_seams.join(", "), help: __("Comma-separated; empty: all."), show_if: (v) => v.source === "Drillholes" },
				{
					key: "interpolation",
					label: __("Between holes"),
					type: "select",
					options: [
						{ value: "tin", label: __("Linear (triangulate the intercepts)") },
						{ value: "idw", label: __("Inverse distance grid") },
					],
					default: "tin",
					show_if: (v) => v.source === "Drillholes",
				},
				{ key: "cell", label: __("Grid cell (m)"), type: "number", default: 10, show_if: (v) => v.source === "Drillholes" && v.interpolation === "idw" },
				{ key: "power", label: __("Power"), type: "number", default: 2, show_if: (v) => v.source === "Drillholes" && v.interpolation === "idw" },
				{ key: "radius", label: __("Search radius (m)"), type: "number", help: __("Empty: no limit."), show_if: (v) => v.source === "Drillholes" && v.interpolation === "idw" },
				{ key: "by_ply", label: __("Plies"), type: "check", check_label: __("Model every ply as its own seam"), default: false, show_if: (v) => v.source === "Drillholes" },
				{ key: "collars_as_topography", label: __("Topography"), type: "check", check_label: __("A topography surface through the collars"), default: true, show_if: (v) => v.source === "Drillholes" },
				{ key: "axis", label: __("Axes"), type: "select", options: ["As stored", "Negate X and Y"], default: info.axis },
			],
			submit_label: __("Build Geology"),
			check: (v) => (v.source === "Model Grids" && seam_fields.length && !seam_fields.some((f) => v[f.key]) ? __("Choose at least one seam.") : null),
		});
		if (!values) return;
		const params = { geo_project: project, source: values.source, axis: values.axis };
		if (values.source === "Model Grids") {
			params.seams = info.seams.filter((s) => values[`seam_${s.key}`]).map((s) => s.key);
			params.topography = values.topography || "";
			params.qualities = values.qualities;
		} else {
			params.seams = (values.drill_seams || "").split(",").map((x) => x.trim()).filter(Boolean);
			Object.assign(params, { interpolation: values.interpolation, cell: values.cell, power: values.power, radius: values.radius || null, by_ply: values.by_ply, collars_as_topography: values.collars_as_topography });
		}
		const form = { ...values, ...params };
		const done = await this.ctx.run_job("mining_job", { model: this.ctx.model().name, operation: "geology", params });
		if (!done) return;
		const r = done.results;
		// the previous geology of this project (and source) is replaced
		const old = [...this.session.entities.values()].filter((e) => e.metadata && e.metadata.geology && e.metadata.geology.geo_project === project && e.metadata.geology.source === r.source).map((e) => e.entity_id);
		const entities = this.geology_entities(r, form);
		const group = [...new Set([...(edit ? edit.group : []), ...old])];
		this.replace(group.length ? { group } : null, __("Geology {0}", [project]), entities);
		this.ctx.zoom(entities.filter((e) => e.entity_type === "Surface").map((e) => e.entity_id)); // often a new or far-away area
		this.ctx.show_result({ kind: "geology_report", title: __("Geology {0}", [r.geology]), result: r });
		this.ctx.message(done.status_message, "result");
	}

	geology_entities(r, params) {
		const colours = ["#2b2b2b", "#5b4a3a", "#3d5a80", "#6d597a", "#355c3a", "#7a5c2e"];
		const seams = [...new Set(r.surfaces.filter((s) => s.seam).map((s) => s.seam))];
		const colour = (seam) => colours[Math.max(0, seams.indexOf(seam)) % colours.length];
		const label = (s) => r.seams.find((x) => x.seam === s)?.seam_label || s;
		const meta = (more) => ({ geology: { model: r.geology, geo_project: r.geo_project, source: r.source, ...more } });
		const entities = [];
		let with_params = false;
		for (const s of r.surfaces) {
			const layer = s.role === "topography" ? this.ensure_layer("GEOLOGY TOPOGRAPHY", "#8fae6b") : this.ensure_layer(`GEOLOGY ${String(label(s.seam)).toUpperCase()}`, colour(s.seam));
			const name = s.role === "topography" ? s.label : `${label(s.seam)} ${s.role === "roof" ? __("roof") : __("floor")}`;
			const extra = { seam: s.seam, role: s.role, variable: s.variable };
			if (!with_params) {
				extra.params = params;
				with_params = true;
			}
			entities.push(this.surface_entity(name, s.role === "topography" ? "Terrain Surface" : "Coal Seam", layer, { geometry_ref: s.geometry_ref, stats: s.stats, source: s.source }, meta(extra)));
		}
		if (r.holes.length) {
			const holes = this.ensure_layer("GEOLOGY DRILLHOLES", "#d0d0d0");
			for (const h of r.holes) {
				entities.push(this.polyline([h.collar, h.end], false, holes, meta({ hole_id: h.hole_id, role: "drillhole" })));
				for (const [seam, [roof, floor]] of Object.entries(h.seams)) {
					entities.push(this.polyline([roof, floor], false, this.ensure_layer(`GEOLOGY ${String(label(seam)).toUpperCase()}`, colour(seam)), meta({ hole_id: h.hole_id, seam, role: "intercept" }), "#ffcc00"));
				}
			}
		}
		return entities;
	}

	// Seam volume, tonnes and qualities of mining blocks from a geology model; material from the coal share.
	async block_geology() {
		const models = (await call("list_geology_models", { model: this.ctx.model().name })).filter((g) => g.status === "Current");
		if (!models.length) throw new Error(__("Build the geology first (Mining → Geology from Geo Planning)."));
		const selected = this.blocks_selected();
		const all = this.blocks_selected(true);
		if (!all.length) throw new Error(__("The model has no mining blocks."));
		const blocks = selected.length ? selected : all;
		this.sc.require_saved(blocks);
		this.materials = await call("list_material_types").catch(() => []);
		const values = await this.ctx.ask({
			title: __("Block Geology"),
			intro: __("{0} block(s){1}: the seams in each block's own bench, exactly (seam volume, tonnes from the density grid, tonnage-weighted qualities); the rest of the block is waste.", [blocks.length, selected.length ? "" : " " + __("(all)")]),
			fields: [
				{ key: "geology", label: __("Geology"), type: "select", options: models.map((g) => ({ value: g.name, label: `${g.name} · ${g.title} · ${g.seams.map((x) => x.seam).join(", ")}` })), default: models[0].name },
				{ key: "min_coal", label: __("Coal block from (% of the block)"), type: "number", default: 50 },
				{
					key: "material_by",
					label: __("Coal blocks are"),
					type: "select",
					options: [
						{ value: "coal", label: __("One coal material") },
						{ value: "seam", label: __("Named after their main seam") },
					],
					default: "coal",
				},
				{ key: "coal_material", label: __("Coal material"), default: this.materials.find((m) => /coal/i.test(m)) || "Coal", show_if: (v) => v.material_by === "coal" },
				{ key: "waste_material", label: __("Waste material"), default: this.materials.find((m) => /overburden|waste/i.test(m)) || "Overburden" },
				{ key: "default_density", label: __("Density where there is no density grid"), type: "number", default: 1.5 },
			],
			submit_label: __("Calculate"),
		});
		if (!values) return;
		const params = { ...values, blocks: selected.length ? selected.map((b) => b.entity_id) : null };
		const done = await this.ctx.run_job("mining_job", { model: this.ctx.model().name, operation: "block_geology", params });
		if (!done) return;
		const r = done.results;
		this.session.apply(__("Block Geology"), (w) => {
			for (const [id, u] of Object.entries(r.updates)) {
				const b = this.session.entities.get(id);
				if (!b) continue;
				w.put({ ...b, color: material_colour(u.material, this.materials), metadata: { ...b.metadata, mining_block: { ...b.metadata.mining_block, material: u.material, geology: u.geology } } });
			}
		});
		const rows = Object.entries(r.updates).map(([id, u]) => ({ id, ...this.session.entities.get(id).metadata.mining_block }));
		this.ctx.show_result({ kind: "block_geology_report", title: __("Block Geology"), result: r, rows });
		this.ctx.message(done.status_message, "result");
	}

	// -------------------------------------------------------------- pit-shell optimisation (Phase 6)

	// Nested pit shells (maximum closure per revenue factor) on a block model valued from a geology model.
	async pit_shells(edit = null) {
		const models = (await call("list_geology_models", { model: this.ctx.model().name })).filter((g) => g.status === "Current");
		if (!models.length) throw new Error(__("Build the geology first (Mining → Geology from Geo Planning)."));
		const std = (await this.standard()) || {};
		const limits = this.sc.selected((e) => closed_loop(e) || (e.entity_type === "Polyline" && e.geometry.closed));
		const values = await this.ask(edit, {
			title: __("Pit Optimisation (nested shells)"),
			intro: __("A block model over the seams, each block valued from the geology; the best pit for every revenue factor (Lerchs–Grossmann, as a minimum cut) within the overall slope. Undiscounted.") + (limits.length === 1 && !edit ? " " + __("The selected outline is the mining limit.") : ""),
			fields: [
				{ key: "geology", label: __("Geology"), type: "select", options: models.map((g) => ({ value: g.name, label: `${g.name} · ${g.title}` })), default: models[0].name },
				{ key: "ground", label: __("Ground surface"), type: "select", options: this.surface_choices(__("The geology's topography")), default: "" },
				{ key: "cell_size", label: __("Block size (m)"), type: "number", default: 20 },
				{ key: "bench_height", label: __("Bench height (m)"), type: "number", default: std.bench_height || 10 },
				{ key: "slope", label: __("Overall slope (°)"), type: "number", default: std.maximum_slope_angle || 45 },
				{ key: "bottom_elevation", label: __("Bottom elevation"), type: "number", help: __("Empty: below the lowest seam floor.") },
				{ key: "price", label: __("Coal price (per t)"), type: "number", default: 1200 },
				{ key: "recovery", label: __("Mining recovery (%)"), type: "number", default: 95 },
				{ key: "mining_cost", label: __("Mining cost (per bcm, all material)"), type: "number", default: 45 },
				{ key: "processing_cost", label: __("Processing and haulage (per t coal)"), type: "number", default: 250 },
				{ key: "default_density", label: __("Density without a density grid"), type: "number", default: 1.5 },
				{ key: "rf_from", label: __("Revenue factors from"), type: "number", default: 0.5 },
				{ key: "rf_to", label: __("…to"), type: "number", default: 1.5 },
				{ key: "rf_step", label: __("…step"), type: "number", default: 0.1 },
				{ key: "selected_rf", label: __("Shell to draw (revenue factor)"), type: "number", default: 1 },
			],
			submit_label: __("Optimise"),
			check: (v) => (v.price > 0 && v.cell_size > 0 && v.slope > 0 && v.slope < 90 ? null : __("Give a price, a block size and a slope between 0° and 90°.")),
		});
		if (!values) return;
		this.saved_surface(values.ground);
		const params = { ...values, ground: values.ground || null, limit: edit ? edit.params.limit : limits.length === 1 ? limits[0].entity_id : null };
		if (params.limit && !edit) this.sc.require_saved(limits);
		for (const k of ["bottom_elevation"]) if (values[k] === "" || values[k] === null) delete params[k];
		const done = await this.ctx.run_job("mining_job", { model: this.ctx.model().name, operation: "pit_shells", params });
		if (!done) return;
		const r = done.results;
		const layer = this.ensure_layer("PIT SHELL", "#e07a5f");
		const meta = (more) => ({ pit_shell: { optimisation: r.optimisation, revenue_factor: r.selected_rf, ...more } });
		const entities = [];
		if (r.shell) entities.push(this.surface_entity(__("Pit Shell RF {0}", [r.selected_rf]), "Design Surface", layer, r.shell, meta({ role: "surface", params })));
		for (const ring of r.outline) entities.push(this.polyline(ring, true, layer, meta({ role: "limit", ...(r.shell ? {} : { params }) }), "#e07a5f"));
		const old = [...this.session.entities.values()].filter((e) => e.metadata && e.metadata.pit_shell).map((e) => e.entity_id);
		const group = [...new Set([...(edit ? edit.group : []), ...old])];
		if (entities.length) this.replace(group.length ? { group } : null, __("Pit Shell"), entities);
		this.ctx.show_result({ kind: "optimisation_report", title: __("Pit Optimisation {0}", [r.optimisation]), result: r });
		this.ctx.message(done.status_message, r.shell ? "result" : "warning");
	}

	// -------------------------------------------------------------- mining blocks

	async blocks(edit = null) {
		const limits = edit ? this.source(edit.params.boundary) : this.sc.selected((e) => closed_loop(e) || (e.entity_type === "Polyline" && e.geometry.closed));
		if (limits.length !== 1) throw new Error(__("Select the block limit (a closed polyline, e.g. the pit's top crest or daylight line)."));
		this.sc.require_saved(limits);
		const surfaces = this.sc.surfaces();
		if (surfaces.length < 2) throw new Error(__("Blocks need two surfaces: the ground (upper) and the pit or floor (lower)."));
		const std = (await this.standard()) || {};
		const upper = surfaces.find((e) => /survey|terrain|ground/i.test(`${e.geometry.surface_type} ${e.geometry.name}`)) || surfaces[0];
		const lower = surfaces.find((e) => /pit/i.test(`${e.geometry.surface_type} ${e.geometry.name}`) && e !== upper) || surfaces.find((e) => e !== upper);
		const values = await this.ask(edit, {
			title: __("Mining Blocks"),
			intro: __("Blocks on a grid along the mining direction, one per bench, inside the limit; each holds the material between the upper and the lower surface in its bench (exact)."),
			fields: [
				{ key: "upper", label: __("Upper surface (top of material)"), type: "select", options: this.surface_choices("").slice(1), default: upper.geometry_ref },
				{ key: "lower", label: __("Lower surface (bottom of mining)"), type: "select", options: this.surface_choices("").slice(1), default: lower.geometry_ref },
				{ key: "bench_height", label: __("Bench height (m)"), type: "number", default: std.bench_height || 10 },
				{ key: "bench_reference_elevation", label: __("Bench reference elevation"), type: "number", default: std.bench_reference_elevation || 0 },
				{ key: "block_length", label: __("Block length, along the direction (m)"), type: "number", default: std.block_length || 50 },
				{ key: "block_width", label: __("Block width (m)"), type: "number", default: std.block_width || 25 },
				{ key: "direction", label: __("Mining direction (° from north)"), type: "number", default: std.mining_direction ?? 90 },
				{ key: "prefix", label: __("Block ID prefix"), default: "B" },
				{ key: "min_volume", label: __("Leave out blocks under (m³)"), type: "number", default: 1 },
			],
			submit_label: __("Make Blocks"),
			check: (v) => (v.upper === v.lower ? __("Choose two different surfaces.") : null),
		});
		if (!values) return;
		this.saved_surface(values.upper);
		this.saved_surface(values.lower);
		const params = { boundary: limits[0].entity_id, ...values };
		const done = await this.ctx.run_job("mining_job", { model: this.ctx.model().name, operation: "blocks", params });
		if (!done) return;
		const r = done.results;
		// regenerated blocks keep the materials of blocks with the same id
		const before = new Map((edit ? edit.group : []).map((id) => this.session.entities.get(id)).filter((e) => e && e.metadata && e.metadata.mining_block).map((e) => [e.metadata.mining_block.block_id, e.metadata.mining_block]));
		const run = edit ? edit.run : `R${Date.now().toString(36).toUpperCase()}`;
		const entities = r.blocks.map((b) => this.block_entity(b, { ...r.common, run, params }, before.get(b.block_id) || null));
		this.replace(edit, __("Mining Blocks"), entities);
		this.ctx.message(done.status_message, "result");
		this.report();
	}

	block_entity(b, common, previous = null) {
		const layer = this.ensure_layer(`BLOCKS ${Math.round(b.bench_bottom)}`, "#7fa7c9");
		const meta = { ...common, ...b };
		delete meta.polygon;
		delete meta.along;
		delete meta.across;
		if (previous) meta.material = previous.material || meta.material || "";
		return this.polyline(b.polygon, true, layer, { mining_block: meta }, material_colour(meta.material, this.materials));
	}

	blocks_selected(all_if_none = false) {
		let blocks = this.sc.selected((e) => e.metadata && e.metadata.mining_block);
		if (!blocks.length && all_if_none) blocks = [...this.session.entities.values()].filter((e) => e.metadata && e.metadata.mining_block);
		return blocks;
	}

	async subdivide() {
		const blocks = this.blocks_selected();
		if (!blocks.length) throw new Error(__("Select the mining blocks to split."));
		this.sc.require_saved(blocks);
		const values = await this.ctx.ask({
			title: __("Subdivide Blocks"),
			intro: __("{0} block(s); the parts' volumes are recalculated.", [blocks.length]),
			fields: [
				{ key: "along", label: __("Parts along the mining direction"), type: "number", default: 2 },
				{ key: "across", label: __("Parts across"), type: "number", default: 1 },
			],
			submit_label: __("Split"),
			check: (v) => (v.along >= 1 && v.across >= 1 && v.along * v.across > 1 ? null : __("Split into at least two parts.")),
		});
		if (!values) return;
		const done = await this.ctx.run_job("mining_job", { model: this.ctx.model().name, operation: "subdivide", params: { blocks: blocks.map((b) => b.entity_id), ...values } });
		if (!done) return;
		const added = [];
		const removed = new Set(Object.keys(done.results.replacements));
		for (const [old, parts] of Object.entries(done.results.replacements)) {
			const previous = this.session.entities.get(old);
			const prev_meta = (previous && previous.metadata && previous.metadata.mining_block) || {};
			for (const p of parts) {
				const e = this.block_entity(p, {}, prev_meta);
				if (previous) e.cad_layer = previous.cad_layer;
				added.push(e);
			}
		}
		// the parts take their place in the sequence (one undoable step with the split)
		const others = [...this.session.entities.values()].filter((e) => e.metadata && e.metadata.mining_block && !removed.has(e.entity_id));
		const renumbered = this.sequenced([...others, ...added], null, 1);
		this.session.apply(__("Subdivide Blocks"), (w) => {
			removed.forEach((id) => w.remove(id));
			renumbered.forEach((e) => w.put(e));
		});
		this.ctx.select(added.map((e) => e.entity_id));
		this.ctx.message(done.status_message, "result");
	}

	async material() {
		const blocks = this.blocks_selected();
		if (!blocks.length) throw new Error(__("Select the mining blocks to classify."));
		this.materials = await call("list_material_types").catch(() => []);
		const values = await this.ctx.ask({
			title: __("Set Block Material"),
			intro: __("{0} block(s).", [blocks.length]),
			fields: [
				...(this.materials.length ? [{ key: "material", label: __("Material type"), type: "select", options: ["", ...this.materials], default: this.materials[0] }] : []),
				{ key: "other", label: this.materials.length ? __("…or another material") : __("Material"), help: this.materials.length ? __("Material Types come from Geo Planning.") : "" },
				{ key: "min", label: __("Only benches from (bottom ≥)"), type: "number" },
				{ key: "max", label: __("…to (bottom ≤)"), type: "number" },
			],
			submit_label: __("Set"),
			check: (v) => (v.material || v.other ? null : __("Choose or type a material.")),
		});
		if (!values) return;
		const material = (values.other || values.material || "").trim();
		const lo = values.min === "" || values.min === null ? -Infinity : values.min;
		const hi = values.max === "" || values.max === null ? Infinity : values.max;
		const chosen = blocks.filter((b) => b.metadata.mining_block.bench_bottom >= lo && b.metadata.mining_block.bench_bottom <= hi);
		this.session.apply(__("Block Material"), (w) => {
			for (const b of chosen) w.put({ ...b, color: material_colour(material, this.materials), metadata: { ...b.metadata, mining_block: { ...b.metadata.mining_block, material } } });
		});
		this.ctx.message(__("{0} block(s) set to {1}.", [chosen.length, material]), "result");
	}

	async sequence() {
		const blocks = this.blocks_selected(true);
		if (!blocks.length) throw new Error(__("The model has no mining blocks."));
		const std = (await this.standard()) || {};
		const values = await this.ctx.ask({
			title: __("Sequence Blocks"),
			intro: __("{0} block(s): numbered bench by bench from the top, along the mining direction within each bench.", [blocks.length]),
			fields: [
				{ key: "direction", label: __("Mining direction (° from north)"), type: "number", default: blocks[0].metadata.mining_block.direction ?? std.mining_direction ?? 90 },
				{ key: "start", label: __("First number"), type: "number", default: 1 },
			],
			submit_label: __("Sequence"),
		});
		if (!values) return;
		const renumbered = this.sequenced(blocks, values.direction, values.start || 1);
		this.session.apply(__("Sequence Blocks"), (w) => renumbered.forEach((e) => w.put(e)));
		this.ctx.message(__("{0} blocks sequenced.", [renumbered.length]), "result");
	}

	// Blocks numbered from the top bench down, along the mining direction within a bench (then across it).
	// direction: degrees from north (null: each block's own); westing/southing models have the plan turned.
	sequenced(blocks, direction, start = 1) {
		const flip = this.ctx.viewport.plan_rotated ? -1 : 1;
		const key = (e) => {
			const m = e.metadata.mining_block;
			const a = ((direction ?? m.direction ?? 0) * Math.PI) / 180;
			const ux = flip * Math.sin(a), uy = flip * Math.cos(a);
			const pts = e.geometry.points;
			const cx = pts.reduce((sum, p) => sum + p[0], 0) / pts.length, cy = pts.reduce((sum, p) => sum + p[1], 0) / pts.length;
			return [-m.bench_top, cx * ux + cy * uy, -cx * uy + cy * ux];
		};
		return blocks
			.map((e) => [key(e), e])
			.sort((a, b) => a[0][0] - b[0][0] || a[0][1] - b[0][1] || a[0][2] - b[0][2])
			.map(([, e], i) => {
				const m = { ...e.metadata.mining_block, sequence: start + i };
				if (direction !== null && direction !== undefined) m.direction = direction;
				return { ...e, metadata: { ...e.metadata, mining_block: m } };
			});
	}

	report() {
		const rows = [...this.session.entities.values()].filter((e) => e.metadata && e.metadata.mining_block).map((e) => ({ entity_id: e.entity_id, ...e.metadata.mining_block }));
		if (!rows.length) throw new Error(__("The model has no mining blocks."));
		rows.sort((a, b) => (a.sequence || 0) - (b.sequence || 0));
		this.ctx.show_result({ kind: "blocks_report", title: __("Mining Blocks"), rows, model: this.ctx.model().name });
	}

	// -------------------------------------------------------------- drill and blast (layout geometry only)

	async drill(edit = null) {
		const limits = edit ? this.source(edit.params.boundary) : this.sc.selected((e) => closed_loop(e) || (e.entity_type === "Polyline" && e.geometry.closed));
		if (limits.length !== 1) throw new Error(__("Select the blast boundary (one closed polyline on the bench)."));
		this.sc.require_saved(limits);
		const std = (await this.standard()) || {};
		const patterns = new Set([...this.session.entities.values()].map((e) => e.metadata && e.metadata.drill_hole && e.metadata.drill_hole.pattern).filter(Boolean));
		let n = patterns.size + 1;
		while (patterns.has(__("Pattern {0}", [n]))) n++;
		const z = limits[0].geometry.points ? limits[0].geometry.points[0][2] : 0;
		const collar = this.sc.surfaces().find((e) => /post|pit|survey|ground/i.test(`${e.geometry.surface_type} ${e.geometry.name}`));
		const values = await this.ask(edit, {
			title: __("Drill Pattern"),
			intro: __("Hole layout only (positions, angles and lengths): no explosive loading. Rows run along the row direction; the burden is measured to the right of it."),
			fields: [
				{ key: "name", label: __("Pattern name"), default: __("Pattern {0}", [n]), required: true },
				{ key: "prefix", label: __("Hole ID prefix"), default: `P${n}-` },
				{ key: "collar_surface", label: __("Collars on"), type: "select", options: this.surface_choices(__("An elevation")), default: collar ? collar.geometry_ref : "" },
				{ key: "collar_elevation", label: __("Collar elevation"), type: "number", default: z, show_if: (v) => !v.collar_surface },
				{ key: "floor_surface", label: __("Bench floor"), type: "select", options: this.surface_choices(__("An elevation")), default: "" },
				{ key: "floor_elevation", label: __("Bench floor elevation"), type: "number", default: std.bench_height ? z - std.bench_height : "", show_if: (v) => !v.floor_surface },
				{ key: "burden", label: __("Burden (m)"), type: "number", default: std.drill_burden || 4 },
				{ key: "spacing", label: __("Spacing (m)"), type: "number", default: std.drill_spacing || 5 },
				{ key: "pattern_type", label: __("Pattern"), type: "select", options: ["Staggered", "Square"], default: std.drill_pattern || "Staggered" },
				{ key: "standoff", label: __("Stand-off from the boundary (m)"), type: "number", default: std.drill_crest_standoff ?? 3 },
				{ key: "row_direction", label: __("Row direction (° from north)"), type: "number", default: 90 },
				{ key: "inclination", label: __("Inclination (° from vertical)"), type: "number", default: 0 },
				{ key: "dip_direction", label: __("Dip direction (°)"), type: "number", help: __("Empty: towards the burden (row direction + 90°).") },
				{ key: "subdrill", label: __("Subdrill (m)"), type: "number", default: std.drill_subdrill ?? 1 },
				{ key: "diameter", label: __("Hole diameter (mm)"), type: "number", default: std.drill_hole_diameter || 165 },
			],
			submit_label: __("Lay Out Holes"),
			check: (v) => (v.burden > 0 && v.spacing > 0 ? (v.floor_surface || v.floor_elevation !== "" ? null : __("Give the bench floor.")) : __("Give the burden and the spacing.")),
		});
		if (!values) return;
		this.saved_surface(values.collar_surface);
		this.saved_surface(values.floor_surface);
		const params = { boundary: limits[0].entity_id, ...values, collar_surface: values.collar_surface || null, floor_surface: values.floor_surface || null };
		if (params.dip_direction === "") delete params.dip_direction;
		const done = await this.ctx.run_job("mining_job", { model: this.ctx.model().name, operation: "drill", params });
		if (!done) return;
		const r = done.results;
		const layer = this.ensure_layer(`DRILL ${r.design.pattern.toUpperCase()}`, "#e8e05a");
		const entities = [];
		for (const h of r.holes) {
			const data = { ...r.design, hole_id: h.hole_id, row: h.row, number: h.number, length: h.length, floor: h.floor };
			entities.push({ entity_id: new_entity_id(), entity_type: "Line", cad_layer: layer, validation_status: "Not Checked", geometry: { p0: h.collar, p1: h.toe }, metadata: { drill_hole: { ...data, role: "hole", params } } });
			entities.push({ entity_id: new_entity_id(), entity_type: "Point", cad_layer: layer, validation_status: "Not Checked", geometry: { p: h.collar }, metadata: { drill_hole: { ...data, role: "collar" } } });
		}
		this.replace(edit, r.design.pattern, entities);
		this.ctx.show_result({ kind: "drill_report", title: r.design.pattern, result: r });
		this.ctx.message(done.status_message, "result");
		for (const p of r.problems.slice(0, 10)) this.ctx.message(p, "warning");
	}

	drill_report() {
		const holes = [...this.session.entities.values()].filter((e) => e.entity_type === "Line" && e.metadata && e.metadata.drill_hole && e.metadata.drill_hole.role === "hole");
		if (!holes.length) throw new Error(__("The model has no drill holes."));
		const patterns = new Map();
		for (const e of holes) {
			const d = e.metadata.drill_hole;
			const a = e.geometry.p0, b = e.geometry.p1;
			const row = { hole_id: d.hole_id, row: d.row, number: d.number, collar: a, toe: b, length: Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]), entity_id: e.entity_id };
			if (!patterns.has(d.pattern)) patterns.set(d.pattern, { design: d, holes: [] });
			patterns.get(d.pattern).holes.push(row);
		}
		const [name, p] = [...patterns.entries()].pop();
		p.holes.sort((x, y) => x.row - y.row || x.number - y.number);
		const lengths = p.holes.map((h) => h.length);
		const summary = { holes: p.holes.length, rows: new Set(p.holes.map((h) => h.row)).size, drilled_length: lengths.reduce((s, v) => s + v, 0), mean_length: lengths.reduce((s, v) => s + v, 0) / lengths.length, min_length: Math.min(...lengths), max_length: Math.max(...lengths) };
		this.ctx.show_result({ kind: "drill_report", title: name + (patterns.size > 1 ? ` (${__("{0} patterns: the last one", [patterns.size])})` : ""), result: { design: p.design, holes: p.holes, summary, problems: [] } });
	}

	// -------------------------------------------------------------- validation

	async validate() {
		const dirty = [...this.session.dirty].map((id) => this.session.entities.get(id)).filter((e) => e && e.metadata && (e.metadata.pit_design || e.metadata.haul_road || e.metadata.mining_block));
		if (dirty.length) throw new Error(__("Save first (Ctrl+S): the validation checks the saved design."));
		const r = await call("validate_design", { model: this.ctx.model().name });
		this.ctx.model().last_design_validation = r.name;
		this.ctx.show_result({ kind: "design_validation", title: __("Design Validation {0}", [r.name]), result: r });
		this.ctx.message(r.summary, r.status === "Fail" ? "error" : r.status === "Warning" ? "warning" : "result");
	}
}
