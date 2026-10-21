// Graphics of dimensions and hatches, in model coordinates: line segments, filled triangles and texts.
// The stored definition stays authoritative; this is only how it is drawn (and what Explode produces).

import * as THREE from "three";

import { TAU, dimension_measurement, format_number, polyline_points, sweep } from "./geometry";

// ------------------------------------------------------------------------------------- dimensions

const unit = (v) => {
	const l = Math.hypot(v[0], v[1]);
	return l ? [v[0] / l, v[1] / l] : [1, 0];
};
const add = (p, v, k = 1) => [p[0] + v[0] * k, p[1] + v[1] * k, p[2]];

function arrow(out, tip, direction, size, z) {
	// a filled arrowhead with its tip at `tip`, pointing along `direction`
	const d = unit(direction);
	const n = [-d[1], d[0]];
	const base = [tip[0] - d[0] * size, tip[1] - d[1] * size];
	const k = out.positions.length / 3;
	out.positions.push(tip[0], tip[1], z, base[0] + (n[0] * size) / 6, base[1] + (n[1] * size) / 6, z, base[0] - (n[0] * size) / 6, base[1] - (n[1] * size) / 6, z);
	out.index.push(k, k + 1, k + 2);
}

let view_turn = 0; // π while the plan view is turned 180° (westing/southing): text stays upright on screen

function readable(angle) {
	// text along a direction, never upside down (as seen in the view)
	let a = (((angle + view_turn) % TAU) + TAU) % TAU;
	if (a > Math.PI / 2 && a <= (3 * Math.PI) / 2) a -= Math.PI;
	return a - view_turn;
}

export function dimension_text(g, decimals = 3, angular_decimals = 4) {
	const value = dimension_measurement(g);
	const shown = g.kind === "angular" ? `${format_number(value, angular_decimals)}°` : `${g.kind === "radius" ? "R" : g.kind === "diameter" ? "Ø" : ""}${format_number(value, decimals)}`;
	if (!g.text_override) return shown;
	return g.text_override.includes("<>") ? g.text_override.replace("<>", shown) : g.text_override;
}

// {segments: [[p, q]], triangles: {positions, index}, texts: [Text geometry]}
export function dimension_graphics(g, decimals = 3, angular_decimals = 4, turned = false) {
	view_turn = turned ? Math.PI : 0;
	const segments = [], triangles = { positions: [], index: [] }, texts = [];
	const z = g.location[2];
	const h = g.text_height, a = g.arrow_size;
	const text = dimension_text(g, decimals, angular_decimals);
	if (g.kind === "aligned" || g.kind === "linear") {
		const u = g.kind === "aligned" ? unit([g.p2[0] - g.p1[0], g.p2[1] - g.p1[1]]) : [Math.cos(g.angle || 0), Math.sin(g.angle || 0)];
		const n = [-u[1], u[0]];
		const L = g.location;
		const foot = (p) => {
			const t = (p[0] - L[0]) * u[0] + (p[1] - L[1]) * u[1];
			return [L[0] + u[0] * t, L[1] + u[1] * t, z];
		};
		const d1 = foot(g.p1), d2 = foot(g.p2);
		segments.push([d1, d2]);
		for (const [p, d] of [[g.p1, d1], [g.p2, d2]]) {
			const out = unit([d[0] - p[0], d[1] - p[1]]);
			const len = Math.hypot(d[0] - p[0], d[1] - p[1]);
			if (len > a * 0.1) segments.push([add([p[0], p[1], z], out, Math.min(a * 0.5, len)), add(d, out, a * 0.6)]);
		}
		const along = unit([d2[0] - d1[0], d2[1] - d1[1]]);
		arrow(triangles, d1, [-along[0], -along[1]], a, z);
		arrow(triangles, d2, along, a, z);
		const angle = readable(Math.atan2(u[1], u[0]));
		const up = [-Math.sin(angle), Math.cos(angle)];
		const m = [(d1[0] + d2[0]) / 2 + up[0] * h * 0.4, (d1[1] + d2[1]) / 2 + up[1] * h * 0.4, z];
		texts.push({ position: m, text, height: h, rotation: angle, width_factor: 1, halign: "center", valign: "bottom" });
	} else if (g.kind === "radius" || g.kind === "diameter") {
		const c = g.center, p = g.p1;
		const d = unit([p[0] - c[0], p[1] - c[1]]);
		const start = g.kind === "diameter" ? [2 * c[0] - p[0], 2 * c[1] - p[1], z] : [c[0], c[1], z];
		segments.push([start, [p[0], p[1], z]]);
		arrow(triangles, [p[0], p[1], z], d, a, z);
		if (g.kind === "diameter") arrow(triangles, start, [-d[0], -d[1]], a, z);
		const L = g.location;
		const r = Math.hypot(p[0] - c[0], p[1] - c[1]);
		if (Math.hypot(L[0] - c[0], L[1] - c[1]) > r) segments.push([[p[0], p[1], z], [L[0], L[1], z]]);
		texts.push({ position: [L[0], L[1], z], text, height: h, rotation: readable(Math.atan2(d[1], d[0])), width_factor: 1, halign: "center", valign: "bottom" });
	} else {
		const c = g.center;
		const r = Math.hypot(g.location[0] - c[0], g.location[1] - c[1]);
		const a1 = Math.atan2(g.p1[1] - c[1], g.p1[0] - c[0]), a2 = Math.atan2(g.p2[1] - c[1], g.p2[0] - c[0]);
		const s = sweep(a1, a2);
		const n = Math.max(8, Math.ceil((s / TAU) * 96));
		const pts = [];
		for (let i = 0; i <= n; i++) pts.push([c[0] + r * Math.cos(a1 + (s * i) / n), c[1] + r * Math.sin(a1 + (s * i) / n), z]);
		for (let i = 0; i < n; i++) segments.push([pts[i], pts[i + 1]]);
		for (const [p, ang] of [[g.p1, a1], [g.p2, a2]]) {
			const d = Math.hypot(p[0] - c[0], p[1] - c[1]);
			const e = [c[0] + (r + a * 0.6) * Math.cos(ang), c[1] + (r + a * 0.6) * Math.sin(ang), z];
			segments.push([[c[0] + Math.min(d, r) * Math.cos(ang), c[1] + Math.min(d, r) * Math.sin(ang), z], e]);
		}
		arrow(triangles, pts[0], [Math.sin(a1), -Math.cos(a1)], a, z);
		arrow(triangles, pts[n], [-Math.sin(a2), Math.cos(a2)], a, z);
		const am = a1 + s / 2;
		texts.push({ position: [c[0] + (r + h * 0.4) * Math.cos(am), c[1] + (r + h * 0.4) * Math.sin(am), z], text, height: h, rotation: readable(am - Math.PI / 2), width_factor: 1, halign: "center", valign: "bottom" });
	}
	return { segments, triangles, texts };
}

