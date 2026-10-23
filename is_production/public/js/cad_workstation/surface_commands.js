// Phase 3 commands: surfaces (build, repair, clip, smooth, checks), analysis (contours, profiles, sections,
// volumes, intersections, slope/elevation) and solid operations (Booleans, revolve, loft, sweep, sections).
// The work is done on the server (CAD worker jobs); this module collects the inputs, starts the job and turns
// the result into entities (one undoable step) or a report in the Analysis panel.

import { call } from "./api";
import { closed_loop, is_closed_profile, new_entity_id } from "./geometry";

const LINEAR = new Set(["Line", "Polyline", "Arc", "Spline", "Ellipse", "Circle"]);

// Short text for a TIN validation report's problems.
export function tin_problems(v) {
	if (!v) return "";
	if (v.message) return v.message;
	const parts = [
		[v.degenerate_triangles, __("degenerate")],
		[v.flipped_triangles, __("flipped")],
		[v.duplicate_triangles, __("duplicate triangles")],
		[v.non_manifold_edges, __("non-manifold edges")],
		[v.duplicate_plan_points, __("duplicate plan points")],
	]
		.filter(([n]) => n)
		.map(([n, label]) => `${n} ${label}`);
	return parts.join(" · ") || __("OK");
}

export function entity_label(e) {
	const name = e.geometry && e.geometry.name;
	return name ? `${name} (${__(e.entity_type)})` : `${__(e.entity_type)} ${e.entity_id}`;
}

export class SurfaceCommands {
	// ctx: session, viewport, config, model(), selection(), select(ids), message(text, level), ask(form),
	//      run_job(method, args), build_solids(items), current_layer(), show_result(result)
	constructor(ctx) {
		this.ctx = ctx;
		this.section = null;
	}

	get session() {
		return this.ctx.session;
	}

	selected(filter = () => true) {
		return this.ctx.selection().map((id) => this.session.entities.get(id)).filter((e) => e && filter(e));
	}

	surfaces(types = ["Surface"]) {
		return [...this.session.entities.values()].filter((e) => types.includes(e.entity_type) && e.geometry_ref);
	}

	// Server jobs read entities from the database: unsaved ones are not there yet.
	require_saved(entities) {
		const unsaved = entities.filter((e) => this.session.dirty.has(e.entity_id) || !this.session.server_ids.has(e.entity_id));
		if (unsaved.length) throw new Error(__("Save first (Ctrl+S): {0} is not saved yet.", [entity_label(unsaved[0])]));
	}

	surface_options(types = ["Surface"]) {
		return this.surfaces(types).map((e) => ({ value: e.entity_id, label: entity_label(e) }));
	}

	first_selected_surface() {
		const s = this.selected((e) => e.entity_type === "Surface");
		return s.length ? s[0].entity_id : (this.surfaces()[0] || {}).entity_id;
	}

	loop_options(entities) {
		return [{ value: "", label: __("None") }, ...entities.filter((e) => closed_loop(e) || (e.entity_type === "Polyline" && e.geometry.closed)).map((e) => ({ value: e.entity_id, label: entity_label(e) }))];
	}

	async run(command, args) {
		const handler = {
			surface_create: () => this.create(),
			surface_repair: () => this.change("repair"),
			surface_clip: () => this.change("clip"),
			surface_smooth: () => this.change("smooth"),
			surface_checks: () => this.checks(),
			contours: () => this.contours(),
			profile: () => this.profile(),
			sections: () => this.sections(),
			volume: () => this.volume(),
			volume_history: () => this.volume_history(),
			intersect: () => this.intersect(),
			analyse: () => this.analyse(),
			surface_colour: () => this.ctx.viewport.surfaces.set_mode(args),
			surface_wireframe: () => this.ctx.viewport.surfaces.set_wireframe(!this.ctx.viewport.surfaces.wireframe),
			solid_boolean: () => this.boolean(args),
			solid_revolve: () => this.revolve(),
			solid_loft: () => this.loft(),
			solid_sweep: () => this.sweep(),
			section_cut: () => this.section_cut(),
			section_plane: () => this.section_plane(),
			section_plane_off: () => this.set_section(null),
		}[command];
		if (!handler) return false;
		await handler();
		return true;
	}

	// ------------------------------------------------------------------ surfaces

