// The CAD viewport: three.js scene of a model's entities, 2D (plan) and 3D navigation, picking,
// snapping and tool previews.
//
// Precision: the scene is drawn relative to the model's local origin. Model coordinates (JS doubles)
// have the origin subtracted *before* they go into float32 buffers, so survey coordinates such as
// X = 2 500 000 keep sub-millimetre precision on screen. Picking and snapping return model coordinates
// computed from the exact geometry, never from the float32 display buffers.
//
// Performance: entities are drawn into a few large buffers ("batches", one per colour/line style), not one
// three.js object each, so drawings with tens of thousands of entities stay interactive. A batch knows which
// entity owns each segment/point/triangle (picking). Solids keep their own meshes.

import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { LineMaterial } from "three/examples/jsm/lines/LineMaterial.js";
import { LineSegments2 } from "three/examples/jsm/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/examples/jsm/lines/LineSegmentsGeometry.js";

import { dimension_graphics, hatch_graphics } from "./annotate";
import { entity_nearest, intersect, perpendicular_foot, primitives, tangent_points } from "./geom2d";
import { block_extent, block_matrix, curve_points, entity_extent, snap_points } from "./geometry";
import { PointCloudLayer } from "./pointclouds";
import { SurfaceLayer } from "./surfaces";
import { font_ready, glyph_geometry, mtext_glyphs, text_glyphs } from "./text";

const PICK_PIXELS = 6;
const HIGHLIGHT = "#ffa000";
const PREVIEW = 0x2490ef;
const XLINE_REACH = 1e5;
const DASHES = { Dashed: [12, 6], Dotted: [1.5, 5], "Dash Dot": [12, 5], Centre: [18, 5], Hidden: [6, 4], Phantom: [20, 6] };
const SNAP_PRIORITY = { endpoint: 0, node: 0, insertion: 0, midpoint: 1, centre: 1, quadrant: 1, intersection: 2, perpendicular: 3, tangent: 3, nearest: 4 };

// The area inside a closed curve as triangles (its first point's elevation).
function fill_part(pts) {
	let ring = pts.map((p) => new THREE.Vector2(p[0], p[1]));
	if (ring.length > 1 && ring[0].distanceTo(ring[ring.length - 1]) < 1e-9) ring = ring.slice(0, -1);
	const tris = { positions: [], index: [] };
	if (ring.length < 3) return { lines: [], points: [], tris, glyphs: [] };
	const z = pts[0][2] || 0;
	for (const p of ring) tris.positions.push(p.x, p.y, z);
	for (const f of THREE.ShapeUtils.triangulateShape(ring, [])) tris.index.push(f[0], f[1], f[2]);
	return { lines: [], points: [], tris, glyphs: [] };
}

export class Viewport {
	constructor(container, { on_view_change, on_mode_change } = {}) {
		this.container = container;
		this.on_view_change = on_view_change || (() => {});
		this.on_mode_change = on_mode_change || (() => {});
		this.orbit_mode = false; // left-drag orbits (for trackpads); see start_orbit
		this.origin = [0, 0, 0];
		this.elevation = 0;
		this.mode = "2d";
		this.projection = "orthographic";
		this.plan_rotated = false;
		this.show_lineweights = false;
		this.selected = new Set();
		this.mesh_cache = new Map(); // geometry_ref -> parsed mesh
		this.blocks = new Map(); // block name -> block (entities in block coordinates)
		this.batches = new Map(); // style key -> batch
		this.entity_keys = new Map(); // entity id -> Set of batch keys it is drawn in
		this.solids = new Map(); // entity id -> Object3D
		this.draw_cache = new WeakMap(); // entity object -> parts
		this.references = []; // [{name, entities, layers, blocks, offset, visible}]
		this.snap_index = new SpatialIndex();
		this.angular_decimals = 4;
		this.decimals = 3;

		this.renderer = new THREE.WebGLRenderer({ antialias: true, logarithmicDepthBuffer: true });
		this.renderer.setPixelRatio(window.devicePixelRatio || 1);
		this.renderer.domElement.className = "cad-canvas";
		container.appendChild(this.renderer.domElement);

		this.scene = new THREE.Scene();
		this.content = new THREE.Group(); // batches + solids
		this.reference_group = new THREE.Group();
		this.highlight = new THREE.Group();
		this.overlay = new THREE.Group();
		this.grid = new THREE.Group();
		this.scene.add(this.grid, this.reference_group, this.content, this.highlight);
		this.surfaces = new SurfaceLayer(this);
		this.content.add(this.surfaces.group);
		this.clouds = new PointCloudLayer(this);
		this.content.add(this.clouds.group);
		// the cursor, snap marker and previews are drawn over a cached image of the rest (see render)
		this.overlay_scene = new THREE.Scene();
		this.overlay_scene.add(this.overlay);
		this.feature_overlay = new THREE.Group(); // feature previews (extrude handle): not cleared by the drawing tools
		this.overlay_scene.add(this.feature_overlay);
		this.extrude_preview = null;
		this.blit_camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
		this.blit_material = new THREE.MeshBasicMaterial({ depthTest: false, depthWrite: false });
		this.blit_scene = new THREE.Scene();
		this.blit_scene.add(new THREE.Mesh(new THREE.PlaneGeometry(2, 2), this.blit_material));
		this.static_dirty = true;
		this.scene.add(new THREE.AmbientLight(0xffffff, 0.55));
		this.sun = new THREE.DirectionalLight(0xffffff, 1.4);
		this.sun.position.set(0.4, -0.6, 1);
		this.scene.add(this.sun);

		this.ortho = new THREE.OrthographicCamera(-1, 1, 1, -1, -1e7, 1e7);
		this.perspective = new THREE.PerspectiveCamera(45, 1, 0.1, 1e7);
		for (const camera of [this.ortho, this.perspective]) camera.up.set(0, 0, 1);
		this.camera = this.ortho;
		this.camera.position.set(0, -1e-3, 1000);

		this.controls = new OrbitControls(this.camera, this.renderer.domElement);
		this.controls.screenSpacePanning = true;
		this.controls.zoomToCursor = true;
		this.controls.enableDamping = false;
		this.controls.rotateSpeed = 0.6;
		this.controls.addEventListener("change", () => this.view_changed());
		this.controls.enableRotate = false; // rotation is ours: orbit about the point under the cursor (start_orbit)
		this.orbit_drag = null;
		this.orbit_moved_at = 0;
		this.on_orbit_down = (event) => this.start_orbit(event);
		this.renderer.domElement.addEventListener("pointerdown", this.on_orbit_down, true);

		this.raycaster = new THREE.Raycaster();
		this.axes = this.make_axes_inset();
		this.observer = new ResizeObserver(() => this.resize());
		this.observer.observe(container);
		this.set_mode("2d");
		this.resize();
		this.read_theme();
	}

	dispose() {
		this.renderer.domElement.removeEventListener("pointerdown", this.on_orbit_down, true);
		this.observer.disconnect();
		if (this.static_target) this.static_target.dispose();
		this.controls.dispose();
		this.renderer.dispose();
		this.renderer.domElement.remove();
	}

	read_theme() {
		const style = getComputedStyle(this.container);
		this.background = new THREE.Color(style.getPropertyValue("--cad-viewport-bg").trim() || "#1e2329");
		this.ink = style.getPropertyValue("--cad-viewport-ink").trim() || "#e0e0e0";
		this.grid_color = new THREE.Color(style.getPropertyValue("--cad-grid").trim() || "#2c333b");
		this.grid_major = new THREE.Color(style.getPropertyValue("--cad-grid-major").trim() || "#3a434d");
		this.scene.background = this.background;
		this.invalidate();
	}

	// ------------------------------------------------------------------ coordinates

	set_origin(origin) {
		this.origin = origin.map((v) => Number(v) || 0);
	}

	// Move the drawing origin without moving the view (camera and target shift by the same amount).
	rebase(origin) {
		const d = new THREE.Vector3(this.origin[0] - origin[0], this.origin[1] - origin[1], this.origin[2] - origin[2]);
		this.set_origin(origin);
		this.surfaces.clear(); // tiles are placed relative to the origin: they reload
		this.clouds.clear();
		this.camera.position.add(d);
		this.controls.target.add(d);
		this.controls.update();
	}

	// Centre the view on a model point (same direction and zoom): the camera target moves there.
	look_at_point(model_point) {
		const t = this.to_local(model_point);
		const d = t.clone().sub(this.controls.target);
		this.camera.position.add(d);
		this.controls.target.copy(t);
		this.controls.update();
		this.view_changed();
	}

