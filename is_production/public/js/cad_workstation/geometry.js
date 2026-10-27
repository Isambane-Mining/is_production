// Exact CAD geometry in the browser: the same definitions as cad_engineering/services/cad_geometry.py.
// Model coordinates are JS doubles; everything sent to WebGL is made relative to the view origin first,
// so float32 buffers keep sub-millimetre precision even at survey coordinates.

import { sample as sample_spline } from "./bspline";

export const TAU = Math.PI * 2;

// X/Y directions of the plane perpendicular to n, exactly as OCCT's gp_Ax2(P, V) (and the bench) pick them.
export function plane_basis(n) {
	const [a, b, c] = n;
	const aa = Math.abs(a), ab = Math.abs(b), ac = Math.abs(c);
	let x;
	if (ab <= aa && ab <= ac) x = aa > ac ? [-c, 0, a] : [c, 0, -a];
	else if (aa <= ab && aa <= ac) x = ab > ac ? [0, -c, b] : [0, c, -b];
	else x = aa > ab ? [-b, a, 0] : [b, -a, 0];
	const length = Math.hypot(...x);
	x = x.map((v) => v / length);
	const y = [b * x[2] - c * x[1], c * x[0] - a * x[2], a * x[1] - b * x[0]];
	return [x, y];
}

export function cross(a, b) {
	return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
}

// Counter-clockwise sweep from a0 to a1 in (0, 2π].
export function sweep(a0, a1) {
	const s = (((a1 - a0) % TAU) + TAU) % TAU;
	return s > 1e-15 ? s : TAU;
}

export function norm_angle(a) {
	return ((a % TAU) + TAU) % TAU;
}

export function is_up(normal) {
	return !normal || (Math.abs(normal[0]) < 1e-12 && Math.abs(normal[1]) < 1e-12 && normal[2] > 0);
}

export function arc_points(center, radius, a0, a1, normal = [0, 0, 1], segments = null) {
	const s = sweep(a0, a1);
	const n = segments || Math.max(8, Math.min(512, Math.ceil((s / TAU) * 128)));
	const [ux, uy] = plane_basis(normal || [0, 0, 1]);
	const points = [];
	for (let i = 0; i <= n; i++) {
		const a = a0 + (s * i) / n;
		const c = Math.cos(a) * radius, si = Math.sin(a) * radius;
		points.push([0, 1, 2].map((k) => center[k] + c * ux[k] + si * uy[k]));
	}
	return points;
}

// A bulged polyline segment (DXF convention, horizontal): centre, radius, start angle, signed sweep.
export function bulge_arc(p0, p1, b) {
	const dx = p1[0] - p0[0], dy = p1[1] - p0[1];
	const c = Math.hypot(dx, dy);
	const rn = [dy / c, -dx / c];
	const r = (c * (1 + b * b)) / (4 * Math.abs(b));
	const mid = [(p0[0] + p1[0]) / 2 + ((b * c) / 2) * rn[0], (p0[1] + p1[1]) / 2 + ((b * c) / 2) * rn[1]];
	const sign = b > 0 ? 1 : -1;
	const center = [mid[0] - sign * r * rn[0], mid[1] - sign * r * rn[1], p0[2]];
	return { center, radius: r, a0: Math.atan2(p0[1] - center[1], p0[0] - center[0]), sweep: 4 * Math.atan(b), mid: [mid[0], mid[1], p0[2]] };
}

export function bulge_between(center, p0, p1, ccw) {
	const a0 = Math.atan2(p0[1] - center[1], p0[0] - center[0]);
	const a1 = Math.atan2(p1[1] - center[1], p1[0] - center[0]);
	const s = ccw ? sweep(a0, a1) : -sweep(a1, a0);
	return Math.tan(s / 4);
}