	async create() {
		const chosen = this.selected();
		const datasets = await call("list_gis_datasets").catch(() => []);
		if (!chosen.length && !datasets.length) throw new Error(__("Select the points, lines and surfaces to build from (or add GIS datasets with elevations)."));
		this.require_saved(chosen);
		const n = this.surfaces().length + 1;
		const values = await this.ctx.ask({
			title: __("Create Surface"),
			intro: __("{0} selected object(s). Points and surfaces give elevations; lines and polylines can be breaklines (triangle edges follow them).", [chosen.length]),
			fields: [
				{ key: "name", label: __("Name"), default: __("Surface {0}", [n]), required: true },
				{ key: "surface_type", label: __("Surface type"), type: "select", options: this.ctx.config.surface_types.filter(Boolean), default: "Design Surface" },
				{
					key: "lines_as",
					label: __("Lines and polylines"),
					type: "select",
					options: [
						{ value: "breaklines", label: __("Breaklines") },
						{ value: "points", label: __("Points only") },
					],
					default: "breaklines",
				},
				{ key: "boundary", label: __("Outer boundary"), type: "select", options: this.loop_options(chosen), default: "" },
				{ key: "holes", label: __("Holes"), type: "multiselect", options: this.loop_options(chosen).slice(1) },
				{ key: "gis_datasets", label: __("GIS datasets"), type: "multiselect", options: datasets.map((d) => ({ value: d.name, label: `${d.title || d.dataset_name || d.name} (${d.feature_count || 0})` })) },
				{ key: "max_edge", label: __("Longest triangle edge"), type: "number", help: __("Leave empty for no limit; long edges across gaps are removed.") },
			],
			submit_label: __("Create"),
		});
		if (!values) return;
		const skip = new Set([values.boundary, ...values.holes].filter(Boolean));
		const points = [], breaklines = [];
		for (const e of chosen) {
			if (skip.has(e.entity_id)) continue;
			if (values.lines_as === "breaklines" && LINEAR.has(e.entity_type) && e.entity_type !== "Circle") breaklines.push(e.entity_id);
			else points.push(e.entity_id);
		}
		const params = { points, breaklines, boundary: values.boundary || null, holes: values.holes, gis_datasets: values.gis_datasets, max_edge: values.max_edge || null };
		const done = await this.ctx.run_job("surface_job", { model: this.ctx.model().name, operation: "build", params });
		if (!done) return;
		const r = done.results;
		const entity = {
			entity_id: new_entity_id(),
			entity_type: "Surface",
			cad_layer: this.ctx.current_layer(),
			geometry: { name: values.name, surface_type: values.surface_type, source: r.source },
			geometry_ref: r.geometry_ref,
			bbox: r.bbox,
			area: r.stats.surface_area,
			validation_status: r.stats.validation.ok ? "Valid" : "Warning",
			_stats: r.stats,
		};
		this.session.apply(__("Create Surface"), (w) => w.put(entity));
		this.ctx.select([entity.entity_id]);
		for (const w of r.warnings || []) this.ctx.message(w, "warning");
		this.report_stats(values.name, r.stats);
	}

	report_stats(name, stats) {
		const v = stats.validation || {};
		this.ctx.message(
			__("{0}: {1} triangles, {2} vertices, plan area {3}, elevations {4} … {5}. Checks: {6}.", [
				name,
				stats.triangle_count,
				stats.vertex_count,
				stats.plan_area.toFixed(2),
				stats.min_elevation.toFixed(2),
				stats.max_elevation.toFixed(2),
				v.ok ? (v.spike_count ? __("OK, {0} possible spike(s)", [v.spike_count]) : __("OK")) : tin_problems(v),
			]),
			v.ok ? "result" : "warning"
		);
	}