	to_local(p) {
		return new THREE.Vector3(p[0] - this.origin[0], p[1] - this.origin[1], (p[2] || 0) - this.origin[2]);
	}

	to_model(v) {
		return [v.x + this.origin[0], v.y + this.origin[1], v.z + this.origin[2]];
	}

	world_per_pixel() {
		const height = this.container.clientHeight || 1;
		if (this.camera.isOrthographicCamera) return (this.camera.top - this.camera.bottom) / this.camera.zoom / height;
		const distance = this.camera.position.distanceTo(this.controls.target);
		return (2 * distance * Math.tan((this.camera.fov * Math.PI) / 360)) / height;
	}

	ndc(event) {
		const rect = this.renderer.domElement.getBoundingClientRect();
		return new THREE.Vector2(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
	}

	screen_of(model_point) {
		this.camera.updateMatrixWorld(); // the view may have changed since the last frame
		const v = this.to_local(model_point).project(this.camera);
		const rect = this.renderer.domElement.getBoundingClientRect();
		return [((v.x + 1) / 2) * rect.width, ((1 - v.y) / 2) * rect.height];
	}

	// The model point under the cursor on the work plane (Z = elevation).
	cursor_point(event) {
		this.raycaster.setFromCamera(this.ndc(event), this.camera);
		const plane = new THREE.Plane(new THREE.Vector3(0, 0, 1), -(this.elevation - this.origin[2]));
		const hit = new THREE.Vector3();
		if (!this.raycaster.ray.intersectPlane(plane, hit)) return null;
		return this.to_model(hit);
	}

	// ------------------------------------------------------------------ snapping

	// Snaps by priority: endpoint/node/insertion, midpoint/centre/quadrant, intersection, perpendicular/
	// tangent (from `from`), nearest; then the grid; else the cursor. `kinds` = the snaps switched on.
	snap(event, session, { kinds = null, grid_snap = false, grid = 1, from = null, aperture = 12 } = {}) {
		const raw = this.cursor_point(event);
		if (!raw) return null;
		const on = (k) => !kinds || kinds.has(k);
		const rect = this.renderer.domElement.getBoundingClientRect();
		const sx = event.clientX - rect.left, sy = event.clientY - rect.top;
		const radius = aperture * this.world_per_pixel() * (this.mode === "3d" ? 4 : 1.5);
		const candidates = [];
		const consider = (point, kind) => {
			if (!on(kind)) return;
			const [px, py] = this.screen_of(point);
			const d = Math.hypot(px - sx, py - sy);
			if (d <= aperture) candidates.push({ point: [...point], kind, d });
		};
		for (const c of this.snap_index.points_near(raw, radius)) consider(c.point, c.kind);
		const nearby = this.snap_index.entities_near(raw, radius, session, this.references_snap_entities());
		if (on("intersection") && nearby.length < 60) {
			const prims = nearby.flatMap((e) => primitives(e).prims.map((p) => ({ p, id: e.entity_id })));
			for (let i = 0; i < prims.length; i++)
				for (let j = i + 1; j < prims.length; j++) {
					if (prims[i].id === prims[j].id) continue;
					for (const x of intersect(prims[i].p, prims[j].p)) consider(x.point, "intersection");
				}
		}
		if (from && (on("perpendicular") || on("tangent")))
			for (const e of nearby)
				for (const p of primitives(e).prims) {
					if (on("perpendicular")) {
						const foot = perpendicular_foot(p, from);
						if (foot) consider(foot, "perpendicular");
					}
					if (on("tangent")) for (const t of tangent_points(p, from)) consider(t, "tangent");
				}
		if (on("nearest"))
			for (const e of nearby) {
				const n = entity_nearest(e, raw);
				if (n) consider(n.point, "nearest");
			}
		if (candidates.length) {
			candidates.sort((a, b) => SNAP_PRIORITY[a.kind] - SNAP_PRIORITY[b.kind] || a.d - b.d);
			const best = candidates[0];
			return { point: best.point, kind: best.kind };
		}
		if (grid_snap && grid > 0) return { point: [Math.round(raw[0] / grid) * grid, Math.round(raw[1] / grid) * grid, raw[2]], kind: "grid" };
		return { point: raw, kind: null };
	}

	references_snap_entities() {
		return this.reference_entities || [];
	}

	pick(event) {
		this.raycaster.setFromCamera(this.ndc(event), this.camera);
		const tolerance = PICK_PIXELS * this.world_per_pixel();
		this.raycaster.params.Line.threshold = tolerance;
		this.raycaster.params.Line2 = { threshold: PICK_PIXELS };
		this.raycaster.params.Points.threshold = tolerance;
		const hits = this.raycaster.intersectObjects(this.content.children, true);
		let surface = null; // a surface is picked only when nothing else is under the cursor (it covers lines drawn on it)
		for (const hit of hits) {
			let object = hit.object;
			if (object.userData.batch) {
				const b = object.userData.batch;
				let owners, index;
				if (object.isInstancedMesh) [owners, index] = [object.userData.instance_owner, hit.instanceId];
				else if (object.isLineSegments2) [owners, index] = [b.seg_owner, hit.faceIndex]; // (a Mesh subclass: test first)
				else if (object.isMesh) [owners, index] = [b.tri_owner, hit.faceIndex];
				else if (object.isPoints) [owners, index] = [b.point_owner, hit.index];
				else [owners, index] = [b.seg_owner, Math.floor(hit.index / 2)];
				const id = b.members_list[owners[index]];
				if (id) return id;
				continue;
			}
			while (object && !object.userData.entity_id) object = object.parent;
			if (object && object.visible) {
				if (object.parent !== this.surfaces.group) return object.userData.entity_id;
				surface = surface || object.userData.entity_id;
			}
		}
		return surface;
	}

	// Entities inside (window) or touching (crossing) a screen rectangle [x0, y0, x1, y1] in CSS pixels.
	select_in_rect(session, rect, crossing) {
		const out = [];
		const inside = (p) => p[0] >= rect[0] && p[0] <= rect[2] && p[1] >= rect[1] && p[1] <= rect[3];
		for (const entity of session.entities.values()) {
			const layer = session.layer(entity.cad_layer);
			if (layer && (!layer.visible || layer.locked)) continue;
			const box = entity_extent(entity, this.blocks);
			if (!box || !box.every(Number.isFinite)) continue;
			const corners = [];
			for (const x of [box[0], box[3]]) for (const y of [box[1], box[4]]) for (const z of [box[2], box[5]]) corners.push(this.screen_of([x, y, z]));
			const xs = corners.map((c) => c[0]), ys = corners.map((c) => c[1]);
			const sbox = [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
			if (!crossing) {
				if (corners.every(inside)) out.push(entity.entity_id);
			} else if (sbox[0] <= rect[2] && sbox[2] >= rect[0] && sbox[1] <= rect[3] && sbox[3] >= rect[1]) {
				const pts = curve_points(entity);
				if (!pts || pts.length < 2) out.push(entity.entity_id);
				else if (pts.some((p) => inside(this.screen_of(p))) || segments_cross_rect(pts.map((p) => this.screen_of(p)), rect)) out.push(entity.entity_id);
			}
		}
		return out;
	}

	// Elevation of the surfaces under the cursor (display meshes; for reading only).
	surface_at(event) {
		this.raycaster.setFromCamera(this.ndc(event), this.camera);
		return this.surfaces.z_under(this.raycaster);
	}

	// Clip everything on one side of a plane (section view); null removes it. normal/point in model coordinates.
	set_section_plane(section) {
		if (!section) this.renderer.clippingPlanes = [];
		else {
			const n = new THREE.Vector3(...section.normal).normalize();
			this.renderer.clippingPlanes = [new THREE.Plane().setFromNormalAndCoplanarPoint(n, this.to_local(section.point))];
		}
		this.section = section;
		this.invalidate();
	}

	// The plan box (model XY) under a screen rectangle (2D mode).
	model_rect(rect) {
		const a = this.cursor_point({ clientX: rect[0] + this.renderer.domElement.getBoundingClientRect().left, clientY: rect[1] + this.renderer.domElement.getBoundingClientRect().top });
		const b = this.cursor_point({ clientX: rect[2] + this.renderer.domElement.getBoundingClientRect().left, clientY: rect[3] + this.renderer.domElement.getBoundingClientRect().top });
		return a && b ? [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])] : null;
	}

	// ------------------------------------------------------------------ drawing entities

	layer_of(session, entity) {
		return session.layer(entity.cad_layer) || session.layer_by_name(entity.cad_layer);
	}

