// The model being edited in the browser: entities and layers as loaded, local changes not yet saved,
// and undo/redo. Nothing is authoritative here: Save sends the changes to save_edits, which checks them,
// records them in the Working revision and returns the server's view (bounding boxes, versions).

export class EditSession {
	constructor() {
		this.reset();
	}

	reset(model = null) {
		this.model = model;
		this.entities = new Map(); // entity_id -> entity
		this.layers = new Map(); // CAD Layer name -> layer
		this.dirty = new Set(); // entity ids created or changed
		this.deleted = new Set(); // entity ids deleted (that exist on the server)
		this.dirty_layers = new Map(); // layer key -> layer change
		this.blocks = new Map(); // block name -> {block_name, base_point, entities}
		this.dirty_blocks = new Set();
		this.references = []; // external references of the model
		this.references_dirty = false;
		this.server_ids = new Set();
		this.undo_stack = [];
		this.redo_stack = [];
		this.listeners = new Set();
	}

	on_change(fn) {
		this.listeners.add(fn);
		return () => this.listeners.delete(fn);
	}

	emit(ids) {
		for (const fn of this.listeners) fn(ids);
	}

	load(model, layers, entities, blocks = [], references = []) {
		const listeners = this.listeners;
		this.reset(model);
		this.listeners = listeners;
		for (const layer of layers) this.layers.set(layer.name, layer);
		for (const block of blocks) this.blocks.set(block.block_name, block);
		this.references = references.map((r) => ({ ...r }));
		this.add_loaded(entities);
	}

	add_loaded(entities) {
		for (const e of entities) {
			this.entities.set(e.entity_id, e);
			this.server_ids.add(e.entity_id);
		}
		this.emit(entities.map((e) => e.entity_id));
	}

	get change_count() {
		return this.dirty.size + this.deleted.size + this.dirty_layers.size + this.dirty_blocks.size + (this.references_dirty ? 1 : 0);
	}

	define_block(block) {
		this.blocks.set(block.block_name, block);
		this.dirty_blocks.add(block.block_name);
	}

	set_references(references) {
		this.references = references;
		this.references_dirty = true;
		this.emit([]);
	}

	layer(name) {
		return this.layers.get(name);
	}

	layer_by_name(layer_name) {
		for (const layer of this.layers.values()) if (layer.layer_name === layer_name) return layer;
		return null;
	}

	is_locked(entity) {
		const layer = this.layers.get(entity.cad_layer);
		return Boolean(layer && layer.locked);
	}

	// One undoable step: fn receives a writer with put(entity) / remove(id).
	apply(label, fn) {
		const before = new Map();
		const writer = {
			put: (entity) => {
				if (!before.has(entity.entity_id)) before.set(entity.entity_id, this.entities.get(entity.entity_id) || null);
				this.entities.set(entity.entity_id, entity);
				this.dirty.add(entity.entity_id);
				this.deleted.delete(entity.entity_id);
			},
			remove: (id) => {
				if (!this.entities.has(id)) return;
				if (!before.has(id)) before.set(id, this.entities.get(id));
				this.entities.delete(id);
				this.dirty.delete(id);
				if (this.server_ids.has(id)) this.deleted.add(id);
			},
		};
		fn(writer);
		if (!before.size) return;
		const after = new Map([...before.keys()].map((id) => [id, this.entities.get(id) || null]));
		this.undo_stack.push({ label, before, after });
		if (this.undo_stack.length > 200) this.undo_stack.shift();
		this.redo_stack = [];
		this.emit([...before.keys()]);
	}

	restore(states) {
		for (const [id, entity] of states) {
			if (entity) {
				this.entities.set(id, entity);
				this.dirty.add(id);
				this.deleted.delete(id);
			} else {
				this.entities.delete(id);
				this.dirty.delete(id);
				if (this.server_ids.has(id)) this.deleted.add(id);
			}
		}
		this.emit([...states.keys()]);
	}

	undo() {
		const step = this.undo_stack.pop();
		if (!step) return null;
		this.restore(step.before);
		this.redo_stack.push(step);
		return step.label;
	}

	redo() {
		const step = this.redo_stack.pop();
		if (!step) return null;
		this.restore(step.after);
		this.undo_stack.push(step);
		return step.label;
	}

	set_layer(key, change) {
		const existing = this.layers.get(key);
		const merged = { ...(existing || {}), ...change };
		this.layers.set(key, merged);
		this.dirty_layers.set(key, { ...(this.dirty_layers.get(key) || {}), ...(existing ? { name: existing.name } : {}), ...change });
		this.emit([]);
	}

	changes() {
		const upserts = [...this.dirty].map((id) => {
			const e = this.entities.get(id);
			return {
				entity_id: e.entity_id,
				entity_type: e.entity_type,
				layer: e.cad_layer,
				geometry: e.geometry,
				geometry_ref: e.geometry_ref || null,
				color: e.color || null,
				line_type: e.line_type || null,
				line_weight: e.line_weight || 0,
				metadata: e.metadata || null,
			};
		});
		const changes = { upserts, deletes: [...this.deleted], layers: [...this.dirty_layers.values()] };
		if (this.dirty_blocks.size) changes.blocks = [...this.dirty_blocks].map((name) => this.blocks.get(name)).filter(Boolean);
		if (this.references_dirty) changes.references = this.references;
		return changes;
	}

	// After save_edits: take the server's versions/boxes, forget local changes (undo history stays usable
	// as further edits on top of the saved state).
	saved(result) {
		for (const row of result.entities) {
			const e = this.entities.get(row.entity_id);
			if (!e) continue;
			e.entity_version = row.entity_version;
			e.validation_status = row.validation_status;
			[e.min_x, e.min_y, e.min_z, e.max_x, e.max_y, e.max_z] = row.bbox;
			this.server_ids.add(row.entity_id);
		}
		for (const id of result.deleted) this.server_ids.delete(id);
		this.layers = new Map(result.layers.map((l) => [l.name, l]));
		// entities drawn on a layer created in this session referred to it by its layer name
		const by_layer_name = new Map(result.layers.map((l) => [l.layer_name, l.name]));
		for (const e of this.entities.values()) {
			if (!this.layers.has(e.cad_layer) && by_layer_name.has(e.cad_layer)) e.cad_layer = by_layer_name.get(e.cad_layer);
		}
		if (result.blocks) this.blocks = new Map(result.blocks.map((b) => [b.block_name, b]));
		this.dirty.clear();
		this.deleted.clear();
		this.dirty_layers.clear();
		this.dirty_blocks.clear();
		this.references_dirty = false;
		this.emit(result.entities.map((r) => r.entity_id));
	}
}
