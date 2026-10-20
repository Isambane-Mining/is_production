// Edit session of one layer in the GIS Workstation.
//
// Changes are made to OpenLayers features in the browser and kept in an undo/redo history; nothing
// reaches the server until Save, which sends every pending add/update/delete in one request
// (gis_edit_service.save_edits: all or nothing, revision-checked, validated by the GIS Feature
// document). Split and merge are computed on the server (shapely) but only applied here, so they can be
// undone like anything else.
//
// Every feature the session touches (selects or changes) is kept in `features`, so reloading the layer
// for a new view never drops an unsaved change. New features have negative ids until saved.

import Collection from "ol/Collection.js";
import Feature from "ol/Feature.js";
import GeoJSON from "ol/format/GeoJSON.js";
import { Draw, DragBox, Modify, Snap, Translate } from "ol/interaction.js";
import { MultiLineString, MultiPoint, MultiPolygon } from "ol/geom.js";
import { createEmpty, extend, getCenter } from "ol/extent.js";
import { platformModifierKeyOnly } from "ol/events/condition.js";
import { call } from "./api.js";

const EDIT = "is_production.geo_planning.services.gis_edit_service.";
const geojson = new GeoJSON();
const __ = (...args) => window.__(...args);

export function base_kind(geometry_type) {
	if (/Point/.test(geometry_type)) return "Point";
	if (/LineString/.test(geometry_type)) return "LineString";
	if (/Polygon/.test(geometry_type)) return "Polygon";
	return null;
}

export class EditSession {
	constructor(gis_map, name, schema, on) {
		this.gis = gis_map;
		this.map = gis_map.map;
		this.entry = gis_map.layers[name];
		this.source = this.entry.source;
		this.name = name;
		this.schema = schema;
		this.on = on; // { change(), selection(ids), message(text, kind) }

		this.features = new Map(); // id -> OpenLayers feature, for every feature touched
		this.exists = new Map(); // id -> currently part of the layer
		this.original = new Map(); // id -> state when first touched (saved features only)
		this.undo_stack = [];
		this.redo_stack = [];
		this.selected = new Collection();
		this.next_ref = -1;
		this.tool = "select";
		this.snap_on = true;
		this.trace_on = false;
		this.default_z = "";
		this.kind = base_kind(schema.geometry_type) || "Polygon"; // what "Add" draws in Geometry datasets
		this.snaps = [];
		this.draw = null;

		this.modify = new Modify({ features: this.selected }); // Alt+click removes a vertex
		this.modify.on("modifystart", (e) => this.begin(e.features.getArray()));
		this.modify.on("modifyend", () => this.end(__("Edit vertices")));
		this.translate = new Translate({ features: this.selected });
		this.translate.on("translatestart", (e) => this.begin(e.features.getArray()));
		this.translate.on("translateend", () => this.end(__("Move")));
		this.box = new DragBox({ condition: platformModifierKeyOnly }); // Ctrl/Cmd + drag: box select
		this.box.on("boxend", () => this.select_box(this.box.getGeometry().getExtent()));
		for (const i of [this.modify, this.translate, this.box]) this.map.addInteraction(i);

		this.entry.session = this;
		this.set_tool("select");
	}

	destroy() {
		this.stop_draw();
		for (const i of [this.modify, this.translate, this.box, ...this.snaps]) this.map.removeInteraction(i);
		this.entry.session = null;
		this.entry.layer.changed();
	}

	// ------------------------------------------------------------ tools

	// tools: select, add, vertex, move, add_part, split; "none" while a map navigation tool is in use
	set_tool(tool) {
		this.stop_draw();
		this.tool = tool;
		this.modify.setActive(tool === "vertex");
		this.translate.setActive(tool === "move");
		if (tool === "add") this.start_draw(this.kind, "add");
		if (tool === "add_part") this.start_draw(this.part_kind(), "add_part");
		if (tool === "split") this.start_draw("LineString", "split");
		this.update_snap();
		this.changed();
	}

	start_draw(kind, purpose) {
		if (!kind) return;
		this.draw = new Draw({ type: kind, trace: this.trace_on, traceSource: this.source, stopClick: true });
		this.draw.on("drawend", (e) => this.after_draw(e.feature.getGeometry(), purpose));
		this.map.addInteraction(this.draw);
	}

	stop_draw() {
		if (this.draw) this.map.removeInteraction(this.draw);
		this.draw = null;
	}