	style_of(session, entity, inherited = null) {
		const layer = this.layer_of(session, entity);
		const override = this.colour_override && this.colour_override.get(entity.entity_id); // planning view (not saved)
		const color = override || entity.color || (inherited && (!layer || layer.layer_name === "0") ? inherited.color : null) || (layer && layer.color) || this.ink;
		const line_type = entity.line_type || (layer && layer.line_type) || "Continuous";
		// an entity's own line weight is always shown; layer weights only with LWT on (as in AutoCAD)
		const own = Number(entity.line_weight) || 0;
		const weight = own || (layer && layer.line_weight) || 0.25;
		return { color, line_type, weight, own_weight: own > 0 };
	}

	// Drawable parts of an entity in model coordinates (JS doubles):
	// [{style, lines: number[] (segment pairs), points: number[], tris: {positions, index}}]
	raw_parts(entity, session, inherited = null, depth = 0) {
		const style = this.style_of(session, entity, inherited);
		const g = entity.geometry || {};
		const part = { style, lines: [], points: [], tris: { positions: [], index: [] }, glyphs: [] };
		let out = [part];
		switch (entity.entity_type) {
			case "Point":
				part.points.push(...g.p);
				break;
			case "Construction Line": {
				const a = g.ray ? g.point : g.point.map((v, k) => v - g.direction[k] * XLINE_REACH);
				part.lines.push(...a, ...g.point.map((v, k) => v + g.direction[k] * XLINE_REACH));
				break;
			}
			case "Text":
				text_glyphs(g, part.glyphs, this.plan_rotated);
				part.style = { ...style, text_height: g.height };
				break;
			case "Multiline Text":
				mtext_glyphs(g, part.glyphs, this.plan_rotated);
				part.style = { ...style, text_height: g.height };
				break;
			case "Dimension": {
				const d = dimension_graphics(g, this.decimals, this.angular_decimals, this.plan_rotated);
				for (const [p, q] of d.segments) part.lines.push(...p, ...q);
				part.tris = d.triangles;
				for (const t of d.texts) text_glyphs(t, part.glyphs);
				part.style = { ...style, line_type: "Continuous" };
				break;
			}
			case "Hatch": {
				const h = hatch_graphics(g);
				for (const [p, q] of h.segments) part.lines.push(...p, ...q);
				part.tris = h.triangles;
				part.style = { ...style, line_type: "Continuous", fill: g.pattern === "SOLID" };
				break;
			}
			case "Block Reference":
				out = this.block_parts(entity, session, style, depth);
				break;
			case "Solid":
				out = [];
				break;
			default: {
				const pts = curve_points(entity);
				if (pts) for (let i = 0; i < pts.length - 1; i++) part.lines.push(...pts[i], ...pts[i + 1]);
				const planned = this.colour_override && this.colour_override.has(entity.entity_id); // planning view: blocks filled
				if ((g.fill || planned) && pts && pts.length > 3) out.push({ ...fill_part(pts), style: { ...style, line_type: "Continuous", fill: true } }); // filled shape
			}
		}
		return out;
	}

	// The cached, compact form: origin-relative float32 arrays (rebuilt when the origin moves).
	parts(entity, session) {
		const cached = this.draw_cache.get(entity);
		if (cached) return cached;
		const o = this.origin;
		const relative = (values) => {
			const out = new Float32Array(values.length);
			for (let i = 0; i < values.length; i += 3) {
				out[i] = values[i] - o[0];
				out[i + 1] = values[i + 1] - o[1];
				out[i + 2] = values[i + 2] - o[2];
			}
			return out;
		};
		const parts = this.raw_parts(entity, session).map((p) => ({
			style: p.style,
			lines: relative(p.lines),
			points: relative(p.points),
			tris: { positions: relative(p.tris.positions), index: Uint32Array.from(p.tris.index) },
			glyphs: compact_glyphs(p.glyphs || [], o),
		}));
		this.draw_cache.set(entity, parts);
		return parts;
	}

	block_parts(entity, session, style, depth) {
		const g = entity.geometry;
		const block = this.blocks.get(g.block);
		if (!block || depth > 16) {
			// a missing block shows as a cross at its insertion point
			const p = g.insert, s = 1;
			return [{ style, lines: [p[0] - s, p[1], p[2], p[0] + s, p[1], p[2], p[0], p[1] - s, p[2], p[0], p[1] + s, p[2]], points: [], tris: { positions: [], index: [] } }];
		}
		const M = block_matrix(g, block.base_point || [0, 0, 0]);
		const move = (values) => {
			const out = new Array(values.length);
			for (let i = 0; i < values.length; i += 3) {
				const q = M([values[i], values[i + 1], values[i + 2]]);
				out[i] = q[0];
				out[i + 1] = q[1];
				out[i + 2] = q[2];
			}
			return out;
		};
		const sx = Math.abs(g.scale[0]), sy = Math.abs(g.scale[1]);
		const out = [];
		for (const child of block.entities || []) {
			const sub = { ...child, cad_layer: child.layer || "0" };
			for (const part of this.raw_parts(sub, session, style, depth + 1)) {
				const scaled = part.style.text_height ? { ...part.style, text_height: part.style.text_height * sy } : part.style;
				// text stays readable in mirrored references (as CAD programs do by default)
				const glyphs = (part.glyphs || []).map((q) => {
					const p = M([q.x, q.y, q.z]);
					return { ch: q.ch, x: p[0], y: p[1], z: p[2], rotation: q.rotation + (g.rotation || 0), sx: q.sx * sx, sy: q.sy * sy };
				});
				out.push({ style: scaled, lines: move(part.lines), points: move(part.points), tris: { positions: move(part.tris.positions), index: part.tris.index }, glyphs });
			}
		}
		return out;
	}

	style_key(style) {
		const color = new THREE.Color(style.color).getHexString();
		if (style.text_height) return `${color}|text|${Math.round(Math.log2(style.text_height) * 2)}`; // text, by size (level of detail)
		if (style.fill) return `${color}|fill`;
		const weight = this.show_lineweights || style.own_weight ? Math.round((style.weight || 0.25) * 100) : 25;
		return `${color}|${style.line_type}|${weight}`;
	}

	set_blocks(blocks) {
		this.blocks = new Map((blocks || []).map((b) => [b.block_name, b]));
		for (const b of this.blocks.values()) delete b._extent;
	}

	// Redraw the given entities (all when ids is null). Batches touched by them are rebuilt.
	sync(session, ids = null) {
		const all = ids === null;
		if (all) {
			this.draw_cache = new WeakMap();
			this.entity_keys.clear();
			this.surfaces.model = session.model && session.model.name;
			for (const id of [...this.surfaces.items.keys()]) if (!session.entities.has(id)) this.surfaces.remove(id);
			this.clouds.model = this.surfaces.model;
			for (const id of [...this.clouds.items.keys()]) if (!session.entities.has(id)) this.clouds.remove(id);
			for (const b of this.batches.values()) b.members.clear();
			this.snap_index.rebuild(session, this.blocks);
		}
		const dirty = new Set();
		const targets = all ? [...session.entities.keys()] : ids;
		for (const id of targets) {
			for (const key of this.entity_keys.get(id) || []) {
				const b = this.batches.get(key);
				if (b) {
					b.members.delete(id);
					dirty.add(key);
				}
			}
			this.entity_keys.delete(id);
			const old = this.solids.get(id);
			if (old) {
				this.content.remove(old);
				old.traverse(dispose_object);
				this.solids.delete(id);
			}
			const entity = session.entities.get(id);
			if (!all) this.snap_index.update(id, entity, this.blocks);
			if (!entity || !["Surface", "Mesh"].includes(entity.entity_type)) this.surfaces.remove(id);
			if (!entity || entity.entity_type !== "Point Cloud") this.clouds.remove(id);
			if (!entity) continue;
			const layer = this.layer_of(session, entity);
			if (entity.entity_type === "Point Cloud") {
				this.clouds.sync(entity, this.style_of(session, entity).color, !layer || Boolean(layer.visible));
				continue;
			}
			if (entity.entity_type === "Surface" || entity.entity_type === "Mesh") {
				this.surfaces.sync(entity, this.style_of(session, entity).color, !layer || Boolean(layer.visible));
				continue;
			}
			if (layer && !layer.visible) continue;
			if (entity.entity_type === "Solid") {
				const object = this.build_solid(entity, this.style_of(session, entity).color);
				object.userData.entity_id = id;
				this.solids.set(id, object);
				this.content.add(object);
				continue;
			}
			const keys = new Set();
			for (const part of this.parts(entity, session)) {
				const key = this.style_key(part.style);
				keys.add(key);
				if (!this.batches.has(key)) this.batches.set(key, { key, style: part.style, members: new Set(), objects: [] });
				this.batches.get(key).members.add(id);
				dirty.add(key);
			}
			this.entity_keys.set(id, keys);
		}
		for (const key of dirty) this.rebuild_batch(this.batches.get(key), session);
		this.rebuild_highlight(session);
		this.apply_text_lod();
		this.invalidate();
	}

