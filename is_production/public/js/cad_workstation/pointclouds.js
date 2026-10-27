// Point clouds in the viewport, loaded by view like the surfaces: each cloud has a tile index; a tile has all its
// points (level 0) and random subsets (levels 1, 2: 1 in 8, 1 in 64). Tiles in view are fetched at the level their
// size on screen calls for, within a budget of points; tiles out of view keep only their coarsest level. Display
// only — every edit, surface and comparison works on the stored LAZ.

import * as THREE from "three";

import { get_point_tile, get_surface_index } from "./api";
import { ramp } from "./surfaces";

const MAX_PARALLEL = 4;
const LEVEL_PIXELS = [900, 250]; // a tile wider than this on screen gets level 0 / level 1
const BUDGET = 6_000_000; // points drawn at most (finer levels are skipped past it)
// ASPRS LAS classes
export const CLASS_COLOURS = {
	0: [0.6, 0.6, 0.6],
	1: [0.75, 0.75, 0.75],
	2: [0.65, 0.45, 0.25],
	3: [0.55, 0.8, 0.4],
	4: [0.3, 0.65, 0.25],
	5: [0.1, 0.45, 0.15],
	6: [0.85, 0.3, 0.25],
	7: [1.0, 0.0, 1.0],
	9: [0.25, 0.5, 0.9],
	17: [0.9, 0.8, 0.3],
	18: [1.0, 0.4, 1.0],
};
export const CLASS_NAMES = { 0: __("Never classified"), 1: __("Unclassified"), 2: __("Ground"), 3: __("Low vegetation"), 4: __("Medium vegetation"), 5: __("High vegetation"), 6: __("Building"), 7: __("Low point (noise)"), 9: __("Water"), 17: __("Bridge deck"), 18: __("High noise") };

function diverging(t) {
	// −1 blue, 0 white, +1 red
	t = Math.max(-1, Math.min(1, t));
	return t < 0 ? [1 + t * 0.85, 1 + t * 0.6, 1] : [1, 1 - t * 0.6, 1 - t * 0.85];
}

export class PointCloudLayer {
	constructor(viewport) {
		this.viewport = viewport;
		this.group = new THREE.Group();
		this.items = new Map(); // entity id -> {entity, index, tiles: Map(key -> {level, object, pending}), group}
		this.queue = [];
		this.active = 0;
		this.model = null;
		this.mode = "elevation"; // elevation | classification | intensity | rgb | deviation
		this.size = 2;
		this.deviation_range = 1;
		this.selected = new Set();
		this.on_loaded = () => {};
	}

	clear() {
		for (const id of [...this.items.keys()]) this.remove(id);
		this.queue = [];
	}

	remove(id) {
		const item = this.items.get(id);
		if (!item) return;
		this.group.remove(item.group);
		item.group.traverse((o) => o.geometry && o.geometry.dispose());
		this.items.delete(id);
	}

	sync(entity, color, visible) {
		const id = entity.entity_id;
		const old = this.items.get(id);
		if (old && old.entity.geometry_ref === entity.geometry_ref) {
			old.entity = entity;
			old.color = color;
			old.group.visible = visible;
			this.recolor(old);
			return;
		}
		if (old) this.remove(id);
		const group = new THREE.Group();
		group.userData.entity_id = id;
		group.visible = visible;
		this.group.add(group);
		const item = { entity, color, group, tiles: new Map(), index: null };
		this.items.set(id, item);
		get_surface_index(this.model, entity.geometry_ref)
			.then((index) => {
				if (this.items.get(id) !== item) return;
				if (!index) {
					item.missing = true;
					this.on_loaded({ missing: entity });
					return;
				}
				item.index = index;
				entity._stats = index.stats; // point count, classes … in Properties
				this.on_loaded({ stats: entity });
				this.update();
			})
			.catch(() => (item.missing = true));
	}

	drawn() {
		let n = 0;
		for (const item of this.items.values()) for (const slot of item.tiles.values()) n += slot.points || 0;
		return n;
	}