	cancel_sketch() {
		if (this.draw) this.draw.abortDrawing();
	}

	set_snap(on) {
		this.snap_on = on;
		this.update_snap();
		this.changed();
	}

	set_trace(on) {
		this.trace_on = on;
		if (this.draw) this.set_tool(this.tool); // rebuild the drawing tool with tracing on/off
		this.changed();
	}

	set_kind(kind) {
		this.kind = kind;
		if (this.tool === "add" || this.tool === "add_part") this.set_tool(this.tool);
	}

	// Snap must be added after the interactions it serves: rebuilt whenever a tool changes.
	update_snap() {
		for (const s of this.snaps) this.map.removeInteraction(s);
		this.snaps = [];
		if (!this.snap_on) return;
		for (const entry of Object.values(this.gis.layers)) {
			if (entry.kind === "service" || !entry.layer.getVisible()) continue; // tiles cannot be snapped to
			const snap = new Snap({ source: entry.source, pixelTolerance: 10 });
			this.map.addInteraction(snap);
			this.snaps.push(snap);
		}
	}

	part_kind() {
		const f = this.selected.item(0);
		return f ? base_kind(f.getGeometry().getType()) : base_kind(this.schema.geometry_type);
	}

	// ------------------------------------------------------------ selection

	click(event) {
		if (!["select", "vertex", "move"].includes(this.tool)) return;
		let hit = null;
		this.map.forEachFeatureAtPixel(
			event.pixel,
			(f) => {
				hit = f;
				return true;
			},
			{ hitTolerance: 5, layerFilter: (l) => l === this.entry.layer }
		);
		const additive = event.originalEvent.shiftKey || event.originalEvent.ctrlKey || event.originalEvent.metaKey;
		if (!additive) this.selected.clear();
		if (hit) {
			this.touch(hit);
			const i = this.selected.getArray().indexOf(hit);
			if (additive && i >= 0) this.selected.removeAt(i);
			else if (i < 0) this.selected.push(hit);
		}
		this.selection_changed();
	}

	select_box(extent) {
		this.source.forEachFeatureIntersectingExtent(extent, (f) => {
			this.touch(f);
			if (!this.selected.getArray().includes(f)) this.selected.push(f);
		});
		this.selection_changed();
	}

	select_ids(ids, additive = false) {
		if (!additive) this.selected.clear();
		for (const id of ids) {
			const f = this.features.get(id) || this.source.getFeatureById(id);
			if (!f || this.exists.get(id) === false) continue;
			this.touch(f);
			const i = this.selected.getArray().indexOf(f);
			if (additive && i >= 0) this.selected.removeAt(i);
			else if (i < 0) this.selected.push(f);
		}
		this.selection_changed();
	}

	selected_ids() {
		return this.selected.getArray().map((f) => f.getId());
	}

	is_selected(feature) {
		return this.selected.getArray().includes(feature);
	}

	selection_changed() {
		this.entry.layer.changed();
		this.on.selection(this.selected_ids());
		if (["add_part"].includes(this.tool)) this.set_tool(this.tool); // the part kind follows the target
	}

	// ------------------------------------------------------------ history

	touch(f) {
		const id = f.getId();
		if (this.features.has(id)) return;
		this.features.set(id, f);
		this.exists.set(id, true);
		if (id > 0) this.original.set(id, this.state(id));
	}

	state(id) {
		const f = this.features.get(id);
		if (!f || !this.exists.get(id)) return { exists: false };
		return {
			exists: true,
			geometry: f.getGeometry().clone(),
			attributes: { ...(f.get("attributes") || {}) },
			label: f.get("label") ?? null,
		};
	}

	states(ids) {
		return new Map(ids.map((id) => [id, this.state(id)]));
	}

	// Interactions that change features as the user drags (Modify, Translate)
	begin(features) {
		features.forEach((f) => this.touch(f));
		this.before = this.states(features.map((f) => f.getId()));
	}

	end(label) {
		if (!this.before) return;
		this.push(label, this.before, this.states([...this.before.keys()]));
		this.before = null;
	}

	// Discrete edits: ids may include new ones (not yet in `features`: they start as not existing)
	record(label, ids, fn) {
		const before = this.states(ids);
		fn();
		this.push(label, before, this.states(ids));
	}

	push(label, before, after) {
		this.undo_stack.push({ label, before, after });
		if (this.undo_stack.length > 200) this.undo_stack.shift();
		this.redo_stack = [];
		this.changed();
	}

