// Surfaces (TINs) and meshes in the viewport, loaded by view: each surface has a tile index; a tile has its full
// triangles (level 0) and coarser resampled grids (levels 1, 2). Tiles in view are fetched at the level their
// size on screen calls for; tiles out of view are dropped from memory. Display only — volumes, profiles and
// contours always use the authoritative TIN on the server.

import * as THREE from "three";

import { get_surface_index, get_surface_tile } from "./api";

const MAX_PARALLEL = 6;
const LEVEL_PIXELS = [700, 180]; // a tile wider than this on screen needs level 0 / level 1
const RAMP = [
	[0.0, [0.16, 0.29, 0.65]],
	[0.25, [0.18, 0.62, 0.62]],
	[0.5, [0.38, 0.7, 0.27]],
	[0.75, [0.85, 0.72, 0.3]],
	[1.0, [0.62, 0.36, 0.22]],
];

export function ramp(t) {
	t = Math.min(1, Math.max(0, t));
	for (let i = 1; i < RAMP.length; i++) {
		if (t <= RAMP[i][0]) {
			const [t0, a] = RAMP[i - 1], [t1, b] = RAMP[i];
			const u = (t - t0) / (t1 - t0);
			return [0, 1, 2].map((k) => a[k] + (b[k] - a[k]) * u);
		}
	}
	return RAMP[RAMP.length - 1][1];
}

function slope_color(deg) {
	return ramp(Math.min(deg / 60, 1));
}

export class SurfaceLayer {
	constructor(viewport) {
		this.viewport = viewport;
		this.group = new THREE.Group();
		this.items = new Map(); // entity id -> {entity, index, tiles: Map(key -> {level, object}), group}
		this.queue = [];
		this.active = 0;
		this.model = null;
		this.mode = "elevation"; // elevation | slope | layer
		this.wireframe = false;
		this.opacity = 1;
		this.selected = new Set();
		this.on_loaded = () => {};
	}

	set_model(model) {
		this.clear();
		this.model = model;
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
		if (item.index) this.refresh_range();
	}

