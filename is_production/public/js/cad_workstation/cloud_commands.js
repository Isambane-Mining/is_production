// Phase 6 point-cloud commands (Point Cloud menu): colour and point size, editing (crop, classes, noise, decimate,
// ground), surface from a cloud, comparison with a surface and export. Every edit is a worker job that writes a new
// LAZ; the cloud entity then points to it as one undoable change (the old file stays in storage and in revisions).

import { call } from "./api";
import { closed_loop, new_entity_id } from "./geometry";
import { CLASS_NAMES } from "./pointclouds";
import { entity_label } from "./surface_commands";

const CLASS_OPTIONS = Object.entries(CLASS_NAMES).map(([value, label]) => ({ value: Number(value), label: `${value} · ${label}` }));

function parse_classes(text) {
	return String(text || "")
		.split(/[,;\s]+/)
		.map((x) => parseInt(x, 10))
		.filter((x) => Number.isInteger(x) && x >= 0 && x <= 255);
}

export class CloudCommands {
	// ctx: as MiningCommands (session, viewport, model(), selection(), select(), message, ask, run_job, show_result,
	// surfaces: the SurfaceCommands instance)
	constructor(ctx) {
		this.ctx = ctx;
	}

	get session() {
		return this.ctx.session;
	}

	get layer() {
		return this.ctx.viewport.clouds;
	}

	async run(command, args) {
		const handler = {
			cloud_colour: () => this.colour(args),
			cloud_size: () => this.point_size(),
			cloud_crop: () => this.crop(),
			cloud_noise: () => this.noise(),
			cloud_decimate: () => this.decimate(),
			cloud_ground: () => this.ground(),
			cloud_set_class: () => this.set_class(),
			cloud_classes: () => this.classes(),
			cloud_surface: () => this.surface(),
			cloud_compare: () => this.compare(),
			cloud_export: () => this.export_cloud(),
		}[command];
		if (!handler) return false;
		await handler();
		return true;
	}

	clouds() {
		return [...this.session.entities.values()].filter((e) => e.entity_type === "Point Cloud");
	}

	// the cloud to work on: the selected one, the only one, or the one chosen
	async target(title) {
		const selected = this.ctx.selection().map((id) => this.session.entities.get(id)).filter((e) => e && e.entity_type === "Point Cloud");
		let cloud = selected.length === 1 ? selected[0] : null;
		const all = this.clouds();
		if (!cloud && all.length === 1) cloud = all[0];
		if (!cloud) {
			if (!all.length) throw new Error(__("The model has no point cloud: import a LAS / LAZ / XYZ file as a point cloud (File → Import)."));
			const v = await this.ctx.ask({ title, fields: [{ key: "id", label: __("Point cloud"), type: "select", options: all.map((e) => ({ value: e.entity_id, label: entity_label(e) })), default: all[0].entity_id }], submit_label: __("Next") });
			if (!v) return null;
			cloud = this.session.entities.get(v.id);
		}
		// an unsaved edit of a cloud is fine (the server knows the files its jobs made); a cloud never saved is not
		if (!cloud._stats) throw new Error(__("{0} is still loading.", [entity_label(cloud)]));
		return cloud;
	}

	// a closed outline in the selection (saved), for crop / set class
	outline() {
		const loops = this.ctx.selection().map((id) => this.session.entities.get(id)).filter((e) => e && (closed_loop(e) || (e.entity_type === "Polyline" && e.geometry.closed)));
		if (loops.length > 1) throw new Error(__("Select one closed outline."));
		if (loops.length) this.ctx.surfaces.require_saved(loops);
		return loops[0] || null;
	}

	async edit(cloud, operations, label) {
		const done = await this.ctx.run_job("cloud_job", { model: this.ctx.model().name, operation: "edit", params: { geometry_ref: cloud.geometry_ref, name: cloud.geometry.name, operations } });
		if (!done) return null;
		const r = done.results;
		this.swap(cloud, r, label);
		this.ctx.show_result({ kind: "cloud_report", title: label, result: r });
		this.ctx.message(done.status_message, "result");
		return r;
	}

