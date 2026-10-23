// The live preview of an extrusion in the viewport: a translucent prism over the profile(s) and an arrow handle that
// is dragged up or down to set the distance (as in Fusion's Extrude). Display only: the solid itself is made by
// Open CASCADE in the CAD worker when the extrusion is confirmed.

import * as THREE from "three";

import { polyline_points } from "./geometry";

const HANDLE_PIXELS = 70; // arrow length on screen

// A ring offset inwards by `inset` (mitred corners; a preview of the worker's exact offset).
function inset_ring(ring, inset) {
	if (!inset) return ring;
	const n = ring.length;
	return ring.map((p, i) => {
		const a = ring[(i - 1 + n) % n], b = ring[(i + 1) % n];
		const d1 = new THREE.Vector2(p.x - a.x, p.y - a.y).normalize();
		const d2 = new THREE.Vector2(b.x - p.x, b.y - p.y).normalize();
		const n1 = new THREE.Vector2(-d1.y, d1.x), n2 = new THREE.Vector2(-d2.y, d2.x); // left = inside (counter-clockwise)
		const bis = n1.clone().add(n2);
		const len = bis.length();
		if (len < 1e-9) return new THREE.Vector2(p.x + n1.x * inset, p.y + n1.y * inset);
		bis.divideScalar(len);
		const k = inset / Math.max(bis.dot(n1), 0.2); // mitre, limited at very sharp corners
		return new THREE.Vector2(p.x + bis.x * k, p.y + bis.y * k);
	});
}