	// Concatenate the members' parts into one set of buffers (typed arrays all the way).
	rebuild_batch(batch, session) {
		for (const o of batch.objects) {
			this.content.remove(o);
			dispose_object(o);
		}
		batch.objects = [];
		if (!batch.members.size) {
			this.batches.delete(batch.key);
			return;
		}
		batch.members_list = [...batch.members];
		const chosen = [];
		const glyph_lists = new Map(); // char -> {data: number[], owners: number[]}
		let nl = 0, np = 0, nt = 0, ni = 0;
		batch.members_list.forEach((id, n) => {
			const entity = session.entities.get(id);
			if (!entity) return;
			for (const part of this.parts(entity, session)) {
				if (this.style_key(part.style) !== batch.key) continue;
				chosen.push([n, part]);
				collect_glyphs(glyph_lists, part.glyphs, n);
				nl += part.lines.length;
				np += part.points.length;
				nt += part.tris.positions.length;
				ni += part.tris.index.length;
			}
		});
		const lines = new Float32Array(nl), points = new Float32Array(np), tri_pos = new Float32Array(nt), tri_index = new Uint32Array(ni);
		const seg_owner = new Uint32Array(nl / 6), point_owner = new Uint32Array(np / 3), tri_owner = new Uint32Array(ni / 3);
		let ol = 0, op = 0, ot = 0, oi = 0;
		for (const [n, part] of chosen) {
			lines.set(part.lines, ol);
			seg_owner.fill(n, ol / 6, (ol + part.lines.length) / 6);
			ol += part.lines.length;
			points.set(part.points, op);
			point_owner.fill(n, op / 3, (op + part.points.length) / 3);
			op += part.points.length;
			const base = ot / 3;
			tri_pos.set(part.tris.positions, ot);
			for (let k = 0; k < part.tris.index.length; k++) tri_index[oi + k] = part.tris.index[k] + base;
			tri_owner.fill(n, oi / 3, (oi + part.tris.index.length) / 3);
			ot += part.tris.positions.length;
			oi += part.tris.index.length;
		}
		batch.seg_owner = seg_owner;
		batch.point_owner = point_owner;
		batch.tri_owner = tri_owner;
		const color = new THREE.Color(batch.style.color);
		if (lines.length) {
			const object = this.line_object(lines, batch.style, color);
			object.userData.batch = batch;
			batch.objects.push(object);
		}
		if (points.length) {
			const geometry = new THREE.BufferGeometry();
			geometry.setAttribute("position", new THREE.BufferAttribute(points, 3));
			const object = new THREE.Points(geometry, new THREE.PointsMaterial({ color, size: 6, sizeAttenuation: false }));
			object.userData.batch = batch;
			batch.objects.push(object);
		}
		if (tri_index.length) {
			const geometry = new THREE.BufferGeometry();
			geometry.setAttribute("position", new THREE.BufferAttribute(tri_pos, 3));
			geometry.setIndex(new THREE.BufferAttribute(tri_index, 1));
			const object = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ color, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 }));
			object.userData.batch = batch;
			object.renderOrder = -0.5;
			batch.objects.push(object);
		}
		for (const object of glyph_objects(glyph_lists, new THREE.MeshBasicMaterial({ color, side: THREE.DoubleSide }))) {
			object.userData.batch = batch;
			batch.objects.push(object);
		}
		for (const object of batch.objects) this.content.add(object);
	}

	// Text smaller than about 2 pixels on screen is not drawn (it would be unreadable and costly).
	apply_text_lod() {
		const wpp = this.world_per_pixel();
		for (const batch of this.batches.values()) {
			if (!batch.style.text_height) continue;
			const visible = batch.style.text_height / wpp >= 2;
			for (const o of batch.objects) o.visible = visible;
		}
	}

	line_object(positions, style, color) {
		const dash = DASHES[style.line_type];
		const weight_px = this.show_lineweights || style.own_weight ? Math.max(1, (style.weight || 0.25) * 4) : 1;
		if (weight_px > 1.25) {
			const geometry = new LineSegmentsGeometry().setPositions(positions);
			const material = new LineMaterial({ color, linewidth: weight_px, dashed: Boolean(dash), worldUnits: false });
			material.resolution.set(this.container.clientWidth, this.container.clientHeight);
			const object = new LineSegments2(geometry, material);
			if (dash) {
				object.computeLineDistances();
				this.set_dash(material, dash);
			}
			return object;
		}
		const geometry = new THREE.BufferGeometry();
		geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
		const material = dash ? new THREE.LineDashedMaterial({ color }) : new THREE.LineBasicMaterial({ color });
		const object = new THREE.LineSegments(geometry, material);
		if (dash) {
			object.computeLineDistances();
			this.set_dash(material, dash);
		}
		return object;
	}

	set_dash(material, dash) {
		const wpp = this.world_per_pixel();
		material.dashSize = dash[0] * wpp;
		material.gapSize = dash[1] * wpp;
		if (material.isLineMaterial) material.needsUpdate = true;
	}

	rebuild_highlight(session) {
		for (const child of [...this.highlight.children]) {
			this.highlight.remove(child);
			dispose_object(child);
		}
		if (this.selected.size) {
			const parts = [];
			for (const id of this.selected) {
				const entity = session.entities.get(id);
				if (entity && entity.entity_type !== "Solid") parts.push(...this.parts(entity, session));
			}
			const concat = (key) => {
				const total = parts.reduce((s, p) => s + p[key].length, 0);
				const out = new Float32Array(total);
				let o = 0;
				for (const p of parts) {
					out.set(p[key], o);
					o += p[key].length;
				}
				return out;
			};
			const lines = concat("lines"), points = concat("points");
			const color = new THREE.Color(HIGHLIGHT);
			if (lines.length) {
				const geometry = new LineSegmentsGeometry().setPositions(lines);
				const material = new LineMaterial({ color, linewidth: 2.5, depthTest: false });
				material.resolution.set(this.container.clientWidth, this.container.clientHeight);
				const object = new LineSegments2(geometry, material);
				object.renderOrder = 5;
				this.highlight.add(object);
			}
			if (points.length) {
				const geometry = new THREE.BufferGeometry();
				geometry.setAttribute("position", new THREE.BufferAttribute(points, 3));
				this.highlight.add(new THREE.Points(geometry, new THREE.PointsMaterial({ color, size: 9, sizeAttenuation: false, depthTest: false })));
			}
			let nt = 0, ni = 0;
			for (const p of parts) {
				nt += p.tris.positions.length;
				ni += p.tris.index.length;
			}
			if (ni) {
				const pos = new Float32Array(nt), index = new Uint32Array(ni);
				let ot = 0, oi = 0;
				for (const p of parts) {
					pos.set(p.tris.positions, ot);
					for (let k = 0; k < p.tris.index.length; k++) index[oi + k] = p.tris.index[k] + ot / 3;
					ot += p.tris.positions.length;
					oi += p.tris.index.length;
				}
				const geometry = new THREE.BufferGeometry();
				geometry.setAttribute("position", new THREE.BufferAttribute(pos, 3));
				geometry.setIndex(new THREE.BufferAttribute(index, 1));
				const mesh = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ color, side: THREE.DoubleSide, depthTest: false }));
				mesh.renderOrder = 5;
				this.highlight.add(mesh);
			}
		}
		if (this.selected.size) {
			const lists = new Map();
			for (const id of this.selected) {
				const entity = session.entities.get(id);
				if (entity && entity.entity_type !== "Solid") for (const p of this.parts(entity, session)) collect_glyphs(lists, p.glyphs, 0);
			}
			for (const object of glyph_objects(lists, new THREE.MeshBasicMaterial({ color: HIGHLIGHT, side: THREE.DoubleSide, depthTest: false }))) {
				object.renderOrder = 5;
				this.highlight.add(object);
			}
		}
		for (const [id, object] of this.solids) object.traverse((o) => o.isLineSegments && o.material.color && o.material.color.set(this.selected.has(id) ? HIGHLIGHT : 0x101418));
	}

	update_visibility(session) {
		this.sync(session);
	}

	build_solid(entity, color) {
		const group = new THREE.Group();
		const mesh = this.mesh_cache.get(entity.geometry_ref);
		if (!mesh) {
			const b = entity.bbox || [entity.min_x, entity.min_y, entity.min_z, entity.max_x, entity.max_y, entity.max_z];
			if (b.every(Number.isFinite)) group.add(new THREE.Box3Helper(new THREE.Box3(this.to_local(b.slice(0, 3)), this.to_local(b.slice(3))), color));
			return group;
		}
		const geometry = new THREE.BufferGeometry();
		geometry.setAttribute("position", new THREE.BufferAttribute(mesh.positions, 3));
		geometry.setIndex(new THREE.BufferAttribute(mesh.index, 1));
		geometry.computeVertexNormals();
		const surface = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({ color, roughness: 0.75, metalness: 0.05, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 }));
		const edges_geometry = new THREE.BufferGeometry();
		edges_geometry.setAttribute("position", new THREE.BufferAttribute(mesh.edges, 3));
		const edges = new THREE.LineSegments(edges_geometry, new THREE.LineBasicMaterial({ color: this.selected.has(entity.entity_id) ? HIGHLIGHT : 0x101418 }));
		group.add(surface, edges);
		group.position.copy(this.to_local(mesh.origin));
		return group;
	}

	set_selection(ids, session) {
		this.selected = new Set(ids);
		this.surfaces.set_selection(ids);
		this.clouds.set_selection(ids);
		this.rebuild_highlight(session);
		this.invalidate();
	}

	// ------------------------------------------------------------------ external references

	// refs: [{name, offset: [x, y, z], content: {entities, layers, blocks}}]
	set_references(refs, session) {
		for (const child of [...this.reference_group.children]) {
			this.reference_group.remove(child);
			dispose_object(child);
		}
		this.reference_entities = [];
		const faded = (hex) => new THREE.Color(hex).lerp(this.background, 0.55);
		const by_color = new Map();
		const saved_blocks = this.blocks;
		for (const ref of refs) {
			const layers = new Map((ref.content.layers || []).map((l) => [l.layer_name, l]));
			const ref_session = { layer: () => null, layer_by_name: (name) => layers.get(name) };
			this.blocks = new Map((ref.content.blocks || []).map((b) => [b.block_name, b]));
			const off = ref.offset || [0, 0, 0];
			for (const e of ref.content.entities || []) {
				const layer = layers.get(e.layer);
				if (layer && !layer.visible) continue;
				const entity = { ...e, cad_layer: e.layer || "0", geometry: e.geometry };
				if (entity.entity_type === "Solid") continue;
				for (const part of this.raw_parts(entity, ref_session)) {
					const key = new THREE.Color(part.style.color).getHexString();
					if (!by_color.has(key)) by_color.set(key, { lines: [], tris: [], index: [] });
					const bucket = by_color.get(key);
					for (let i = 0; i < part.lines.length; i += 3) bucket.lines.push(part.lines[i] + off[0] - this.origin[0], part.lines[i + 1] + off[1] - this.origin[1], part.lines[i + 2] + off[2] - this.origin[2]);
				}
				this.reference_entities.push({ ...entity, entity_id: `xref:${ref.name}:${e.entity_id}`, geometry: offset_geometry(entity, off) });
			}
		}
		this.blocks = saved_blocks;
		for (const [hex, bucket] of by_color) {
			if (!bucket.lines.length) continue;
			const geometry = new THREE.BufferGeometry();
			geometry.setAttribute("position", new THREE.Float32BufferAttribute(bucket.lines, 3));
			this.reference_group.add(new THREE.LineSegments(geometry, new THREE.LineBasicMaterial({ color: faded(`#${hex}`) })));
		}
		this.snap_index.set_extra(this.reference_entities, this.blocks);
		this.invalidate();
	}

	// ------------------------------------------------------------------ previews

	clear_preview() {
		for (const child of [...this.overlay.children]) {
			this.overlay.remove(child);
			dispose_object(child);
		}
		this.render();
	}

	preview(polylines, marker = null, entities = null, session = null) {
		this.clear_preview();
		for (const points of polylines || []) {
			if (points.length < 2) continue;
			const geometry = new THREE.BufferGeometry().setFromPoints(points.map((p) => this.to_local(p)));
			const line = new THREE.Line(geometry, new THREE.LineDashedMaterial({ color: PREVIEW, dashSize: 6 * this.world_per_pixel(), gapSize: 4 * this.world_per_pixel(), depthTest: false }));
			line.computeLineDistances();
			line.renderOrder = 10;
			this.overlay.add(line);
		}
		// ghost entities (e.g. a text or dimension being placed)
		for (const entity of entities || []) {
			for (const part of this.raw_parts(entity, session || { layer: () => null, layer_by_name: () => null }, { color: "#2490ef" })) {
				const o = this.origin;
				if (part.lines.length) {
					const pos = [];
					for (let i = 0; i < part.lines.length; i += 3) pos.push(part.lines[i] - o[0], part.lines[i + 1] - o[1], part.lines[i + 2] - o[2]);
					const geometry = new THREE.BufferGeometry();
					geometry.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
					const object = new THREE.LineSegments(geometry, new THREE.LineBasicMaterial({ color: PREVIEW, depthTest: false }));
					object.renderOrder = 10;
					this.overlay.add(object);
				}
				const lists = new Map();
				collect_glyphs(lists, compact_glyphs(part.glyphs || [], o), 0);
				for (const object of glyph_objects(lists, new THREE.MeshBasicMaterial({ color: PREVIEW, side: THREE.DoubleSide, depthTest: false }))) {
					object.renderOrder = 10;
					this.overlay.add(object);
				}
				if (part.tris.index.length) {
					const pos = [];
					for (let i = 0; i < part.tris.positions.length; i += 3) pos.push(part.tris.positions[i] - o[0], part.tris.positions[i + 1] - o[1], part.tris.positions[i + 2] - o[2]);
					const geometry = new THREE.BufferGeometry();
					geometry.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
					geometry.setIndex(part.tris.index);
					const object = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ color: PREVIEW, side: THREE.DoubleSide, depthTest: false, transparent: true, opacity: 0.8 }));
					object.renderOrder = 10;
					this.overlay.add(object);
				}
			}
		}
		if (marker && marker.point) {
			const s = 7 * this.world_per_pixel();
			const c = this.to_local(marker.point);
			const shape = marker_shape(marker.kind, c, s);
			const box = new THREE.Line(new THREE.BufferGeometry().setFromPoints(shape), new THREE.LineBasicMaterial({ color: marker.kind ? 0x22c55e : PREVIEW, depthTest: false }));
			box.renderOrder = 11;
			this.overlay.add(box);
		}
		this.render();
	}

	// ------------------------------------------------------------------ navigation

	set_mode(mode) {
		this.mode = mode;
		if (mode === "2d") {
			this.use_camera(this.ortho);
			this.controls.mouseButtons = { LEFT: -1, MIDDLE: THREE.MOUSE.PAN, RIGHT: THREE.MOUSE.PAN };
			this.set_view("top");
		} else {
			this.use_camera(this.projection === "perspective" ? this.perspective : this.ortho);
			this.controls.mouseButtons = { LEFT: -1, MIDDLE: THREE.MOUSE.PAN, RIGHT: -1 }; // right-drag orbits (start_orbit)
		}
		this.view_changed();
	}

	// ------------------------------------------------------------------ orbit

	// Which drags orbit: right-drag (3D), Shift+middle-drag, Alt+left-drag, or left-drag while orbit mode is on.
	// The view turns about the point under the cursor where the drag starts (what you look at stays put).
	start_orbit(event) {
		const wants =
			(event.button === 2 && this.mode === "3d") ||
			(event.button === 1 && event.shiftKey) ||
			(event.button === 0 && (event.altKey || this.orbit_mode));
		if (!wants) return;
		event.stopImmediatePropagation(); // not a selection, pick or pan
		event.preventDefault();
		if (this.mode === "2d") {
			this.set_mode("3d");
			this.on_mode_change("3d");
		}
		const pivot = this.pivot_at(event);
		this.orbit_drag = { x: event.clientX, y: event.clientY, pivot, moved: false };
		this.show_pivot(pivot);
		const el = this.renderer.domElement;
		el.setPointerCapture(event.pointerId);
		const move = (e) => {
			const d = this.orbit_drag;
			if (!d) return;
			const dx = e.clientX - d.x, dy = e.clientY - d.y;
			d.x = e.clientX;
			d.y = e.clientY;
			if (Math.abs(dx) + Math.abs(dy) > 0) d.moved = true;
			const per_pixel = Math.PI / Math.max(this.container.clientHeight, 300); // half a turn per viewport height
			this.orbit(-dx * per_pixel, dy * per_pixel, d.pivot);
		};
		const up = (e) => {
			el.removeEventListener("pointermove", move);
			el.removeEventListener("pointerup", up);
			el.removeEventListener("pointercancel", up);
			if (el.hasPointerCapture(e.pointerId)) el.releasePointerCapture(e.pointerId);
			if (this.orbit_drag && this.orbit_drag.moved) this.orbit_moved_at = performance.now();
			this.orbit_drag = null;
			this.show_pivot(null);
		};
		el.addEventListener("pointermove", move);
		el.addEventListener("pointerup", up);
		el.addEventListener("pointercancel", up);
	}

	// A right-click that ended an orbit is not "Enter" for the drawing tools.
	orbit_just_ended() {
		return performance.now() - this.orbit_moved_at < 400;
	}

	// The point to turn about: geometry or surface under the cursor, else the view centre.
	pivot_at(event) {
		this.raycaster.setFromCamera(this.ndc(event), this.camera);
		const tolerance = 4 * this.world_per_pixel();
		this.raycaster.params.Line.threshold = tolerance;
		this.raycaster.params.Points.threshold = tolerance;
		let hits = [];
		try {
			hits = this.raycaster.intersectObjects(this.content.children, true);
		} catch (e) {
			hits = [];
		}
		const hit = hits.find((h) => h.object.visible !== false);
		return hit ? hit.point.clone() : this.controls.target.clone();
	}

	// Turn the view about pivot (local coordinates): d_azimuth about the vertical, d_elevation up/down (radians).
	orbit(d_azimuth, d_elevation, pivot = null) {
		if (this.mode === "2d") {
			this.set_mode("3d");
			this.on_mode_change("3d");
		}
		pivot = pivot || this.controls.target.clone();
		const camera = this.camera;
		const target = this.controls.target;
		const dir = camera.position.clone().sub(target).normalize(); // from the target towards the camera
		const limit = THREE.MathUtils.degToRad(89.9);
		const elevation = Math.asin(THREE.MathUtils.clamp(dir.z, -1, 1));
		// never past straight down/up; a view already steeper than the limit (plan) only tilts away from it
		let next = THREE.MathUtils.clamp(elevation + d_elevation, -limit, limit);
		if ((d_elevation > 0 && next < elevation) || (d_elevation < 0 && next > elevation) || !d_elevation) next = elevation;
		const turn = new THREE.Quaternion();
		if (Math.abs(next - elevation) > 1e-12) {
			const azimuth = Math.atan2(dir.y, dir.x);
			const to = new THREE.Vector3(Math.cos(next) * Math.cos(azimuth), Math.cos(next) * Math.sin(azimuth), Math.sin(next));
			turn.setFromUnitVectors(dir, to);
		}
		turn.premultiply(new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 0, 1), d_azimuth));
		for (const v of [camera.position, target]) v.sub(pivot).applyQuaternion(turn).add(pivot);
		camera.up.set(0, 0, 1);
		camera.lookAt(target);
		this.controls.update();
		this.view_changed();
	}

	show_pivot(pivot) {
		if (this.pivot_marker) {
			this.overlay.remove(this.pivot_marker);
			this.pivot_marker.geometry.dispose();
			this.pivot_marker = null;
		}
		if (pivot) {
			const geometry = new THREE.BufferGeometry().setAttribute("position", new THREE.Float32BufferAttribute(pivot.toArray(), 3));
			this.pivot_marker = new THREE.Points(geometry, new THREE.PointsMaterial({ color: 0xffb020, size: 9, sizeAttenuation: false, depthTest: false }));
			this.overlay.add(this.pivot_marker);
		}
		this.render();
	}

	set_projection(projection) {
		this.projection = projection;
		if (this.mode === "3d") this.use_camera(projection === "perspective" ? this.perspective : this.ortho);
	}

	use_camera(camera) {
		if (camera === this.camera) return;
		const old = this.camera;
		const span = this.world_per_pixel() * (this.container.clientHeight || 1);
		camera.position.copy(old.position);
		camera.up.set(0, 0, 1);
		if (camera.isOrthographicCamera) {
			camera.zoom = (camera.top - camera.bottom) / span;
		} else {
			const direction = old.position.clone().sub(this.controls.target).normalize();
			const distance = span / (2 * Math.tan((camera.fov * Math.PI) / 360));
			camera.position.copy(this.controls.target).addScaledVector(direction, distance);
		}
		camera.updateProjectionMatrix();
		this.camera = camera;
		this.controls.object = camera;
		this.controls.update();
		this.view_changed();
	}

	// Standard views. The plan view looks straight down with +Y up — or turned 180° for westing/southing
	// coordinates (South African Lo), so north is up (a hair of tilt keeps the orbit stable).
	set_view(name) {
		const target = this.controls.target.clone();
		const distance = Math.max(this.camera.position.distanceTo(target), 10);
		const directions = {
			top: [0, -1e-6, 1],
			bottom: [0, 1e-6, -1],
			front: [0, -1, 0],
			back: [0, 1, 0],
			left: [-1, 0, 0],
			right: [1, 0, 0],
			iso: [1, -1, 1],
			iso_sw: [-1, -1, 1],
		};
		const raw = directions[name] || directions.top;
		const flip = this.plan_rotated ? -1 : 1;
		const d = new THREE.Vector3(raw[0] * flip, raw[1] * flip, raw[2]).normalize();
		if (name !== "top" && this.mode === "2d") this.set_mode("3d");
		this.camera.position.copy(target).addScaledVector(d, distance);
		this.camera.up.set(0, 0, 1);
		this.camera.lookAt(target);
		this.controls.update();
		this.view_changed();
	}

	zoom_to(box) {
		if (!box || !box.every(Number.isFinite)) return;
		const min = this.to_local(box.slice(0, 3)), max = this.to_local(box.slice(3));
		const center = min.clone().add(max).multiplyScalar(0.5);
		const size = max.clone().sub(min);
		const radius = Math.max(size.length() / 2, 1);
		const direction = this.camera.position.clone().sub(this.controls.target);
		if (direction.lengthSq() === 0) direction.set(0, -1e-6, 1);
		direction.normalize();
		this.controls.target.copy(center);
		const w = this.container.clientWidth || 1, h = this.container.clientHeight || 1;
		if (this.camera.isOrthographicCamera) {
			const span_x = Math.max(size.x, 1e-6), span_y = Math.max(size.y, 1e-6);
			const fit = this.mode === "2d" ? Math.min(w / span_x, h / span_y) : Math.min(w, h) / (2 * radius);
			this.camera.zoom = (fit * 0.85 * (this.camera.top - this.camera.bottom)) / h;
			this.camera.position.copy(center).addScaledVector(direction, Math.max(radius * 4, 100));
		} else {
			const distance = radius / Math.sin((this.camera.fov * Math.PI) / 360);
			this.camera.position.copy(center).addScaledVector(direction, distance * 1.1);
		}
		this.camera.updateProjectionMatrix();
		this.controls.update();
		this.view_changed();
	}

	resize() {
		const w = this.container.clientWidth || 1, h = this.container.clientHeight || 1;
		this.renderer.setSize(w, h, false);
		const span = this.ortho.top - this.ortho.bottom > 2 ? this.world_per_pixel() * h : null;
		this.ortho.left = -w / 2;
		this.ortho.right = w / 2;
		this.ortho.top = h / 2;
		this.ortho.bottom = -h / 2;
		if (span) this.ortho.zoom = h / span;
		this.ortho.updateProjectionMatrix();
		this.perspective.aspect = w / h;
		this.perspective.updateProjectionMatrix();
		this.scene.traverse((o) => o.material && o.material.isLineMaterial && o.material.resolution.set(w, h));
		this.view_changed();
	}

	view_changed() {
		this.camera.updateMatrixWorld(); // picks and projections right after a view change use the new view
		if (this.camera.isPerspectiveCamera) {
			const distance = this.camera.position.distanceTo(this.controls.target);
			this.camera.near = Math.max(distance / 1e4, 1e-3);
			this.camera.far = distance * 1e4;
			this.camera.updateProjectionMatrix();
		}
		for (const batch of this.batches.values()) {
			const dash = DASHES[batch.style.line_type];
			if (dash) for (const o of batch.objects) if (o.material.dashSize !== undefined) this.set_dash(o.material, dash);
		}
		this.update_grid();
		this.apply_text_lod();
		this.invalidate();
		clearTimeout(this.surface_timer);
		this.surface_timer = setTimeout(() => {
			this.surfaces.update(); // load the surface and point-cloud tiles the view needs
			this.clouds.update();
		}, 150);
		if (this.extrude_preview) this.extrude_preview.refresh(); // the handle keeps its size on screen
		this.on_view_change(this);
	}

	view_box() {
		if (this.mode !== "2d") return null;
		const wpp = this.world_per_pixel();
		const w = this.container.clientWidth, h = this.container.clientHeight;
		const t = this.to_model(this.controls.target);
		return [t[0] - (w / 2) * wpp, t[1] - (h / 2) * wpp, t[0] + (w / 2) * wpp, t[1] + (h / 2) * wpp];
	}

	grid_spacing() {
		const raw = this.world_per_pixel() * 24;
		const step = Math.pow(10, Math.ceil(Math.log10(raw)));
		return raw * 2 < step ? step / 2 : step;
	}

	update_grid() {
		if (!this.grid || !this.show_grid_lines) {
			if (this.grid) this.grid.clear();
			return;
		}
		this.grid.clear();
		const spacing = this.grid_spacing();
		const t = this.to_model(this.controls.target);
		const reach = Math.max(this.container.clientWidth, this.container.clientHeight) * this.world_per_pixel() * (this.mode === "2d" ? 0.75 : 1.5);
		const x0 = Math.floor((t[0] - reach) / spacing) * spacing, x1 = Math.ceil((t[0] + reach) / spacing) * spacing;
		const y0 = Math.floor((t[1] - reach) / spacing) * spacing, y1 = Math.ceil((t[1] + reach) / spacing) * spacing;
		const minor = [], major = [];
		const z = this.elevation - this.origin[2];
		for (let x = x0; x <= x1 + spacing / 2; x += spacing) {
			const target = Math.abs(Math.round(x / spacing) % 10) === 0 ? major : minor;
			target.push(x - this.origin[0], y0 - this.origin[1], z, x - this.origin[0], y1 - this.origin[1], z);
		}
		for (let y = y0; y <= y1 + spacing / 2; y += spacing) {
			const target = Math.abs(Math.round(y / spacing) % 10) === 0 ? major : minor;
			target.push(x0 - this.origin[0], y - this.origin[1], z, x1 - this.origin[0], y - this.origin[1], z);
		}
		for (const [positions, color] of [[minor, this.grid_color], [major, this.grid_major]]) {
			const geometry = new THREE.BufferGeometry();
			geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
			const lines = new THREE.LineSegments(geometry, new THREE.LineBasicMaterial({ color, depthWrite: false }));
			lines.renderOrder = -1;
			this.grid.add(lines);
		}
		this.grid_step = spacing;
	}

	set_grid_visible(visible) {
		this.show_grid_lines = visible;
		this.update_grid();
		this.invalidate();
	}

	make_axes_inset() {
		const scene = new THREE.Scene();
		scene.add(new THREE.AxesHelper(1));
		const camera = new THREE.OrthographicCamera(-1.4, 1.4, 1.4, -1.4, -10, 10);
		camera.up.set(0, 0, 1);
		return { scene, camera };
	}

	// The scene changed (content, view, selection): the cached image must be redrawn.
	invalidate() {
		this.static_dirty = true;
		this.render();
	}

	// Draw a frame. The model (batches, solids, grid, highlight) is rendered into an off-screen image only
	// when it changed; pointer moves just copy that image and draw the overlay (cursor, snaps, previews)
	// on top, so moving the mouse stays cheap however large the drawing is.
	render() {
		if (this.frame) return;
		this.frame = requestAnimationFrame(() => {
			this.frame = null;
			const r = this.renderer;
			const w = this.container.clientWidth, h = this.container.clientHeight;
			const dpr = r.getPixelRatio();
			const W = Math.max(1, Math.floor(w * dpr)), H = Math.max(1, Math.floor(h * dpr));
			if (!this.static_target || this.static_target.width !== W || this.static_target.height !== H) {
				if (this.static_target) this.static_target.dispose();
				this.static_target = new THREE.WebGLRenderTarget(W, H, { samples: 4 });
				this.static_dirty = true;
			}
			// fat lines (LineSegments2) take their pixel size from the renderer's viewport: it must be the whole view here,
			// not the axes inset the previous frame ended with (that made highlighted lines ~14× too wide)
			r.setViewport(0, 0, w, h);
			if (this.static_dirty) {
				r.setRenderTarget(this.static_target);
				r.render(this.scene, this.camera);
				r.setRenderTarget(null);
				this.static_dirty = false;
			}
			r.setViewport(0, 0, w, h);
			r.setScissorTest(false);
			this.blit_material.map = this.static_target.texture;
			r.autoClear = true;
			r.render(this.blit_scene, this.blit_camera);
			r.autoClear = false;
			r.render(this.overlay_scene, this.camera);
			const size = 76;
			const direction = this.camera.position.clone().sub(this.controls.target).normalize();
			this.axes.camera.position.copy(direction.multiplyScalar(3));
			this.axes.camera.up.copy(this.camera.up);
			this.axes.camera.lookAt(0, 0, 0);
			r.setScissorTest(true);
			r.setScissor(8, 8, size, size);
			r.setViewport(8, 8, size, size);
			r.clearDepth();
			r.render(this.axes.scene, this.axes.camera);
			r.setScissorTest(false);
			r.setViewport(0, 0, w, h);
			r.autoClear = true;
		});
	}

	text_ready() {
		return font_ready();
	}
}