	swap(cloud, r, label) {
		this.session.apply(label, (w) => w.put({ ...cloud, geometry_ref: r.geometry_ref, bbox: r.stats.bbox, geometry: { ...cloud.geometry, source: r.source }, _stats: r.stats }));
	}

	// -------------------------------------------------------------- display

	colour(mode) {
		const options = {};
		if (mode === "deviation") {
			const ranges = this.clouds().map((e) => e._stats && e._stats.deviation_range).filter(Boolean);
			if (!ranges.length) throw new Error(__("No cloud has deviations yet: Point Cloud → Compare with Surface."));
		}
		this.layer.set_mode(mode, options);
		this.ctx.message(__("Point clouds coloured by {0}.", [__(mode)]), "result");
	}

	async point_size() {
		const v = await this.ctx.ask({ title: __("Point Size"), fields: [{ key: "size", label: __("Size (pixels)"), type: "number", default: this.layer.size }, { key: "range", label: __("Deviation colour range ± (m)"), type: "number", default: this.layer.deviation_range }], submit_label: __("Apply") });
		if (!v) return;
		this.layer.set_size(Math.max(1, Math.min(12, Number(v.size) || 2)));
		if (v.range > 0) this.layer.set_mode(this.layer.mode, { deviation_range: Number(v.range) });
	}

	// -------------------------------------------------------------- editing

	async crop() {
		const outline = this.outline();
		const cloud = await this.target(__("Crop Point Cloud"));
		if (!cloud) return;
		const v = await this.ctx.ask({
			title: __("Crop Point Cloud"),
			intro: outline ? __("Inside or outside the selected outline, and / or an elevation band.") : __("No outline is selected: crop by elevation only (select a closed outline with the cloud to crop in plan)."),
			fields: [
				...(outline ? [{ key: "keep", label: __("Keep"), type: "select", options: [{ value: "inside", label: __("Inside the outline") }, { value: "outside", label: __("Outside the outline") }], default: "inside" }] : []),
				{ key: "zmin", label: __("From elevation"), type: "number", help: __("Empty: no lower limit.") },
				{ key: "zmax", label: __("To elevation"), type: "number", help: __("Empty: no upper limit.") },
			],
			submit_label: __("Crop"),
			check: (x) => (outline || x.zmin !== "" || x.zmax !== "" ? null : __("Give an elevation band (or select an outline).")),
		});
		if (!v) return;
		const op = { op: "crop", keep: v.keep || "inside", zmin: v.zmin === "" ? null : v.zmin, zmax: v.zmax === "" ? null : v.zmax };
		if (outline) op.outline = outline.entity_id;
		await this.edit(cloud, [op], __("Crop Point Cloud"));
	}

	async noise() {
		const cloud = await this.target(__("Remove Noise"));
		if (!cloud) return;
		const v = await this.ctx.ask({
			title: __("Remove Noise"),
			intro: __("Statistical outliers: a point whose mean distance to its nearest neighbours is far above the cloud's typical spacing (birds, dust, multipath) is removed."),
			fields: [
				{ key: "k", label: __("Neighbours"), type: "number", default: 8 },
				{ key: "std", label: __("Limit (standard deviations)"), type: "number", default: 3, help: __("Smaller removes more.") },
			],
			submit_label: __("Remove"),
		});
		if (v) await this.edit(cloud, [{ op: "noise", k: v.k, std: v.std }], __("Remove Noise"));
	}

	async decimate() {
		const cloud = await this.target(__("Decimate"));
		if (!cloud) return;
		const v = await this.ctx.ask({
			title: __("Decimate Point Cloud"),
			fields: [
				{ key: "mode", label: __("Method"), type: "select", options: [{ value: "voxel", label: __("One point per cube") }, { value: "random", label: __("Random share") }], default: "voxel" },
				{ key: "cell", label: __("Cube size (m)"), type: "number", default: 0.5, show_if: (x) => x.mode === "voxel" },
				{ key: "keep", label: __("Keep in each cube"), type: "select", options: [{ value: "lowest", label: __("The lowest point (terrain)") }, { value: "highest", label: __("The highest point") }, { value: "centre", label: __("The point nearest the centre") }], default: "lowest", show_if: (x) => x.mode === "voxel" },
				{ key: "fraction", label: __("Share to keep (0–1)"), type: "number", default: 0.25, show_if: (x) => x.mode === "random" },
			],
			submit_label: __("Decimate"),
		});
		if (v) await this.edit(cloud, [{ op: "decimate", mode: v.mode, cell: v.cell, keep: v.keep, fraction: v.fraction }], __("Decimate Point Cloud"));
	}