	async change(operation) {
		const options = this.surface_options(["Surface"]);
		if (!options.length) throw new Error(__("The model has no surfaces."));
		const fields = [{ key: "surface", label: __("Surface"), type: "select", options, default: this.first_selected_surface() }];
		const titles = { repair: __("Repair Surface"), clip: __("Clip Surface"), smooth: __("Smooth Surface") };
		if (operation === "repair") {
			fields.push({ key: "remove_spikes", label: __("Spikes"), type: "check", check_label: __("Remove spikes as well"), default: false });
			fields.push({ key: "spike_factor", label: __("Spike factor"), type: "number", default: 8, help: __("A vertex is a spike when it departs from its neighbours by more than this × the typical departure.") });
		} else if (operation === "clip") {
			const loops = this.loop_options([...this.session.entities.values()]).slice(1);
			if (!loops.length) throw new Error(__("Draw a closed polyline to clip with."));
			const picked = this.selected((e) => closed_loop(e) || (e.entity_type === "Polyline" && e.geometry.closed));
			fields.push({ key: "boundary", label: __("Boundary"), type: "select", options: loops, default: (picked[0] || {}).entity_id || loops[0].value });
			fields.push({
				key: "keep",
				label: __("Keep"),
				type: "select",
				options: [
					{ value: "inside", label: __("Inside the boundary") },
					{ value: "outside", label: __("Outside the boundary") },
				],
				default: "inside",
			});
		} else {
			fields.push({ key: "iterations", label: __("Iterations"), type: "number", default: 3 });
			fields.push({ key: "weight", label: __("Weight (0–1)"), type: "number", default: 0.5, help: __("Breakline and boundary vertices do not move.") });
		}
		fields.push({ key: "as_new", label: __("Result"), type: "check", check_label: __("Keep the original; add the result as a new surface"), default: false });
		const values = await this.ctx.ask({ title: titles[operation], fields, submit_label: __("Run") });
		if (!values) return;
		const surface = this.session.entities.get(values.surface);
		const params = { geometry_ref: surface.geometry_ref, ...values };
		if (operation === "clip") this.require_saved([this.session.entities.get(values.boundary)]);
		const done = await this.ctx.run_job("surface_job", { model: this.ctx.model().name, operation, params });
		if (!done) return;
		const r = done.results;
		const name = values.as_new ? `${surface.geometry.name} (${operation})` : surface.geometry.name;
		const next = {
			...surface,
			entity_id: values.as_new ? new_entity_id() : surface.entity_id,
			geometry: { ...surface.geometry, name, source: r.source },
			geometry_ref: r.geometry_ref,
			bbox: r.bbox,
			area: r.stats.surface_area,
			validation_status: r.stats.validation.ok ? "Valid" : "Warning",
			_stats: r.stats,
		};
		this.session.apply(titles[operation], (w) => w.put(next));
		this.ctx.select([next.entity_id]);
		this.report_stats(name, r.stats);
	}

	async checks() {
		const s = this.selected((e) => ["Surface", "Mesh"].includes(e.entity_type));
		if (!s.length) throw new Error(__("Select a surface."));
		const e = s[0];
		if (!e._stats) throw new Error(__("The surface is still loading."));
		this.ctx.show_result({ kind: "checks", title: __("Checks of {0}", [entity_label(e)]), entity: e, stats: e._stats });
	}

	// ------------------------------------------------------------------ analysis

	async analysis(analysis, params, entities) {
		this.require_saved(entities);
		const done = await this.ctx.run_job("analysis_job", { model: this.ctx.model().name, analysis, params });
		if (done) this.ctx.message(done.status_message, "result");
		return done ? done.results : null;
	}

	ensure_layer(name, color) {
		const s = this.session;
		const existing = s.layer_by_name(name) || s.layers.get(name);
		if (existing) return existing.name || name;
		s.set_layer(name, { layer_name: name, color, visible: 1, locked: 0, display_order: s.layers.size });
		return name;
	}

	add_polylines(label, lines, layer) {
		const entities = lines
			.filter((l) => l.points.length >= 2)
			.map((l) => ({
				entity_id: new_entity_id(),
				entity_type: "Polyline",
				cad_layer: l.layer || layer,
				validation_status: "Not Checked",
				geometry: { points: l.points.map((p) => [p[0], p[1], p[2] ?? 0]), closed: Boolean(l.closed) },
			}));
		if (entities.length) this.session.apply(label, (w) => entities.forEach((e) => w.put(e)));
		return entities.length;
	}