// ---------------------------------------------------------------------------------------------- helpers

// Free GPU buffers; an instanced glyph keeps its geometry, which every occurrence of the character shares.
function dispose_object(o) {
	if (o.isInstancedMesh) o.dispose();
	else if (o.geometry) o.geometry.dispose();
}

// glyph instances → {chars: string[], data: Float32Array [x, y, z, rotation, sx, sy] relative to origin}
function compact_glyphs(glyphs, o) {
	const data = new Float32Array(glyphs.length * 6);
	glyphs.forEach((q, i) => data.set([q.x - o[0], q.y - o[1], q.z - o[2], q.rotation, q.sx, q.sy], i * 6));
	return { chars: glyphs.map((q) => q.ch), data };
}

function collect_glyphs(lists, glyphs, owner) {
	if (!glyphs || !glyphs.chars.length) return;
	glyphs.chars.forEach((ch, i) => {
		let list = lists.get(ch);
		if (!list) lists.set(ch, (list = { data: [], owners: [] }));
		for (let k = 0; k < 6; k++) list.data.push(glyphs.data[i * 6 + k]);
		list.owners.push(owner);
	});
}

const _matrix = new THREE.Matrix4(), _q = new THREE.Quaternion(), _p = new THREE.Vector3(), _s = new THREE.Vector3(), _z = new THREE.Vector3(0, 0, 1);