	update() {
		const vp = this.viewport;
		const camera = vp.camera;
		camera.updateMatrixWorld();
		const frustum = new THREE.Frustum().setFromProjectionMatrix(new THREE.Matrix4().multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse));
		const wpp = vp.world_per_pixel();
		const o = vp.origin;
		let budget = BUDGET;
		const wanted = [];
		for (const [id, item] of this.items) {
			if (!item.index || !item.group.visible) continue;
			for (const tile of item.index.tiles) {
				const b = tile.bbox;
				const box = new THREE.Box3(new THREE.Vector3(b[0] - o[0], b[1] - o[1], b[4] - o[2]), new THREE.Vector3(b[2] - o[0], b[3] - o[1], b[5] - o[2]));
				const key = `${tile.i}_${tile.j}`;
				const coarsest = tile.levels.length - 1;
				if (!frustum.intersectsBox(box)) {
					const current = item.tiles.get(key);
					if (current && current.level !== null && current.level < coarsest) this.drop(item, key);
					if (!current) wanted.push({ id, item, key, tile, level: coarsest, pixels: 0 });
					continue;
				}
				let distance_wpp = wpp;
				if (camera.isPerspectiveCamera) {
					const d = box.getCenter(new THREE.Vector3()).distanceTo(camera.position);
					distance_wpp = (2 * d * Math.tan((camera.fov * Math.PI) / 360)) / (vp.container.clientHeight || 1);
				}
				const pixels = (b[2] - b[0]) / distance_wpp;
				const level = Math.min(pixels > LEVEL_PIXELS[0] ? 0 : pixels > LEVEL_PIXELS[1] ? 1 : 2, coarsest);
				wanted.push({ id, item, key, tile, level, pixels });
			}
		}
		// the largest tiles on screen get their detail first; past the budget, tiles fall back to coarser levels
		wanted.sort((a, b) => b.pixels - a.pixels);
		for (const w of wanted) {
			let level = w.level;
			while (level < w.tile.levels.length - 1 && w.tile.levels[level].points > budget) level++;
			budget -= w.tile.levels[level].points;
			const current = w.item.tiles.get(w.key);
			if (!current || (current.level !== level && current.pending !== level)) this.request(w, level);
		}
		this.pump();
	}

	request(w, level) {
		const current = w.item.tiles.get(w.key);
		if (current) current.pending = level;
		else w.item.tiles.set(w.key, { level: null, pending: level, object: null, points: 0 });
		this.queue = this.queue.filter((q) => !(q.item === w.item && q.key === w.key));
		this.queue.push({ ...w, level });
	}

	pump() {
		while (this.active < MAX_PARALLEL && this.queue.length) {
			const job = this.queue.shift();
			this.active++;
			get_point_tile(this.model, job.item.entity.geometry_ref, job.tile.levels[job.level].file, job.item.index.version)
				.then((tile) => this.place(job, tile))
				.catch(() => {})
				.finally(() => {
					this.active--;
					this.pump();
				});
		}
	}

	place(job, tile) {
		const { item, key, level } = job;
		if (this.items.get(job.id) !== item) return;
		const slot = item.tiles.get(key);
		if (!slot || slot.pending !== level) return;
		const geometry = new THREE.BufferGeometry();
		geometry.setAttribute("position", new THREE.BufferAttribute(tile.positions, 3));
		geometry.setAttribute("color", new THREE.BufferAttribute(new Float32Array(tile.count * 3), 3));
		geometry.userData = { tile };
		const points = new THREE.Points(geometry, new THREE.PointsMaterial({ size: this.size, sizeAttenuation: false, vertexColors: true }));
		points.position.copy(this.viewport.to_local(tile.origin));
		points.userData.z0 = tile.origin[2];
		this.paint(item, points);
		if (slot.object) {
			item.group.remove(slot.object);
			slot.object.geometry.dispose();
		}
		item.group.add(points);
		Object.assign(slot, { object: points, level, pending: null, points: tile.count });
		this.viewport.invalidate();
	}

	drop(item, key) {
		const slot = item.tiles.get(key);
		if (!slot) return;
		if (slot.object) {
			item.group.remove(slot.object);
			slot.object.geometry.dispose();
		}
		item.tiles.delete(key);
	}

	range() {
		const boxes = [...this.items.values()].map((i) => i.index && i.index.bbox).filter(Boolean);
		if (!boxes.length) return [0, 1];
		return [Math.min(...boxes.map((b) => b[2])), Math.max(...boxes.map((b) => b[5]))];
	}

	paint(item, points) {
		const g = points.geometry;
		const t = g.userData.tile;
		const col = g.attributes.color.array;
		const [zmin, zmax] = this.range();
		const span = Math.max(zmax - zmin, 1e-9);
		const ir = (item.index.stats && item.index.stats.intensity_range) || [0, 65535];
		const ispan = Math.max(ir[1] - ir[0], 1);
		const highlight = this.selected.has(item.entity.entity_id);
		const mode = this.mode === "rgb" && !t.rgb ? "elevation" : this.mode === "deviation" && !t.deviation ? "elevation" : this.mode;
		for (let i = 0; i < t.count; i++) {
			let c;
			if (mode === "classification") c = CLASS_COLOURS[t.classes[i]] || [0.9, 0.9, 0.9];
			else if (mode === "intensity") {
				const v = (t.intensity[i] - ir[0]) / ispan;
				c = [v, v, v];
			} else if (mode === "rgb") c = [t.rgb[3 * i] / 255, t.rgb[3 * i + 1] / 255, t.rgb[3 * i + 2] / 255];
			else if (mode === "deviation") c = Number.isFinite(t.deviation[i]) ? diverging(t.deviation[i] / this.deviation_range) : [0.35, 0.35, 0.35];
			else c = ramp((t.positions[3 * i + 2] + points.userData.z0 - zmin) / span);
			if (highlight) c = [c[0] * 0.5 + 0.5, c[1] * 0.5 + 0.32, c[2] * 0.5];
			col[3 * i] = c[0];
			col[3 * i + 1] = c[1];
			col[3 * i + 2] = c[2];
		}
		g.attributes.color.needsUpdate = true;
	}

	recolor(item) {
		item.group.traverse((o) => o.isPoints && this.paint(item, o));
		this.viewport.invalidate();
	}

	set_mode(mode, options = {}) {
		this.mode = mode;
		if (options.deviation_range) this.deviation_range = options.deviation_range;
		for (const item of this.items.values()) this.recolor(item);
	}

	set_size(size) {
		this.size = size;
		this.group.traverse((o) => o.isPoints && (o.material.size = size));
		this.viewport.invalidate();
	}

	set_selection(ids) {
		const next = new Set(ids);
		for (const item of this.items.values()) {
			const id = item.entity.entity_id;
			if (next.has(id) !== this.selected.has(id)) {
				this.selected = next;
				this.recolor(item);
			}
		}
		this.selected = next;
	}

	legend() {
		if (!this.items.size) return null;
		const [min, max] = this.range();
		return { mode: this.mode, min, max, deviation_range: this.deviation_range };
	}
}