	async ground() {
		const cloud = await this.target(__("Classify Ground"));
		if (!cloud) return;
		const v = await this.ctx.ask({
			title: __("Classify Ground"),
			intro: __("Progressive morphological filter: the lowest points per cell are opened with growing windows; what stands out above the opening (buildings, plant, vegetation, stockpile equipment) is not ground. The largest window must be wider than the widest object to remove."),
			fields: [
				{ key: "cell", label: __("Cell (m)"), type: "number", default: 1 },
				{ key: "max_window", label: __("Largest object width (m)"), type: "number", default: 40 },
				{ key: "slope", label: __("Terrain slope (rise per m)"), type: "number", default: 0.3, help: __("Steeper ground needs more: benches and batters ~0.5–1.") },
				{ key: "dh_max", label: __("Largest height threshold (m)"), type: "number", default: 3 },
				{ key: "tolerance", label: __("Ground tolerance (m)"), type: "number", default: 0.3 },
			],
			submit_label: __("Classify"),
		});
		if (!v) return;
		await this.edit(cloud, [{ op: "ground", ...v }], __("Classify Ground"));
		this.layer.set_mode("classification");
	}

	async set_class() {
		const outline = this.outline();
		const cloud = await this.target(__("Set Class"));
		if (!cloud) return;
		const v = await this.ctx.ask({
			title: __("Set Class"),
			intro: outline ? __("Points inside the selected outline.") : __("Every point (select a closed outline with the cloud to limit it in plan)."),
			fields: [
				{ key: "to", label: __("New class"), type: "select", options: CLASS_OPTIONS, default: 6 },
				{ key: "from", label: __("Only points now in classes"), help: __("e.g. 0, 1 — empty: any class") },
				{ key: "zmin", label: __("From elevation"), type: "number" },
				{ key: "zmax", label: __("To elevation"), type: "number" },
			],
			submit_label: __("Set"),
		});
		if (!v) return;
		const op = { op: "set_class", to: Number(v.to), from: parse_classes(v.from), zmin: v.zmin === "" ? null : v.zmin, zmax: v.zmax === "" ? null : v.zmax };
		if (outline) op.outline = outline.entity_id;
		await this.edit(cloud, [op], __("Set Class"));
		this.layer.set_mode("classification");
	}

	async classes() {
		const cloud = await this.target(__("Keep or Remove Classes"));
		if (!cloud) return;
		const present = Object.keys((cloud._stats && cloud._stats.classes) || {}).join(", ");
		const v = await this.ctx.ask({
			title: __("Keep or Remove Classes"),
			intro: present ? __("The cloud has classes {0}.", [present]) : "",
			fields: [
				{ key: "keep", label: __("Action"), type: "select", options: [{ value: "keep", label: __("Keep only these classes") }, { value: "drop", label: __("Remove these classes") }], default: "keep" },
				{ key: "classes", label: __("Classes"), default: "2", required: true, help: __("e.g. 2 (ground), 7, 18 (noise)") },
			],
			submit_label: __("Apply"),
			check: (x) => (parse_classes(x.classes).length ? null : __("Give one or more class numbers.")),
		});
		if (v) await this.edit(cloud, [{ op: "classes", classes: parse_classes(v.classes), keep: v.keep === "keep" }], __("Point Cloud Classes"));
	}

	// -------------------------------------------------------------- surfaces, comparison, export