// One InstancedMesh per character: every occurrence shares the glyph's triangles.
function glyph_objects(lists, material) {
	const out = [];
	for (const [ch, list] of lists) {
		const count = list.owners.length;
		const mesh = new THREE.InstancedMesh(glyph_geometry(ch), material, count);
		for (let i = 0; i < count; i++) {
			const d = list.data;
			_p.set(d[i * 6], d[i * 6 + 1], d[i * 6 + 2]);
			_q.setFromAxisAngle(_z, d[i * 6 + 3]);
			_s.set(d[i * 6 + 4], d[i * 6 + 5], 1);
			mesh.setMatrixAt(i, _matrix.compose(_p, _q, _s));
		}
		mesh.instanceMatrix.needsUpdate = true;
		mesh.computeBoundingSphere();
		mesh.userData.instance_owner = Uint32Array.from(list.owners);
		mesh.renderOrder = -0.25;
		out.push(mesh);
	}
	return out;
}

function offset_geometry(entity, off) {
	if (!off || (!off[0] && !off[1] && !off[2])) return entity.geometry;
	const g = JSON.parse(JSON.stringify(entity.geometry));
	const move = (p) => [p[0] + off[0], p[1] + off[1], (p[2] || 0) + off[2]];
	for (const key of ["p", "p0", "p1", "center", "position", "insert", "point", "location"]) if (g[key]) g[key] = move(g[key]);
	if (g.points) g.points = g.points.map(move);
	if (g.control_points) g.control_points = g.control_points.map(move);
	if (g.loops) g.loops = g.loops.map((l) => ({ ...l, points: l.points.map(move) }));
	return g;
}