	async contours() {
		const options = this.surface_options();
		if (!options.length) throw new Error(__("The model has no surfaces."));
		const values = await this.ctx.ask({
			title: __("Contours"),
			fields: [
				{ key: "surface", label: __("Surface"), type: "select", options, default: this.first_selected_surface() },
				{ key: "interval", label: __("Interval"), type: "number", default: 1, required: true },
				{ key: "major", label: __("Major every"), type: "number", default: 5, help: __("Elevation step of the major contours (0 = none).") },
				{ key: "base", label: __("Base elevation"), type: "number", default: 0 },
			],
			submit_label: __("Make Contours"),
			check: (v) => (v.interval > 0 ? null : __("The interval must be greater than zero.")),
		});
		if (!values) return;
		const surface = this.session.entities.get(values.surface);
		const r = await this.analysis("contours", { geometry_refs: [surface.geometry_ref], interval: values.interval, major: values.major || 0, base: values.base || 0 }, [surface]);
		if (!r) return;
		const minor = this.ensure_layer("CONTOUR-MINOR", "#9a8f6a");
		const major = this.ensure_layer("CONTOUR-MAJOR", "#d9b44a");
		const count = this.add_polylines(__("Contours"), r.contours.map((c) => ({ ...c, layer: c.major ? major : minor })), minor);
		this.ctx.message(__("{0} contour lines added (layers CONTOUR-MINOR / CONTOUR-MAJOR).", [count]), "result");
	}

	alignment_options() {
		return [...this.session.entities.values()].filter((e) => ["Line", "Polyline"].includes(e.entity_type)).map((e) => ({ value: e.entity_id, label: entity_label(e) }));
	}

	selected_alignment() {
		const a = this.selected((e) => ["Line", "Polyline"].includes(e.entity_type));
		return a.length ? a[0].entity_id : null;
	}

	async ask_alignment(title, extra = []) {
		const surfaces = this.surface_options();
		if (!surfaces.length) throw new Error(__("The model has no surfaces."));
		const alignment = this.selected_alignment();
		if (!alignment) throw new Error(__("Select the alignment (a line or polyline) first."));
		const chosen = this.selected((e) => e.entity_type === "Surface").map((e) => e.entity_id);
		return this.ctx.ask({
			title,
			fields: [
				{ key: "surfaces", label: __("Surfaces"), type: "multiselect", options: surfaces, default: chosen.length ? chosen : surfaces.map((s) => s.value) },
				...extra,
			],
			submit_label: __("Run"),
			check: (v) => (v.surfaces.length ? null : __("Choose at least one surface.")),
		}).then((v) => v && { ...v, alignment });
	}

	async profile() {
		const values = await this.ask_alignment(__("Long Section (Profile)"));
		if (!values) return;
		const surfaces = values.surfaces.map((id) => this.session.entities.get(id));
		const alignment = this.session.entities.get(values.alignment);
		const r = await this.analysis("profile", { geometry_refs: surfaces.map((s) => s.geometry_ref), alignment: alignment.entity_id }, [...surfaces, alignment]);
		if (r) this.ctx.show_result({ kind: "profile", title: __("Profile along {0}", [entity_label(alignment)]), result: r });
	}

	async sections() {
		const values = await this.ask_alignment(__("Cross-Sections"), [
			{ key: "interval", label: __("Interval"), type: "number", default: 20, required: true },
			{ key: "left", label: __("Width left"), type: "number", default: 50 },
			{ key: "right", label: __("Width right"), type: "number", default: 50 },
			{ key: "start", label: __("First chainage"), type: "number", default: 0 },
		]);
		if (!values) return;
		const surfaces = values.surfaces.map((id) => this.session.entities.get(id));
		const alignment = this.session.entities.get(values.alignment);
		const params = { geometry_refs: surfaces.map((s) => s.geometry_ref), alignment: alignment.entity_id, interval: values.interval, left: values.left, right: values.right, start: values.start };
		const r = await this.analysis("sections", params, [...surfaces, alignment]);
		if (r) this.ctx.show_result({ kind: "sections", title: __("Sections along {0}", [entity_label(alignment)]), result: r });
	}