export function polyline_points(points, bulges, closed) {
	const n = points.length;
	const count = closed ? n : n - 1;
	const out = [points[0]];
	for (let i = 0; i < count; i++) {
		const p0 = points[i], p1 = points[(i + 1) % n];
		const b = bulges ? bulges[i] || 0 : 0;
		if (b) {
			const arc = bulge_arc(p0, p1, b);
			const steps = Math.max(4, Math.min(256, Math.ceil((Math.abs(arc.sweep) / TAU) * 128)));
			for (let k = 1; k < steps; k++) {
				const a = arc.a0 + (arc.sweep * k) / steps;
				out.push([arc.center[0] + arc.radius * Math.cos(a), arc.center[1] + arc.radius * Math.sin(a), p0[2]]);
			}
		}
		out.push(p1);
	}
	return out;
}

export function ellipse_point(g, t) {
	const minor = cross(g.normal || [0, 0, 1], g.major_axis).map((v) => v * g.ratio);
	return [0, 1, 2].map((k) => g.center[k] + Math.cos(t) * g.major_axis[k] + Math.sin(t) * minor[k]);
}

export function ellipse_points(g, segments = 128) {
	const t0 = g.start_param ?? 0;
	const s = sweep(t0, g.end_param ?? TAU);
	const n = Math.max(8, Math.ceil((s / TAU) * segments));
	const out = [];
	for (let i = 0; i <= n; i++) out.push(ellipse_point(g, t0 + (s * i) / n));
	return out;
}

// Polyline points of a curve entity in model coordinates (for drawing, picking and snapping).
export function curve_points(entity) {
	const g = entity.geometry || {};
	switch (entity.entity_type) {
		case "Line":
			return [g.p0, g.p1];
		case "Polyline": {
			const pts = polyline_points(g.points, g.bulges, g.closed);
			return g.closed && !g.bulges ? [...pts, g.points[0]] : pts;
		}
		case "Circle":
			return arc_points(g.center, g.radius, 0, TAU, g.normal);
		case "Arc":
			return arc_points(g.center, g.radius, g.start_angle, g.end_angle, g.normal);
		case "Ellipse":
			return ellipse_points(g);
		case "Spline":
			return sample_spline(g);
		default:
			return null;
	}
}

export function mid(p, q) {
	return [0, 1, 2].map((k) => (p[k] + q[k]) / 2);
}

// Snap candidates: [{point, kind}] — endpoints, midpoints, centres, quadrants, nodes, insertion points.
export function snap_points(entity) {
	const g = entity.geometry || {};
	const out = [];
	switch (entity.entity_type) {
		case "Point":
			out.push({ point: g.p, kind: "node" });
			break;
		case "Line":
			out.push({ point: g.p0, kind: "endpoint" }, { point: g.p1, kind: "endpoint" }, { point: mid(g.p0, g.p1), kind: "midpoint" });
			break;
		case "Polyline": {
			const pts = g.points;
			pts.forEach((p) => out.push({ point: p, kind: "endpoint" }));
			const n = g.closed ? pts.length : pts.length - 1;
			for (let i = 0; i < n; i++) {
				const b = g.bulges ? g.bulges[i] : 0;
				const q = pts[(i + 1) % pts.length];
				if (b) {
					const arc = bulge_arc(pts[i], q, b);
					out.push({ point: arc.mid, kind: "midpoint" }, { point: arc.center, kind: "centre" });
				} else out.push({ point: mid(pts[i], q), kind: "midpoint" });
			}
			break;
		}
		case "Circle":
			out.push({ point: g.center, kind: "centre" });
			if (is_up(g.normal)) for (let k = 0; k < 4; k++) out.push({ point: [g.center[0] + g.radius * Math.cos((k * Math.PI) / 2), g.center[1] + g.radius * Math.sin((k * Math.PI) / 2), g.center[2]], kind: "quadrant" });
			break;
		case "Arc": {
			const pts = arc_points(g.center, g.radius, g.start_angle, g.end_angle, g.normal, 2);
			out.push({ point: g.center, kind: "centre" }, { point: pts[0], kind: "endpoint" }, { point: pts[2], kind: "endpoint" }, { point: pts[1], kind: "midpoint" });
			break;
		}
		case "Ellipse": {
			out.push({ point: g.center, kind: "centre" });
			const closed = sweep(g.start_param ?? 0, g.end_param ?? TAU) >= TAU - 1e-12;
			if (closed) for (let k = 0; k < 4; k++) out.push({ point: ellipse_point(g, (k * Math.PI) / 2), kind: "quadrant" });
			else out.push({ point: ellipse_point(g, g.start_param), kind: "endpoint" }, { point: ellipse_point(g, g.end_param), kind: "endpoint" });
			break;
		}
		case "Spline": {
			const pts = curve_points(entity);
			out.push({ point: pts[0], kind: "endpoint" }, { point: pts[pts.length - 1], kind: "endpoint" });
			for (const p of g.fit_points || []) out.push({ point: p, kind: "node" });
			break;
		}
		case "Construction Line":
			out.push({ point: g.point, kind: "node" });
			break;
		case "Text":
		case "Multiline Text":
			out.push({ point: g.position, kind: "insertion" });
			break;
		case "Dimension":
			out.push({ point: g.p1, kind: "node" }, { point: g.p2, kind: "node" }, { point: g.location, kind: "node" });
			break;
		case "Hatch":
			for (const loop of g.loops) for (const p of loop.points) out.push({ point: p, kind: "endpoint" });
			break;
		case "Block Reference":
			out.push({ point: g.insert, kind: "insertion" });
			break;
		default:
			break;
	}
	return out;
}