function marker_shape(kind, c, s) {
	const V = (x, y) => new THREE.Vector3(c.x + x * s, c.y + y * s, c.z);
	switch (kind) {
		case "midpoint":
			return [V(-1, -1), V(1, -1), V(0, 1), V(-1, -1)];
		case "centre":
		case "quadrant": {
			const pts = [];
			for (let i = 0; i <= 16; i++) pts.push(V(Math.cos((i / 16) * Math.PI * 2), Math.sin((i / 16) * Math.PI * 2)));
			return pts;
		}
		case "intersection":
			return [V(-1, -1), V(1, 1), V(0, 0), V(-1, 1), V(1, -1)];
		case "perpendicular":
			return [V(-1, 1), V(-1, -1), V(1, -1), V(-1, -1), V(-1, 0), V(0, 0), V(0, -1)];
		case "tangent": {
			const pts = [V(-1, 1), V(1, 1)];
			for (let i = 0; i <= 16; i++) pts.push(V(0.7 * Math.cos((i / 16) * Math.PI * 2), 0.3 + 0.7 * Math.sin((i / 16) * Math.PI * 2)));
			return pts;
		}
		case "nearest":
			return [V(-1, -1), V(1, 1), V(-1, 1), V(1, -1), V(-1, -1)];
		default:
			return [V(-1, -1), V(1, -1), V(1, 1), V(-1, 1), V(-1, -1)];
	}
}