	async volume() {
		const surfaces = this.surface_options();
		if (!surfaces.length) throw new Error(__("The model has no surfaces."));
		const picked = this.selected((e) => e.entity_type === "Surface").map((e) => e.entity_id);
		const loops = this.loop_options([...this.session.entities.values()].filter((e) => e.entity_type !== "Surface"));
		const boundary = this.selected((e) => e.entity_type === "Polyline" && e.geometry.closed);
		const values = await this.ctx.ask({
			title: __("Cut and Fill Volume"),
			intro: __("Cut = the base surface above the comparison; fill = the base below it. Calculated exactly between the triangulated surfaces and recorded."),
			fields: [
				{ key: "base", label: __("Base surface (e.g. existing ground)"), type: "select", options: surfaces, default: picked[0] || surfaces[0].value },
				{
					key: "against",
					label: __("Compare with"),
					type: "select",
					options: [
						{ value: "surface", label: __("Another surface") },
						{ value: "elevation", label: __("An elevation") },
					],
					default: surfaces.length > 1 ? "surface" : "elevation",
				},
				{ key: "comparison", label: __("Comparison surface (e.g. design)"), type: "select", options: surfaces, default: picked[1] || (surfaces[1] || surfaces[0]).value, show_if: (v) => v.against === "surface" },
				{ key: "elevation", label: __("Elevation"), type: "number", default: 0, show_if: (v) => v.against === "elevation" },
				{ key: "boundary", label: __("Within boundary"), type: "select", options: loops, default: (boundary[0] || {}).entity_id || "" },
			],
			submit_label: __("Calculate"),
			check: (v) => (v.against === "surface" && v.base === v.comparison ? __("Choose two different surfaces.") : null),
		});
		if (!values) return;
		const base = this.session.entities.get(values.base);
		const used = [base];
		const params = { geometry_refs: [base.geometry_ref] };
		if (values.against === "surface") {
			const comp = this.session.entities.get(values.comparison);
			params.geometry_refs.push(comp.geometry_ref);
			used.push(comp);
		} else params.elevation = values.elevation;
		if (values.boundary) {
			params.boundary = values.boundary;
			used.push(this.session.entities.get(values.boundary));
		}
		const r = await this.analysis("volume", params, used);
		if (r) this.ctx.show_result({ kind: "volume", title: __("Volume {0}", [r.record || ""]), result: r, units: this.ctx.model().linear_units });
	}

	async volume_history() {
		const rows = await call("list_volume_calculations", { model: this.ctx.model().name });
		this.ctx.show_result({ kind: "volume_history", title: __("Recorded Volumes"), rows });
	}

	async intersect() {
		const surfaces = this.surface_options();
		if (surfaces.length < 2) throw new Error(__("Intersect needs two surfaces."));
		const picked = this.selected((e) => e.entity_type === "Surface").map((e) => e.entity_id);
		const values = await this.ctx.ask({
			title: __("Intersect Surfaces"),
			intro: __("Lines where the two surfaces have the same elevation (e.g. design daylight lines)."),
			fields: [
				{ key: "a", label: __("First surface"), type: "select", options: surfaces, default: picked[0] || surfaces[0].value },
				{ key: "b", label: __("Second surface"), type: "select", options: surfaces, default: picked[1] || surfaces[1].value },
			],
			check: (v) => (v.a === v.b ? __("Choose two different surfaces.") : null),
		});
		if (!values) return;
		const a = this.session.entities.get(values.a), b = this.session.entities.get(values.b);
		const r = await this.analysis("intersect", { geometry_refs: [a.geometry_ref, b.geometry_ref] }, [a, b]);
		if (!r) return;
		const count = this.add_polylines(__("Intersect Surfaces"), r.lines, this.ensure_layer("INTERSECTION", "#e5534b"));
		this.ctx.message(__("{0} intersection line(s) added on layer INTERSECTION.", [count]), "result");
	}

	async analyse() {
		const options = this.surface_options();
		if (!options.length) throw new Error(__("The model has no surfaces."));
		const values = await this.ctx.ask({
			title: __("Slope and Elevation Analysis"),
			fields: [
				{ key: "surface", label: __("Surface"), type: "select", options, default: this.first_selected_surface() },
				{ key: "elevation_band", label: __("Elevation band"), type: "number", help: __("Empty = a tenth of the range.") },
			],
		});
		if (!values) return;
		const surface = this.session.entities.get(values.surface);
		const r = await this.analysis("analyse", { geometry_refs: [surface.geometry_ref], elevation_band: values.elevation_band || null }, [surface]);
		if (r) this.ctx.show_result({ kind: "analyse", title: __("Analysis of {0}", [entity_label(surface)]), result: r });
	}

	// ------------------------------------------------------------------ solids

	solids_selected(min) {
		const solids = this.selected((e) => e.entity_type === "Solid" && e.geometry_ref);
		if (solids.length < min) throw new Error(__("Select at least {0} solid(s).", [min]));
		this.require_saved(solids);
		return solids;
	}