// ---------------------------------------------------------------------------------------- transforms

// A plan similarity: (x, y) → M·(x, y) + t with M = s·R(θ) (·diag(1, -1) when mirrored); z → sz·z + tz.
export function similarity({ angle = 0, scale = 1, mirror = false, base = [0, 0, 0], translate = [0, 0, 0], mirror_axis_angle = 0 } = {}) {
	// mirror about the line through base at mirror_axis_angle: M = R(2φ)·diag(1, -1)
	const theta = mirror ? 2 * mirror_axis_angle : angle;
	const c = Math.cos(theta) * scale, s = Math.sin(theta) * scale;
	const m = mirror ? [c, s, s, -c] : [c, -s, s, c];
	const t = [base[0] - (m[0] * base[0] + m[1] * base[1]) + translate[0], base[1] - (m[2] * base[0] + m[3] * base[1]) + translate[1]];
	return { m, t, theta, scale, mirror, sz: scale, tz: base[2] - scale * base[2] + translate[2] };
}

export function translation(d) {
	return similarity({ translate: d });
}

export function apply_point(T, p) {
	return [T.m[0] * p[0] + T.m[1] * p[1] + T.t[0], T.m[2] * p[0] + T.m[3] * p[1] + T.t[1], T.sz * (p[2] || 0) + T.tz];
}

function apply_vector(T, v) {
	return [T.m[0] * v[0] + T.m[1] * v[1], T.m[2] * v[0] + T.m[3] * v[1], T.sz * (v[2] || 0)];
}

function turn(T, a) {
	// a plan angle after the transform
	return T.mirror ? T.theta - a : a + T.theta;
}

function unit(v) {
	const l = Math.hypot(...v);
	return v.map((c) => c / l);
}