// ---------------------------------------------------------------------------------------- hatches

// pattern: families of parallel lines [angle (deg), spacing (× 3.175 × scale)]; DOTS are dashes of zero length
const PATTERNS = {
	ANSI31: [[45, 1]],
	ANSI37: [[45, 1], [135, 1]],
	LINES: [[0, 1]],
	GRID: [[0, 1], [90, 1]],
	DOTS: [[0, 1]],
};
const BASE_SPACING = 3.175;
const MAX_LINES = 4000;

export function hatch_loops(g) {
	return g.loops.map((loop) => {
		const pts = polyline_points(loop.points, loop.bulges, true);
		return pts.length > 1 && pts[0][0] === pts[pts.length - 1][0] && pts[0][1] === pts[pts.length - 1][1] ? pts.slice(0, -1) : pts;
	});
}

// SOLID → triangles; patterns → line segments clipped to the boundary (even-odd).
export function hatch_graphics(g) {
	const loops = hatch_loops(g);
	const z = g.loops[0].points[0][2];
	if (g.pattern === "SOLID") {
		const [outer, ...holes] = loops.map((loop) => loop.map((p) => new THREE.Vector2(p[0], p[1])));
		const triangles = { positions: [], index: [] };
		if (!outer || outer.length < 3) return { segments: [], triangles };
		const faces = THREE.ShapeUtils.triangulateShape(outer, holes.filter((h) => h.length >= 3));
		const all = [outer, ...holes.filter((h) => h.length >= 3)].flat();
		for (const p of all) triangles.positions.push(p.x, p.y, z);
		for (const f of faces) triangles.index.push(f[0], f[1], f[2]);
		return { segments: [], triangles };
	}
	const segments = [];
	const xs = loops.flat().map((p) => p[0]), ys = loops.flat().map((p) => p[1]);
	const box = [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
	const diagonal = Math.hypot(box[2] - box[0], box[3] - box[1]);
	for (const [deg, factor] of PATTERNS[g.pattern] || PATTERNS.ANSI31) {
		let spacing = BASE_SPACING * (g.scale || 1) * factor;
		if (diagonal / spacing > MAX_LINES) spacing = diagonal / MAX_LINES; // a very fine pattern is drawn coarser
		const angle = (deg * Math.PI) / 180 + (g.angle || 0);
		const d = [Math.cos(angle), Math.sin(angle)], n = [-d[1], d[0]];
		const center = [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2];
		const steps = Math.ceil(diagonal / 2 / spacing);
		for (let k = -steps; k <= steps; k++) {
			const o = [center[0] + n[0] * k * spacing, center[1] + n[1] * k * spacing];
			const hits = [];
			for (const loop of loops) {
				for (let i = 0; i < loop.length; i++) {
					const p = loop[i], q = loop[(i + 1) % loop.length];
					const e = [q[0] - p[0], q[1] - p[1]];
					const den = d[0] * e[1] - d[1] * e[0];
					if (Math.abs(den) < 1e-15) continue;
					const w = [p[0] - o[0], p[1] - o[1]];
					const t = (w[0] * e[1] - w[1] * e[0]) / den;
					const u = (w[0] * d[1] - w[1] * d[0]) / den;
					if (u >= 0 && u < 1) hits.push(t);
				}
			}
			hits.sort((a, b) => a - b);
			for (let i = 0; i + 1 < hits.length; i += 2) {
				const a = [o[0] + d[0] * hits[i], o[1] + d[1] * hits[i], z], b = [o[0] + d[0] * hits[i + 1], o[1] + d[1] * hits[i + 1], z];
				if (g.pattern === "DOTS") {
					for (let t = hits[i] + spacing / 2; t < hits[i + 1]; t += spacing) {
						const p = [o[0] + d[0] * t, o[1] + d[1] * t, z];
						segments.push([p, [p[0] + spacing * 0.02, p[1], z]]);
					}
				} else segments.push([a, b]);
			}
		}
	}
	// the boundary itself is drawn too
	for (const loop of loops) for (let i = 0; i < loop.length; i++) segments.push([loop[i], loop[(i + 1) % loop.length]]);
	return { segments, triangles: { positions: [], index: [] } };
}