	// extra(r): more fields for the solid made from result r (e.g. its feature)
	add_solids(label, results, layer, remove = [], extra = null) {
		const ok = results.filter((r) => r.geometry_ref);
		for (const r of results.filter((r) => !r.geometry_ref)) this.ctx.message(`${label}: ${r.message || __("invalid geometry")}`, "error");
		if (!ok.length) return null;
		const entities = ok.map((r) => ({
			entity_id: new_entity_id(),
			entity_type: "Solid",
			cad_layer: layer,
			geometry: { source: r.source },
			geometry_ref: r.geometry_ref,
			bbox: r.bbox,
			volume: r.volume,
			area: r.area,
			validation_status: r.valid ? "Valid" : "Invalid",
			...(extra ? extra(r) : {}),
		}));
		this.session.apply(label, (w) => {
			entities.forEach((e) => w.put(e));
			remove.forEach((id) => w.remove(id));
		});
		this.ctx.select(entities.map((e) => e.entity_id));
		this.ctx.message(__("{0}: volume {1}.", [label, ok.map((r) => (r.volume || 0).toFixed(3)).join(", ")]), "result");
		return entities;
	}

	async boolean(kind) {
		const solids = this.solids_selected(2);
		const labels = { union: __("Union"), subtract: __("Subtract"), intersect: __("Intersect") };
		const values = await this.ctx.ask({
			title: labels[kind],
			intro: kind === "subtract" ? __("The other selected solids are cut away from the first one.") : __("{0} solids.", [solids.length]),
			fields: [
				...(kind === "subtract" ? [{ key: "first", label: __("Cut from"), type: "select", options: solids.map((s) => ({ value: s.entity_id, label: entity_label(s) })), default: solids[0].entity_id }] : []),
				{ key: "keep", label: __("Originals"), type: "check", check_label: __("Keep the original solids"), default: false },
			],
		});
		if (!values) return;
		const order = values.first ? [solids.find((s) => s.entity_id === values.first), ...solids.filter((s) => s.entity_id !== values.first)] : solids;
		const results = await this.ctx.build_solids([{ key: kind, op: "boolean", kind, geometry_refs: order.map((s) => s.geometry_ref) }]);
		if (results) this.add_solids(labels[kind], results, order[0].cad_layer, values.keep ? [] : order.map((s) => s.entity_id));
	}

	async revolve() {
		const profiles = this.selected((e) => is_closed_profile(e));
		const axes = this.selected((e) => e.entity_type === "Line");
		if (profiles.length !== 1 || axes.length !== 1) throw new Error(__("Select one closed polyline (the profile) and one line (the axis)."));
		const values = await this.ctx.ask({ title: __("Revolve"), fields: [{ key: "angle", label: __("Angle (°)"), type: "number", default: 360 }] });
		if (!values) return;
		const g = closed_loop(profiles[0]);
		const a = axes[0].geometry;
		const item = { key: "revolve", op: "revolve", profile: g.points, bulges: g.bulges, axis_point: a.p0, axis_direction: [a.p1[0] - a.p0[0], a.p1[1] - a.p0[1], a.p1[2] - a.p0[2]], angle: values.angle };
		const results = await this.ctx.build_solids([item]);
		if (results) this.add_solids(__("Revolve"), results, this.ctx.current_layer());
	}

	async loft() {
		const profiles = this.selected((e) => is_closed_profile(e)).sort((a, b) => a.geometry.points[0][2] - b.geometry.points[0][2]);
		if (profiles.length < 2) throw new Error(__("Select two or more closed polylines (they are lofted in order of elevation)."));
		const values = await this.ctx.ask({ title: __("Loft"), fields: [{ key: "ruled", label: __("Faces"), type: "check", check_label: __("Ruled (flat between profiles)"), default: true }] });
		if (!values) return;
		const results = await this.ctx.build_solids([{ key: "loft", op: "loft", ruled: values.ruled, profiles: profiles.map((p) => ({ points: p.geometry.points })) }]);
		if (results) this.add_solids(__("Loft"), results, this.ctx.current_layer());
	}