	async surface() {
		const cloud = await this.target(__("Surface from Point Cloud"));
		if (!cloud) return;
		const has_ground = Boolean(cloud._stats && cloud._stats.classes && cloud._stats.classes["2"]);
		const v = await this.ctx.ask({
			title: __("Surface from Point Cloud"),
			fields: [
				{ key: "name", label: __("Surface name"), default: __("{0} surface", [cloud.geometry.name]), required: true },
				{ key: "classes", label: __("Classes"), default: has_ground ? "2" : "", help: __("Empty: every point. 2 = ground (Classify Ground first).") },
				{ key: "thin", label: __("Thin to one point per (m)"), type: "number", default: 1, help: __("The lowest point per cell; 0 keeps every point.") },
				{ key: "max_edge", label: __("Longest triangle edge (m)"), type: "number", help: __("Empty: no limit.") },
				{ key: "surface_type", label: __("Surface type"), type: "select", options: ["Survey Surface", "Terrain Surface", "As-Built Surface", "Design Surface"], default: "Survey Surface" },
			],
			submit_label: __("Build Surface"),
		});
		if (!v) return;
		const done = await this.ctx.run_job("cloud_job", { model: this.ctx.model().name, operation: "surface", params: { geometry_ref: cloud.geometry_ref, name: cloud.geometry.name, classes: parse_classes(v.classes), thin: v.thin, max_edge: v.max_edge || null } });
		if (!done) return;
		const r = done.results;
		const layer = this.ctx.surfaces.ensure_layer("SURVEY", "#8fae6b");
		const entity = { entity_id: new_entity_id(), entity_type: "Surface", cad_layer: layer, geometry: { name: v.name, surface_type: v.surface_type, source: r.source }, geometry_ref: r.geometry_ref, bbox: r.stats.bbox, area: r.stats.surface_area, validation_status: "Valid", _stats: r.stats };
		this.session.apply(__("Surface from Point Cloud"), (w) => w.put(entity));
		this.ctx.select([entity.entity_id]);
		this.ctx.message(done.status_message, "result");
	}

	async compare() {
		const cloud = await this.target(__("Compare with Surface"));
		if (!cloud) return;
		const surfaces = this.ctx.surfaces.surfaces();
		if (!surfaces.length) throw new Error(__("The model has no surface to compare with."));
		const v = await this.ctx.ask({
			title: __("Compare Point Cloud with Surface"),
			intro: __("Every point's vertical distance to the surface (+ above, − below): statistics and a histogram, and the cloud coloured by it (blue below, red above)."),
			fields: [
				{ key: "surface", label: __("Surface"), type: "select", options: surfaces.map((e) => ({ value: e.geometry_ref, label: entity_label(e) })), default: surfaces[0].geometry_ref },
				{ key: "tolerance", label: __("Tolerance (m)"), type: "number", default: 0.1 },
			],
			submit_label: __("Compare"),
		});
		if (!v) return;
		this.ctx.surfaces.require_saved(surfaces.filter((e) => e.geometry_ref === v.surface));
		const done = await this.ctx.run_job("cloud_job", { model: this.ctx.model().name, operation: "compare", params: { geometry_ref: cloud.geometry_ref, name: cloud.geometry.name, surface: v.surface, tolerance: v.tolerance } });
		if (!done) return;
		const r = done.results;
		this.swap(cloud, r, __("Compare with Surface"));
		const c = r.comparison;
		this.layer.set_mode("deviation", { deviation_range: Math.max(Math.abs(c.p05 || 0), Math.abs(c.p95 || 0), v.tolerance * 3) });
		this.ctx.show_result({ kind: "cloud_compare", title: __("Point Cloud vs Surface"), result: r });
		this.ctx.message(done.status_message, "result");
	}

	async export_cloud() {
		const cloud = await this.target(__("Export Point Cloud"));
		if (!cloud) return;
		const v = await this.ctx.ask({ title: __("Export Point Cloud"), fields: [{ key: "format", label: __("Format"), type: "select", options: ["LAZ", "LAS", "E57", "XYZ", "CSV"], default: "LAZ" }], submit_label: __("Export") });
		if (!v) return;
		const done = await this.ctx.run_job("cloud_job", { model: this.ctx.model().name, operation: "export", params: { geometry_ref: cloud.geometry_ref, name: cloud.geometry.name, format: v.format } });
		if (!done) return;
		window.open(done.results.file_url, "_blank");
		this.ctx.message(done.status_message, "result");
	}

	// display tiles for a cloud that has none (made before, or by an older version)
	async make_tiles(entity) {
		return this.ctx.run_job("cloud_job", { model: this.ctx.model().name, operation: "tiles", params: { geometry_ref: entity.geometry_ref } });
	}
}