	apply(states) {
		for (const [id, s] of states) {
			const f = this.features.get(id);
			if (!f) continue;
			if (!s.exists) {
				this.exists.set(id, false);
				if (this.source.hasFeature(f)) this.source.removeFeature(f);
				const i = this.selected.getArray().indexOf(f);
				if (i >= 0) this.selected.removeAt(i);
			} else {
				f.setGeometry(s.geometry.clone());
				f.set("attributes", { ...s.attributes });
				f.set("label", s.label);
				this.exists.set(id, true);
				if (!this.source.hasFeature(f)) this.source.addFeature(f);
			}
		}
		this.selection_changed();
	}

	undo() {
		const step = this.undo_stack.pop();
		if (!step) return;
		this.apply(step.before);
		this.redo_stack.push(step);
		this.changed();
	}

	redo() {
		const step = this.redo_stack.pop();
		if (!step) return;
		this.apply(step.after);
		this.undo_stack.push(step);
		this.changed();
	}

	// Called by the map after it reloads the layer for a new view: keep what this session holds.
	keep_in(features) {
		return features.filter((f) => !this.features.has(f.getId()));
	}

	restore_into_source() {
		for (const [id, f] of this.features) {
			if (this.exists.get(id) && !this.source.hasFeature(f)) this.source.addFeature(f);
		}
	}

	// ------------------------------------------------------------ editing operations

	after_draw(geometry, purpose) {
		if (purpose === "add") this.add_feature(this.to_dataset_type(geometry));
		else if (purpose === "add_part") this.add_part(geometry);
		else if (purpose === "split") this.split(geometry);
	}

	new_feature(geometry, attributes = null, label = null) {
		const id = this.next_ref--;
		const f = new Feature({ geometry });
		f.setId(id);
		f.setProperties({
			attributes: attributes ? { ...attributes } : this.default_attributes(),
			label,
			revision: 0,
			validation_status: null,
		});
		return f;
	}

	place(f) {
		this.features.set(f.getId(), f);
		this.exists.set(f.getId(), true);
		if (!this.source.hasFeature(f)) this.source.addFeature(f);
	}

	add_feature(geometry) {
		if (!this.schema.can_create) return this.on.message(__("You cannot create features in this dataset."), "error");
		const f = this.new_feature(geometry);
		this.record(__("Add feature"), [f.getId()], () => this.place(f));
		this.select_ids([f.getId()]);
	}

	add_part(part) {
		const f = this.selected.getLength() === 1 ? this.selected.item(0) : null;
		if (!f) return this.on.message(__("Select exactly one feature to add a part to."), "warning");
		let geometry = f.getGeometry().clone();
		if (!geometry.getType().startsWith("Multi")) {
			if (this.schema.geometry_type !== "Geometry") {
				return this.on.message(__("{0} datasets hold one part per feature. Use a Multi dataset for multipart features.", [this.schema.geometry_type]), "warning");
			}
			geometry = wrap(geometry);
		}
		if (base_kind(geometry.getType()) !== part.getType()) return this.on.message(__("The part must be a {0}.", [base_kind(geometry.getType())]), "warning");
		this.record(__("Add part"), [f.getId()], () => {
			if (part.getType() === "Point") geometry.appendPoint(part);
			else if (part.getType() === "LineString") geometry.appendLineString(part);
			else geometry.appendPolygon(part);
			f.setGeometry(geometry);
		});
	}

	delete_selected() {
		const ids = this.selected_ids();
		if (!ids.length) return;
		if (ids.some((id) => id > 0) && !this.schema.can_delete) return this.on.message(__("You cannot delete features in this dataset."), "error");
		this.record(__("Delete"), ids, () => {
			for (const id of ids) {
				const f = this.features.get(id);
				this.exists.set(id, false);
				if (this.source.hasFeature(f)) this.source.removeFeature(f);
			}
			this.selected.clear();
		});
		this.selection_changed();
	}

	duplicate_selected() {
		if (!this.schema.can_create) return this.on.message(__("You cannot create features in this dataset."), "error");
		const copies = this.selected.getArray().map((f) => this.new_feature(f.getGeometry().clone(), f.get("attributes"), f.get("label")));
		if (!copies.length) return;
		this.record(__("Duplicate"), copies.map((f) => f.getId()), () => copies.forEach((f) => this.place(f)));
		this.select_ids(copies.map((f) => f.getId()));
		this.on.message(__("Duplicated in place: use Move to shift the copies."), "info");
	}