	async sweep() {
		const profiles = this.selected((e) => is_closed_profile(e));
		const paths = this.selected((e) => (e.entity_type === "Polyline" && !e.geometry.closed) || e.entity_type === "Line");
		if (profiles.length !== 1 || paths.length !== 1) throw new Error(__("Select one closed polyline (the profile) and one open polyline or line (the path)."));
		const p = paths[0];
		const path = p.entity_type === "Line" ? [p.geometry.p0, p.geometry.p1] : p.geometry.points;
		const item = { key: "sweep", op: "sweep", profile: profiles[0].geometry.points, path, path_bulges: p.geometry.bulges || null };
		const results = await this.ctx.build_solids([item]);
		if (results) this.add_solids(__("Sweep"), results, this.ctx.current_layer());
	}

	// ------------------------------------------------------------------ section planes

	// A plane from the selection: vertical along a selected line, or horizontal at an elevation.
	async ask_plane(title) {
		const lines = this.selected((e) => e.entity_type === "Line");
		const values = await this.ctx.ask({
			title,
			fields: [
				{
					key: "kind",
					label: __("Plane"),
					type: "select",
					options: [
						...(lines.length ? [{ value: "line", label: __("Vertical, along the selected line") }] : []),
						{ value: "level", label: __("Horizontal, at an elevation") },
					],
					default: lines.length ? "line" : "level",
				},
				{ key: "z", label: __("Elevation"), type: "number", default: this.ctx.viewport.elevation || 0, show_if: (v) => v.kind === "level" },
				{ key: "flip", label: __("Side"), type: "check", check_label: __("Keep the other side"), default: false },
			],
		});
		if (!values) return null;
		let plane;
		if (values.kind === "line") {
			const { p0, p1 } = lines[0].geometry;
			const d = [p1[0] - p0[0], p1[1] - p0[1]];
			const len = Math.hypot(d[0], d[1]) || 1;
			plane = { point: p0, normal: [-d[1] / len, d[0] / len, 0] };
		} else plane = { point: [0, 0, values.z], normal: [0, 0, -1] };
		if (values.flip) plane.normal = plane.normal.map((v) => -v);
		return plane;
	}

	async section_plane() {
		const plane = await this.ask_plane(__("Section View"));
		if (plane) this.set_section(plane);
	}

	set_section(plane) {
		this.section = plane;
		this.ctx.viewport.set_section_plane(plane);
		this.ctx.message(plane ? __("Section view on: everything in front of the plane is hidden.") : __("Section view off."));
	}

	async section_cut() {
		const solids = this.selected((e) => e.entity_type === "Solid" && e.geometry_ref);
		const surfaces = this.selected((e) => e.entity_type === "Surface");
		if (!solids.length && !surfaces.length) throw new Error(__("Select the solids or surfaces to cut."));
		const plane = await this.ask_plane(__("Section Lines"));
		if (!plane) return;
		const layer = this.ensure_layer("SECTION", "#4aa8d8");
		let count = 0;
		if (solids.length) {
			const r = await this.analysis("section_solids", { geometry_refs: solids.map((s) => s.geometry_ref), origin: plane.point, normal: plane.normal }, solids);
			if (r) count += this.add_polylines(__("Section Lines"), r.items.flatMap((i) => i.polylines.map((points) => ({ points }))), layer);
		}
		if (surfaces.length) {
			if (Math.abs(plane.normal[2]) > 0.99) {
				// a horizontal cut through a surface is a contour
				for (const s of surfaces) {
					const r = await this.analysis("contours", { geometry_refs: [s.geometry_ref], interval: 1e9, base: plane.point[2] }, [s]);
					if (r) count += this.add_polylines(__("Section Lines"), r.contours, layer);
				}
			} else {
				// a vertical cut: the profile along the line, back in model coordinates
				const ext = 1e6;
				const t = [plane.normal[1], -plane.normal[0]];
				const a = [plane.point[0] - t[0] * ext, plane.point[1] - t[1] * ext, 0];
				const b = [plane.point[0] + t[0] * ext, plane.point[1] + t[1] * ext, 0];
				const r = await this.analysis("profile", { geometry_refs: surfaces.map((s) => s.geometry_ref), alignment: [a, b] }, surfaces);
				if (r) for (const runs of Object.values(r.profiles)) count += this.add_polylines(__("Section Lines"), runs.map((run) => ({ points: run.map((p) => [p[1], p[2], p[3]]) })), layer);
			}
		}
		this.ctx.message(__("{0} section line(s) added on layer SECTION.", [count]), "result");
	}
}