function segments_cross_rect(pts, r) {
	const edges = [
		[[r[0], r[1]], [r[2], r[1]]],
		[[r[2], r[1]], [r[2], r[3]]],
		[[r[2], r[3]], [r[0], r[3]]],
		[[r[0], r[3]], [r[0], r[1]]],
	];
	for (let i = 0; i < pts.length - 1; i++)
		for (const [a, b] of edges) if (segments_meet(pts[i], pts[i + 1], a, b)) return true;
	return false;
}

function segments_meet(p, q, a, b) {
	const d = (x, y, z) => (z[0] - x[0]) * (y[1] - x[1]) - (z[1] - x[1]) * (y[0] - x[0]);
	const d1 = d(a, b, p), d2 = d(a, b, q), d3 = d(p, q, a), d4 = d(p, q, b);
	return d1 * d2 <= 0 && d3 * d4 <= 0;
}

// A uniform grid over model XY: snap points (packed numbers, not objects) and, per cell, the entities that
// pass through it — long curves by their segments, so a large contour only matters near the cursor.
const KINDS = ["endpoint", "midpoint", "centre", "quadrant", "node", "insertion"];
const SEGMENT_TYPES = new Set(["Line", "Polyline", "Arc", "Circle", "Ellipse", "Spline"]);

class SpatialIndex {
	constructor() {
		this.cell = 10;
		this.points = new Map(); // cell key -> [x, y, z, kind, ref, …]
		this.boxes = new Map(); // cell key -> Set(id)
		this.big = new Set(); // entities spanning too many cells (construction lines)
		this.entries = new Map(); // id -> {ref, point_cells, box_cells}
		this.refs = []; // ref -> id
		this.extra = [];
		this.extra_points = [];
	}

	key(ix, iy) {
		return `${ix},${iy}`;
	}

	rebuild(session, blocks) {
		this.points.clear();
		this.boxes.clear();
		this.big.clear();
		this.entries.clear();
		this.refs = [];
		let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
		for (const e of session.entities.values()) {
			if (e.entity_type === "Construction Line") continue;
			const b = entity_extent(e, blocks);
			if (!b || !b.every(Number.isFinite)) continue;
			x0 = Math.min(x0, b[0]);
			y0 = Math.min(y0, b[1]);
			x1 = Math.max(x1, b[3]);
			y1 = Math.max(y1, b[4]);
		}
		if (Number.isFinite(x0)) this.cell = Math.max(Math.max(x1 - x0, y1 - y0, 1) / 512, 0.01);
		for (const [id, e] of session.entities) this.update(id, e, blocks);
	}

	add_point(entry, p, kind) {
		const k = this.key(Math.floor(p[0] / this.cell), Math.floor(p[1] / this.cell));
		let bucket = this.points.get(k);
		if (!bucket) this.points.set(k, (bucket = []));
		bucket.push(p[0], p[1], p[2] || 0, KINDS.indexOf(kind), entry.ref);
		entry.point_cells.add(k);
	}

	add_box(entry, id, x0, y0, x1, y1, limit) {
		const a = Math.floor(x0 / this.cell), b = Math.floor(x1 / this.cell), c = Math.floor(y0 / this.cell), d = Math.floor(y1 / this.cell);
		if ((b - a + 1) * (d - c + 1) > limit) return false;
		for (let ix = a; ix <= b; ix++)
			for (let iy = c; iy <= d; iy++) {
				const k = this.key(ix, iy);
				let set = this.boxes.get(k);
				if (!set) this.boxes.set(k, (set = new Set()));
				set.add(id);
				entry.box_cells.add(k);
			}
		return true;
	}

	update(id, entity, blocks) {
		const old = this.entries.get(id);
		if (old) {
			for (const k of old.point_cells) {
				const bucket = this.points.get(k);
				if (!bucket) continue;
				const kept = [];
				for (let i = 0; i < bucket.length; i += 5) if (bucket[i + 4] !== old.ref) kept.push(bucket[i], bucket[i + 1], bucket[i + 2], bucket[i + 3], bucket[i + 4]);
				this.points.set(k, kept);
			}
			for (const k of old.box_cells) this.boxes.get(k) && this.boxes.get(k).delete(id);
			this.big.delete(id);
			this.entries.delete(id);
		}
		if (!entity) return;
		const entry = { ref: old ? old.ref : this.refs.length, point_cells: new Set(), box_cells: new Set() };
		if (!old) this.refs.push(id);
		for (const c of snap_points(entity)) if (KINDS.includes(c.kind)) this.add_point(entry, c.point, c.kind);
		if (entity.entity_type === "Block Reference") {
			const block = blocks && blocks.get(entity.geometry.block);
			if (block) {
				const M = block_matrix(entity.geometry, block.base_point || [0, 0, 0]);
				for (const child of block.entities || []) for (const c of snap_points(child)) if (KINDS.includes(c.kind)) this.add_point(entry, M(c.point), c.kind);
			}
		}
		if (entity.entity_type === "Construction Line") this.big.add(id);
		else if (SEGMENT_TYPES.has(entity.entity_type)) {
			const pts = curve_points(entity) || [];
			for (let i = 0; i < pts.length - 1; i++) {
				const p = pts[i], q = pts[i + 1];
				if (!this.add_box(entry, id, Math.min(p[0], q[0]), Math.min(p[1], q[1]), Math.max(p[0], q[0]), Math.max(p[1], q[1]), 256)) this.big.add(id);
			}
		} else {
			const box = entity_extent(entity, blocks);
			if (box && box.every(Number.isFinite) && !this.add_box(entry, id, box[0], box[1], box[3], box[4], 4096)) this.big.add(id);
		}
		this.entries.set(id, entry);
	}

	set_extra(entities) {
		this.extra = entities;
		this.extra_points = entities.flatMap((e) => snap_points(e));
	}

	cells(p, r) {
		const out = [];
		const x0 = Math.floor((p[0] - r) / this.cell), x1 = Math.floor((p[0] + r) / this.cell), y0 = Math.floor((p[1] - r) / this.cell), y1 = Math.floor((p[1] + r) / this.cell);
		if ((x1 - x0 + 1) * (y1 - y0 + 1) > 900) return null;
		for (let ix = x0; ix <= x1; ix++) for (let iy = y0; iy <= y1; iy++) out.push(this.key(ix, iy));
		return out;
	}

	points_near(p, r) {
		const out = [];
		const cells = this.cells(p, r);
		if (cells)
			for (const k of cells) {
				const bucket = this.points.get(k);
				if (!bucket) continue;
				for (let i = 0; i < bucket.length; i += 5)
					if (Math.abs(bucket[i] - p[0]) <= r && Math.abs(bucket[i + 1] - p[1]) <= r) out.push({ point: [bucket[i], bucket[i + 1], bucket[i + 2]], kind: KINDS[bucket[i + 3]] });
			}
		for (const c of this.extra_points) if (Math.hypot(c.point[0] - p[0], c.point[1] - p[1]) <= r) out.push(c);
		return out;
	}

	entities_near(p, r, session, extra = []) {
		const ids = new Set(this.big);
		const cells = this.cells(p, r);
		if (cells) for (const k of cells) for (const id of this.boxes.get(k) || []) ids.add(id);
		const out = [];
		for (const id of ids) {
			const e = session.entities.get(id);
			if (e) out.push(e);
		}
		for (const e of extra) {
			const pts = curve_points(e);
			if (pts && pts.some((q) => Math.hypot(q[0] - p[0], q[1] - p[1]) < r * 20)) out.push(e);
		}
		return out;
	}
}

export { block_extent };