	// (Re)attach a Surface/Mesh entity; its index is fetched once per geometry_ref.
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
				entity._stats = index.stats; // shown in Properties and the Surfaces panel
				this.on_loaded({ stats: entity });
				this.refresh_range();
				this.update();
			})
			.catch(() => (item.missing = true));
	}

	// Which tiles at which level, for the current view.
	update() {
		const vp = this.viewport;
		const camera = vp.camera;
		camera.updateMatrixWorld();
		const frustum = new THREE.Frustum().setFromProjectionMatrix(new THREE.Matrix4().multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse));
		const wpp = vp.world_per_pixel();
		const o = vp.origin;
		for (const [id, item] of this.items) {
			if (!item.index || !item.group.visible) continue;
			for (const tile of item.index.tiles) {
				const b = tile.bbox;
				const box = new THREE.Box3(new THREE.Vector3(b[0] - o[0], b[1] - o[1], b[4] - o[2]), new THREE.Vector3(b[2] - o[0], b[3] - o[1], b[5] - o[2]));
				const key = `${tile.i}_${tile.j}`;
				const current = item.tiles.get(key);
				if (!frustum.intersectsBox(box)) {
					if (current && current.level < tile.levels.length - 1) this.drop(item, key); // keep only the coarsest out of view
					continue;
				}
				let distance_wpp = wpp;
				if (camera.isPerspectiveCamera) {
					const centre = box.getCenter(new THREE.Vector3());
					const d = centre.distanceTo(camera.position);
					distance_wpp = (2 * d * Math.tan((camera.fov * Math.PI) / 360)) / (vp.container.clientHeight || 1);
				}
				const pixels = (b[2] - b[0]) / distance_wpp;
				let level = pixels > LEVEL_PIXELS[0] ? 0 : pixels > LEVEL_PIXELS[1] ? 1 : 2;
				level = Math.min(level, tile.levels.length - 1);
				if (!current || current.level !== level) this.request(id, item, key, tile, level);
			}
		}
		this.pump();
	}

	request(id, item, key, tile, level) {
		const wanted = `${id}|${key}|${level}`;
		if (this.queue.some((q) => q.wanted === wanted) || item.tiles.get(key)?.pending === level) return;
		const current = item.tiles.get(key);
		if (current) current.pending = level;
		else item.tiles.set(key, { level: null, pending: level, object: null });
		this.queue.push({ wanted, id, item, key, tile, level });
	}

	pump() {
		while (this.active < MAX_PARALLEL && this.queue.length) {
			const job = this.queue.shift();
			this.active++;
			get_surface_tile(this.model, job.item.entity.geometry_ref, job.tile.levels[job.level].file)
				.then((mesh) => this.place(job, mesh))
				.catch(() => {})
				.finally(() => {
					this.active--;
					this.pump();
				});
		}
	}

	place(job, mesh) {
		const { item, key, level } = job;
		if (this.items.get(job.id) !== item) return;
		const slot = item.tiles.get(key);
		if (!slot || slot.pending !== level) return;
		const object = this.build(item, mesh, level);
		if (slot.object) {
			item.group.remove(slot.object);
			slot.object.traverse((o) => o.geometry && o.geometry.dispose());
		}
		item.group.add(object);
		slot.object = object;
		slot.level = level;
		slot.pending = null;
		this.viewport.invalidate();
	}

	drop(item, key) {
		const slot = item.tiles.get(key);
		if (!slot) return;
		if (slot.object) {
			item.group.remove(slot.object);
			slot.object.traverse((o) => o.geometry && o.geometry.dispose());
		}
		item.tiles.delete(key);
	}

	build(item, mesh, level) {
		const geometry = new THREE.BufferGeometry();
		geometry.setAttribute("position", new THREE.BufferAttribute(mesh.positions, 3));
		geometry.setIndex(new THREE.BufferAttribute(mesh.index, 1));
		geometry.computeVertexNormals();
		const colors = new Float32Array(mesh.positions.length);
		geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
		const material = new THREE.MeshStandardMaterial({
			vertexColors: true,
			roughness: 0.9,
			metalness: 0,
			side: THREE.DoubleSide,
			polygonOffset: true,
			polygonOffsetFactor: 2,
			polygonOffsetUnits: 2,
			transparent: this.opacity < 1,
			opacity: this.opacity,
		});
		const surface = new THREE.Mesh(geometry, material);
		surface.userData.z0 = mesh.origin[2];
		const holder = new THREE.Group();
		holder.position.copy(this.viewport.to_local(mesh.origin));
		holder.add(surface);
		holder.userData.level = level;
		if (this.wireframe && level === 0) holder.add(new THREE.LineSegments(new THREE.WireframeGeometry(geometry), new THREE.LineBasicMaterial({ color: 0x0d1117, transparent: true, opacity: 0.35 })));
		this.paint(item, surface);
		return holder;
	}

	paint(item, surface) {
		const g = surface.geometry;
		const pos = g.attributes.position.array, col = g.attributes.color.array, nor = g.attributes.normal.array;
		// one elevation scale for every surface (the legend's), so equal colours mean equal elevations
		const [zmin, zmax] = this.range || [item.index.bbox[2], item.index.bbox[5]];
		const span = Math.max(zmax - zmin, 1e-9);
		const flat = new THREE.Color(item.color);
		const highlight = this.selected.has(item.entity.entity_id);
		for (let i = 0; i < pos.length; i += 3) {
			let c;
			if (this.mode === "slope") c = slope_color((Math.acos(Math.min(1, Math.abs(nor[i + 2]))) * 180) / Math.PI);
			else if (this.mode === "layer") c = [flat.r, flat.g, flat.b];
			else c = ramp((pos[i + 2] + surface.userData.z0 - zmin) / span);
			if (highlight) c = [c[0] * 0.5 + 0.5, c[1] * 0.5 + 0.32, c[2] * 0.5];
			col[i] = c[0];
			col[i + 1] = c[1];
			col[i + 2] = c[2];
		}
		g.attributes.color.needsUpdate = true;
	}

	// The elevation range of all loaded surfaces; when it changes every surface is repainted.
	refresh_range() {
		const legend = this.legend();
		const next = legend ? [legend.min, legend.max] : null;
		if (next && this.range && next[0] === this.range[0] && next[1] === this.range[1]) return;
		this.range = next;
		for (const item of this.items.values()) if (item.index) this.recolor(item);
	}

	recolor(item) {
		item.group.traverse((o) => o.isMesh && this.paint(item, o));
		this.viewport.invalidate();
	}

	set_mode(mode) {
		this.mode = mode;
		for (const item of this.items.values()) this.recolor(item);
	}

	set_wireframe(on) {
		this.wireframe = on;
		for (const item of this.items.values()) for (const key of [...item.tiles.keys()]) this.drop(item, key);
		this.update();
	}

	set_selection(ids) {
		this.selected = new Set(ids);
		for (const item of this.items.values()) this.recolor(item);
	}

	// Elevation of the surfaces under a screen point (display mesh: for reading, not for calculation).
	z_under(raycaster) {
		const hits = raycaster.intersectObjects(this.group.children, true).filter((h) => h.object.isMesh);
		if (!hits.length) return null;
		const h = hits[0];
		let o = h.object;
		while (o && !o.userData.entity_id) o = o.parent;
		return { point: this.viewport.to_model(h.point), entity_id: o && o.userData.entity_id };
	}

	legend() {
		const ranges = [...this.items.values()].map((i) => i.index && i.index.stats).filter(Boolean);
		if (!ranges.length) return null;
		return { mode: this.mode, min: Math.min(...ranges.map((s) => s.min_elevation)), max: Math.max(...ranges.map((s) => s.max_elevation)) };
	}
}