	rotate_selected(degrees_clockwise) {
		const features = this.selected.getArray();
		if (!features.length || !Number.isFinite(degrees_clockwise)) return;
		const extent = createEmpty();
		features.forEach((f) => extend(extent, f.getGeometry().getExtent()));
		const anchor = getCenter(extent);
		this.record(__("Rotate"), features.map((f) => f.getId()), () =>
			features.forEach((f) => {
				const g = f.getGeometry().clone();
				g.rotate((-degrees_clockwise * Math.PI) / 180, anchor);
				f.setGeometry(g);
			})
		);
	}

	async split(blade) {
		const targets = this.selected.getArray().filter((f) => !/Point/.test(f.getGeometry().getType()));
		if (!targets.length) return this.on.message(__("Select the lines or polygons to split, then draw the cutting line."), "warning");
		try {
			const { results } = await call(EDIT + "geometry_operation", {
				dataset: this.name,
				operation: "split",
				geometries: targets.map((f) => geojson.writeGeometryObject(f.getGeometry())),
				blade: geojson.writeGeometryObject(blade),
			});
			const created = [];
			const touched = [];
			targets.forEach((f, i) => {
				if (!results[i]) return;
				touched.push(f.getId());
				const [first, ...rest] = results[i].map((g) => geojson.readGeometry(g));
				created.push(...rest.map((g) => ({ target: f, geometry: g, copy: this.new_feature(g, f.get("attributes"), f.get("label")) })));
				f.__split_first = first;
			});
			if (created.length && !this.schema.can_create) return this.on.message(__("You cannot create features in this dataset."), "error");
			this.record(__("Split"), [...touched, ...created.map((c) => c.copy.getId())], () => {
				for (const id of touched) {
					const f = this.features.get(id);
					f.setGeometry(f.__split_first);
					delete f.__split_first;
				}
				created.forEach((c) => this.place(c.copy));
			});
			this.select_ids([...touched, ...created.map((c) => c.copy.getId())]);
		} catch (e) {
			this.on.message(e.message, "error");
		}
	}

	async merge_selected() {
		const features = this.selected.getArray();
		if (features.length < 2) return this.on.message(__("Select at least two features to merge."), "warning");
		// keep the oldest saved feature (its id, revision history and attributes); the rest are deleted
		const saved = features.filter((f) => f.getId() > 0).sort((a, b) => a.getId() - b.getId());
		const keep = saved[0] || features[0];
		const others = features.filter((f) => f !== keep);
		if (others.some((f) => f.getId() > 0) && !this.schema.can_delete) return this.on.message(__("Merging deletes features, which you cannot do here."), "error");
		try {
			const { geometry } = await call(EDIT + "geometry_operation", {
				dataset: this.name,
				operation: "merge",
				geometries: features.map((f) => geojson.writeGeometryObject(f.getGeometry())),
			});
			this.record(__("Merge"), features.map((f) => f.getId()), () => {
				keep.setGeometry(geojson.readGeometry(geometry));
				for (const f of others) {
					this.exists.set(f.getId(), false);
					if (this.source.hasFeature(f)) this.source.removeFeature(f);
				}
			});
			this.select_ids([keep.getId()]);
			this.on.message(__("Merged into feature {0}; its attributes were kept.", [keep.getId() > 0 ? keep.getId() : __("(new)")]), "info");
		} catch (e) {
			this.on.message(e.message, "error");
		}
	}

	set_attribute(id, field, value) {
		const f = this.features.get(id) || this.source.getFeatureById(id);
		if (!f) return;
		this.touch(f);
		this.record(__("Edit attribute"), [id], () => f.set("attributes", { ...(f.get("attributes") || {}), [field]: value }));
	}

	set_label(id, label) {
		const f = this.features.get(id) || this.source.getFeatureById(id);
		if (!f) return;
		this.touch(f);
		this.record(__("Edit label"), [id], () => f.set("label", label === "" ? null : label));
	}

	bulk_set(ids, field, value) {
		const features = ids.map((id) => this.features.get(id) || this.source.getFeatureById(id)).filter(Boolean);
		features.forEach((f) => this.touch(f));
		this.record(__("Set {0} on {1} features", [field, features.length]), features.map((f) => f.getId()), () =>
			features.forEach((f) =>
				field === "__label" ? f.set("label", value === "" ? null : value) : f.set("attributes", { ...(f.get("attributes") || {}), [field]: value })
			)
		);
	}