// The entity's geometry after a plan similarity (solids are transformed by the CAD worker instead).
export function transform_geometry(entity, T) {
	const g = JSON.parse(JSON.stringify(entity.geometry));
	const P = (p) => apply_point(T, p);
	const flip = (bulges) => (bulges && T.mirror ? bulges.map((b) => -b) : bulges);
	switch (entity.entity_type) {
		case "Point":
			g.p = P(g.p);
			break;
		case "Line":
			g.p0 = P(g.p0);
			g.p1 = P(g.p1);
			break;
		case "Polyline":
			g.points = g.points.map(P);
			if (g.bulges) g.bulges = flip(g.bulges);
			break;
		case "Circle":
			g.center = P(g.center);
			g.radius *= T.scale;
			break;
		case "Arc":
			g.center = P(g.center);
			g.radius *= T.scale;
			if (T.mirror) [g.start_angle, g.end_angle] = [T.theta - g.end_angle, T.theta - g.start_angle];
			else [g.start_angle, g.end_angle] = [g.start_angle + T.theta, g.end_angle + T.theta];
			break;
		case "Ellipse":
			g.center = P(g.center);
			g.major_axis = apply_vector(T, g.major_axis);
			if (T.mirror) [g.start_param, g.end_param] = [-(g.end_param ?? TAU), -(g.start_param ?? 0)];
			break;
		case "Spline":
			g.control_points = g.control_points.map(P);
			if (g.fit_points) g.fit_points = g.fit_points.map(P);
			break;
		case "Construction Line":
			g.point = P(g.point);
			g.direction = unit(apply_vector(T, g.direction));
			break;
		case "Text":
		case "Multiline Text":
			g.position = P(g.position);
			g.height *= T.scale;
			if (g.width) g.width *= T.scale;
			g.rotation = turn(T, g.rotation || 0);
			break;
		case "Dimension":
			for (const k of ["p1", "p2", "location", "center"]) if (g[k]) g[k] = P(g[k]);
			g.text_height *= T.scale;
			g.arrow_size *= T.scale;
			if (g.kind === "linear") g.angle = turn(T, g.angle || 0);
			if (T.mirror && g.kind === "angular") [g.p1, g.p2] = [g.p2, g.p1];
			break;
		case "Hatch":
			g.loops = g.loops.map((loop) => ({ ...loop, points: loop.points.map(P), ...(loop.bulges ? { bulges: flip(loop.bulges) } : {}) }));
			g.scale *= T.scale;
			g.angle = turn(T, g.angle || 0);
			break;
		case "Block Reference":
			g.insert = P(g.insert);
			g.scale = [g.scale[0] * T.scale, g.scale[1] * T.scale * (T.mirror ? -1 : 1), g.scale[2] * T.sz];
			g.rotation = turn(T, g.rotation || 0);
			break;
		default:
			return null;
	}
	return g;
}

export function translate_geometry(entity, d) {
	return transform_geometry(entity, translation(d));
}

// The 3×4 matrix rows the CAD worker applies to a solid for the same transform.
export function solid_matrix(T) {
	return [T.m[0], T.m[1], 0, T.t[0], T.m[2], T.m[3], 0, T.t[1], 0, 0, T.sz, T.tz];
}

// ---------------------------------------------------------------------------------------- measures

export function dimension_measurement(g) {
	const { p1, p2 } = g;
	if (g.kind === "aligned") return Math.hypot(p2[0] - p1[0], p2[1] - p1[1], p2[2] - p1[2]);
	if (g.kind === "linear") return Math.abs((p2[0] - p1[0]) * Math.cos(g.angle || 0) + (p2[1] - p1[1]) * Math.sin(g.angle || 0));
	const c = g.center;
	if (g.kind === "radius") return Math.hypot(p1[0] - c[0], p1[1] - c[1], p1[2] - c[2]);
	if (g.kind === "diameter") return 2 * Math.hypot(p1[0] - c[0], p1[1] - c[1], p1[2] - c[2]);
	return (sweep(Math.atan2(p1[1] - c[1], p1[0] - c[0]), Math.atan2(p2[1] - c[1], p2[0] - c[0])) * 180) / Math.PI;
}

// Signed area (shoelace + bulge segments) of a closed loop in plan; positive = counter-clockwise.
export function loop_area(points, bulges) {
	let area = 0;
	const n = points.length;
	for (let i = 0; i < n; i++) {
		const p = points[i], q = points[(i + 1) % n];
		area += (p[0] * q[1] - q[0] * p[1]) / 2;
		const b = bulges ? bulges[i] || 0 : 0;
		if (b) {
			const arc = bulge_arc(p, q, b);
			const s = arc.sweep;
			area += (arc.radius * arc.radius * (s - Math.sin(s))) / 2;
		}
	}
	return area;
}

// ---------------------------------------------------------------------------------------- extents

export function extent(points) {
	const box = [Infinity, Infinity, Infinity, -Infinity, -Infinity, -Infinity];
	for (const p of points) {
		for (let k = 0; k < 3; k++) {
			box[k] = Math.min(box[k], p[k] || 0);
			box[k + 3] = Math.max(box[k + 3], p[k] || 0);
		}
	}
	return box;
}