export class ExtrudePreview {
	// profiles: [{points, bulges}] in model coordinates (horizontal); on_change(distance) while the handle is dragged
	constructor(viewport, profiles, on_change) {
		this.viewport = viewport;
		this.on_change = on_change;
		this.group = new THREE.Group();
		this.viewport.feature_overlay.add(this.group);
		this.distance = 10;
		this.symmetric = false;
		this.taper = 0;
		this.rings = profiles.map((p) => {
			let pts = polyline_points(p.points, p.bulges, true);
			if (pts.length > 1 && Math.hypot(pts[0][0] - pts[pts.length - 1][0], pts[0][1] - pts[pts.length - 1][1]) < 1e-9) pts = pts.slice(0, -1);
			const local = pts.map((q) => viewport.to_local(q));
			const ring = local.map((v) => new THREE.Vector2(v.x, v.y));
			if (THREE.ShapeUtils.isClockWise(ring)) ring.reverse();
			return { ring, z: local[0].z };
		});
		const all = this.rings.flatMap((r) => r.ring);
		this.centre = all.reduce((c, p) => c.add(p), new THREE.Vector2()).divideScalar(all.length);
		this.base_z = this.rings[0].z;
		this.material = new THREE.MeshBasicMaterial({ color: 0x4aa8d8, transparent: true, opacity: 0.35, depthTest: false, side: THREE.DoubleSide });
		this.edge_material = new THREE.LineBasicMaterial({ color: 0x9fd3f0, depthTest: false });
		this.handle_material = new THREE.MeshBasicMaterial({ color: 0xffb020, depthTest: false });
		this.grab_material = new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthTest: false, depthWrite: false });
		this.handle = new THREE.Group();
		this.handle.renderOrder = 10;
		this.group.add(this.handle);
		this.on_down = (e) => this.pointer_down(e);
		this.on_hover = (e) => this.hover(e);
		const el = viewport.renderer.domElement;
		el.addEventListener("pointerdown", this.on_down, true);
		el.addEventListener("pointermove", this.on_hover);
		viewport.extrude_preview = this;
		this.build();
	}

	set({ distance, symmetric, taper }) {
		if (distance !== undefined) this.distance = Number(distance) || 0;
		if (symmetric !== undefined) this.symmetric = Boolean(symmetric);
		if (taper !== undefined) this.taper = Number(taper) || 0;
		this.build();
	}

	// z range of the prism (local)
	span() {
		const d = this.distance;
		return this.symmetric ? [this.base_z - d / 2, this.base_z + d / 2] : [this.base_z, this.base_z + d];
	}

	build() {
		for (const child of [...this.group.children]) if (child !== this.handle) this.group.remove(child), child.geometry && child.geometry.dispose();
		const [z0, z1] = this.span();
		const inset = Math.abs(this.distance) * Math.tan((this.taper * Math.PI) / 180);
		for (const { ring } of this.rings) {
			// as the worker makes it: the profile at the start (moved down by half the distance when symmetric), the
			// tapered outline at the end
			const bottom = ring;
			const top = inset_ring(ring, inset);
			const n = ring.length;
			const pos = [];
			for (const p of bottom) pos.push(p.x, p.y, z0);
			for (const p of top) pos.push(p.x, p.y, z1);
			const index = [];
			for (let i = 0; i < n; i++) {
				const j = (i + 1) % n;
				index.push(i, j, n + j, i, n + j, n + i);
			}
			for (const f of THREE.ShapeUtils.triangulateShape(ring, [])) {
				index.push(f[2], f[1], f[0]);
				index.push(n + f[0], n + f[1], n + f[2]);
			}
			const geometry = new THREE.BufferGeometry();
			geometry.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
			geometry.setIndex(index);
			const mesh = new THREE.Mesh(geometry, this.material);
			mesh.renderOrder = 5;
			const edges = [];
			for (let i = 0; i < n; i++) {
				const j = (i + 1) % n;
				edges.push(bottom[i].x, bottom[i].y, z0, bottom[j].x, bottom[j].y, z0, top[i].x, top[i].y, z1, top[j].x, top[j].y, z1);
			}
			const k = Math.max(1, Math.floor(n / 8)); // a few vertical edges
			for (let i = 0; i < n; i += k) edges.push(bottom[i].x, bottom[i].y, z0, top[i].x, top[i].y, z1);
			const lines = new THREE.LineSegments(new THREE.BufferGeometry().setAttribute("position", new THREE.Float32BufferAttribute(edges, 3)), this.edge_material);
			lines.renderOrder = 6;
			this.group.add(mesh, lines);
		}
		this.refresh();
	}

	// The handle: an arrow at the far face, pointing the way the extrusion goes; its size is constant on screen.
	refresh() {
		for (const child of [...this.handle.children]) this.handle.remove(child), child.geometry.dispose();
		const vp = this.viewport;
		const size = HANDLE_PIXELS * vp.world_per_pixel();
		const [, z1] = this.span();
		const up = this.distance >= 0 ? 1 : -1;
		const shaft = new THREE.Mesh(new THREE.CylinderGeometry(size * 0.035, size * 0.035, size * 0.7, 8), this.handle_material);
		shaft.rotation.x = Math.PI / 2;
		shaft.position.set(0, 0, up * size * 0.35);
		const cone = new THREE.Mesh(new THREE.ConeGeometry(size * 0.12, size * 0.3, 16), this.handle_material);
		cone.rotation.x = up > 0 ? Math.PI / 2 : -Math.PI / 2;
		cone.position.set(0, 0, up * size * 0.85);
		const grip = new THREE.Mesh(new THREE.SphereGeometry(size * 0.09, 12, 8), this.handle_material);
		// an invisible, wider grab zone along the whole arrow (the shaft itself is thin)
		const zone = new THREE.Mesh(new THREE.CylinderGeometry(size * 0.16, size * 0.16, size * 1.1, 8), this.grab_material);
		zone.rotation.x = Math.PI / 2;
		zone.position.set(0, 0, up * size * 0.5);
		this.handle.add(shaft, cone, grip, zone);
		for (const o of this.handle.children) o.renderOrder = 10;
		this.handle.position.set(this.centre.x, this.centre.y, z1);
		vp.render();
	}

	handle_model_point() {
		return this.viewport.to_model(this.handle.position);
	}

	hit(event) {
		const vp = this.viewport;
		vp.raycaster.setFromCamera(vp.ndc(event), vp.camera);
		return vp.raycaster.intersectObjects(this.handle.children, false).length > 0;
	}

	hover(event) {
		if (this.drag) return;
		vp_cursor(this.viewport, this.hit(event) ? "ns-resize" : "");
	}

	// distance along the vertical through the profile's centre nearest to the pointer ray (local z)
	z_at(event) {
		const vp = this.viewport;
		vp.raycaster.setFromCamera(vp.ndc(event), vp.camera);
		const { origin, direction } = vp.raycaster.ray;
		const p0 = new THREE.Vector3(this.centre.x, this.centre.y, this.base_z);
		const w0 = p0.clone().sub(origin);
		const b = direction.z, c = direction.dot(direction), d = w0.z, e = direction.dot(w0);
		const denom = c - b * b;
		if (Math.abs(denom) < 1e-9) return null; // looking straight along the axis (plan view)
		return this.base_z + (b * e - c * d) / denom;
	}

	pointer_down(event) {
		if (event.button !== 0 || !this.hit(event)) return;
		event.stopImmediatePropagation();
		event.preventDefault();
		const el = this.viewport.renderer.domElement;
		el.setPointerCapture(event.pointerId);
		this.drag = true;
		const move = (e) => {
			const z = this.z_at(e);
			if (z === null) return;
			let d = z - this.base_z;
			if (this.symmetric) d *= 2;
			d = snap(d, this.viewport.world_per_pixel(), e.shiftKey);
			if (Math.abs(d) < 1e-9) d = 0;
			this.distance = d;
			this.build();
			this.on_change(d);
		};
		const up = (e) => {
			el.removeEventListener("pointermove", move);
			el.removeEventListener("pointerup", up);
			if (el.hasPointerCapture(e.pointerId)) el.releasePointerCapture(e.pointerId);
			this.drag = false;
		};
		el.addEventListener("pointermove", move);
		el.addEventListener("pointerup", up);
	}

	dispose() {
		const el = this.viewport.renderer.domElement;
		el.removeEventListener("pointerdown", this.on_down, true);
		el.removeEventListener("pointermove", this.on_hover);
		vp_cursor(this.viewport, "");
		this.viewport.feature_overlay.remove(this.group);
		this.group.traverse((o) => o.geometry && o.geometry.dispose());
		if (this.viewport.extrude_preview === this) this.viewport.extrude_preview = null;
		this.viewport.render();
	}
}

function vp_cursor(viewport, cursor) {
	viewport.renderer.domElement.style.cursor = cursor;
}

// a round step for about 8 pixels on screen (0.1, 0.2, 0.5, 1, 2, 5, …); Shift: ten times finer
function snap(value, wpp, fine) {
	const raw = wpp * 8 * (fine ? 0.1 : 1);
	const p = Math.pow(10, Math.floor(Math.log10(raw)));
	const step = [1, 2, 5, 10].map((m) => m * p).find((s) => s >= raw);
	return Math.round(value / step) * step;
}