	default_attributes() {
		const out = {};
		for (const a of this.schema.attributes) out[a.field_name] = typed(a, a.default_value);
		return out;
	}

	to_dataset_type(geometry) {
		return this.schema.geometry_type.startsWith("Multi") && !geometry.getType().startsWith("Multi") ? wrap(geometry) : geometry;
	}

	// ------------------------------------------------------------ validate and save

	pending() {
		const adds = [];
		const updates = [];
		const deletes = [];
		for (const [id, f] of this.features) {
			const exists = this.exists.get(id);
			if (id < 0) {
				if (exists) adds.push({ ref: String(id), geometry: geojson.writeGeometryObject(f.getGeometry()), attributes: f.get("attributes") || {}, label: f.get("label") ?? null });
				continue;
			}
			if (!exists) {
				deletes.push({ id, revision: f.get("revision") });
				continue;
			}
			const o = this.original.get(id);
			const change = { id, revision: f.get("revision") };
			if (geojson.writeGeometry(o.geometry) !== geojson.writeGeometry(f.getGeometry())) change.geometry = geojson.writeGeometryObject(f.getGeometry());
			if (JSON.stringify(o.attributes) !== JSON.stringify(f.get("attributes") || {})) change.attributes = f.get("attributes") || {};
			if ((o.label ?? null) !== (f.get("label") ?? null)) change.label = f.get("label") ?? null;
			if (Object.keys(change).length > 2) updates.push(change);
		}
		return { adds, updates, deletes };
	}

	counts() {
		const p = this.pending();
		return { adds: p.adds.length, updates: p.updates.length, deletes: p.deletes.length, total: p.adds.length + p.updates.length + p.deletes.length };
	}

	async validate() {
		const p = this.pending();
		const items = [...p.adds.map((a) => ({ ref: a.ref, geometry: a.geometry })), ...p.updates.filter((u) => u.geometry).map((u) => ({ ref: String(u.id), geometry: u.geometry }))];
		if (!items.length) return [];
		const results = await call(EDIT + "validate_geometries", { dataset: this.name, items });
		for (const r of results) {
			const f = this.features.get(Number(r.ref));
			if (f) f.setProperties({ validation_status: r.valid ? "Valid" : "Invalid", validation_message: r.message });
		}
		this.changed();
		return results;
	}

	async save() {
		const changes = this.pending();
		if (!changes.adds.length && !changes.updates.length && !changes.deletes.length) return { ok: true, nothing: true };
		const result = await call(EDIT + "save_edits", { dataset: this.name, changes, default_z: this.default_z });
		if (result.ok) this.reset();
		else this.select_ids(result.errors.map((e) => Number(e.ref)).filter((id) => this.features.has(id)));
		return result;
	}

	discard() {
		for (const [id, f] of this.features) {
			if (id < 0) {
				if (this.source.hasFeature(f)) this.source.removeFeature(f);
			} else if (this.original.has(id)) {
				this.apply(new Map([[id, this.original.get(id)]]));
			}
		}
		this.reset();
	}

	reset() {
		this.features.clear();
		this.exists.clear();
		this.original.clear();
		this.undo_stack = [];
		this.redo_stack = [];
		this.selected.clear();
		this.selection_changed();
		this.changed();
	}

	changed() {
		this.entry.layer.changed();
		this.on.change();
	}
}

function wrap(geometry) {
	const type = geometry.getType();
	if (type === "Point") return new MultiPoint([geometry.getCoordinates()]);
	if (type === "LineString") return new MultiLineString([geometry.getCoordinates()]);
	if (type === "Polygon") return new MultiPolygon([geometry.getCoordinates()]);
	return geometry;
}

// A value typed in a form or the table, as the attribute's type (the server checks it again).
export function typed(attribute, value) {
	if (value === undefined || value === null || value === "") return null;
	switch (attribute.data_type) {
		case "Integer":
		case "Float": {
			const n = Number(value);
			return Number.isFinite(n) ? n : value;
		}
		case "Boolean":
			if (typeof value === "boolean") return value;
			if (/^(1|true|yes|y)$/i.test(String(value))) return true;
			if (/^(0|false|no|n)$/i.test(String(value))) return false;
			return value;
		default:
			return value;
	}
}