export function union(boxes) {
	const valid = boxes.filter((b) => b && b.every(Number.isFinite));
	if (!valid.length) return null;
	return [0, 1, 2].map((k) => Math.min(...valid.map((b) => b[k]))).concat([3, 4, 5].map((k) => Math.max(...valid.map((b) => b[k]))));
}

// Local extent of a block's content (nested blocks through `blocks`, a Map of name → block).
export function block_extent(block, blocks, depth = 0) {
	if (!block || depth > 16) return null;
	if (block._extent !== undefined) return block._extent;
	const boxes = (block.entities || []).map((e) => entity_extent(e, blocks, depth + 1));
	block._extent = union(boxes);
	return block._extent;
}

export function block_matrix(g, base) {
	const [sx, sy, sz] = g.scale || [1, 1, 1];
	const c = Math.cos(g.rotation || 0), s = Math.sin(g.rotation || 0);
	return (p) => {
		const x = p[0] - base[0], y = p[1] - base[1], z = (p[2] || 0) - base[2];
		return [g.insert[0] + c * sx * x - s * sy * y, g.insert[1] + s * sx * x + c * sy * y, g.insert[2] + sz * z];
	};
}

export function entity_extent(entity, blocks = null, depth = 0) {
	const g = entity.geometry || {};
	switch (entity.entity_type) {
		case "Solid":
		case "Surface":
		case "Mesh":
		case "Point Cloud":
			return entity.bbox || (entity.min_x !== undefined ? [entity.min_x, entity.min_y, entity.min_z, entity.max_x, entity.max_y, entity.max_z] : null);
		case "Point":
			return extent([g.p]);
		case "Construction Line":
			return extent([g.point]);
		case "Text":
		case "Multiline Text":
			return extent([g.position, [g.position[0] + g.height * 0.6 * Math.max(String(g.text || "").length, 1), g.position[1] + g.height, g.position[2]]]);
		case "Dimension":
			return extent([g.p1, g.p2, g.location, ...(g.center ? [g.center] : [])]);
		case "Hatch":
			return union(g.loops.map((loop) => extent(polyline_points(loop.points, loop.bulges, true))));
		case "Block Reference": {
			const block = blocks && blocks.get(g.block);
			const local = block_extent(block, blocks, depth);
			if (!local) return extent([g.insert]);
			const M = block_matrix(g, block.base_point || [0, 0, 0]);
			const corners = [];
			for (const x of [local[0], local[3]]) for (const y of [local[1], local[4]]) for (const z of [local[2], local[5]]) corners.push(M([x, y, z]));
			return extent(corners);
		}
		default: {
			const pts = curve_points(entity);
			return pts ? extent(pts) : null;
		}
	}
}

// ---------------------------------------------------------------------------------------- misc

export function distance(p, q) {
	return Math.hypot(q[0] - p[0], q[1] - p[1], (q[2] || 0) - (p[2] || 0));
}

export function format_number(value, decimals = 3) {
	if (value === null || value === undefined || Number.isNaN(value)) return "";
	return Number(value).toLocaleString("en-ZA", { minimumFractionDigits: decimals, maximumFractionDigits: decimals, useGrouping: false });
}

export function format_point(p, decimals = 3) {
	return p ? p.map((v) => format_number(v, decimals)).join(", ") : "";
}

export function new_entity_id() {
	return `E${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`.toUpperCase();
}

// A closed loop usable as a hatch boundary or extrusion profile: {points, bulges}, or null.
export function closed_loop(entity) {
	const g = entity && entity.geometry;
	if (!g) return null;
	if (entity.entity_type === "Polyline" && g.closed && (g.points.length >= 3 || (g.bulges && g.bulges.some(Boolean)))) return { points: g.points, bulges: g.bulges };
	if (entity.entity_type === "Circle" && is_up(g.normal)) {
		const c = g.center, r = g.radius;
		return { points: [[c[0] + r, c[1], c[2]], [c[0] - r, c[1], c[2]]], bulges: [1, 1] };
	}
	return null;
}

export function is_closed_profile(entity) {
	const loop = closed_loop(entity);
	return Boolean(loop && !(loop.bulges && loop.bulges.some(Boolean)) && loop.points.length >= 3);
}
