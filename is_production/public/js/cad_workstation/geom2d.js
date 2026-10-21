// Plan (XY) geometry for editing: entities as exact primitives — line segments and circular arcs — with
// analytic intersections, nearest points and the modify operations built on them (trim, extend, break,
// join, fillet, chamfer, offset, stretch, explode). Elevation is carried along (interpolated on lines).
// Ellipses and splines take part as fine polyline approximations (used as cutting edges and for snaps);
// trimming them is limited to ellipses (their parameters map back exactly).
//
// The CAD worker re-checks and measures every result after Save; complex polyline offsets go to OCCT.

import { domain as spline_domain, sample as sample_spline } from "./bspline";
import { TAU, arc_points, block_extent, bulge_arc, ellipse_point, is_up, polyline_points, similarity, sweep, transform_geometry } from "./geometry";

const EPS = 1e-9;

// ------------------------------------------------------------------------------------- primitives

// [{kind: "line", p0, p1} | {kind: "arc", c, r, a0, s (signed sweep), z}], plus whether it is approximate.
export function primitives(entity) {
	const g = entity.geometry || {};
	switch (entity.entity_type) {
		case "Line":
			return { prims: [{ kind: "line", p0: g.p0, p1: g.p1 }], exact: true };
		case "Polyline": {
			const pts = g.points, n = pts.length, count = g.closed ? n : n - 1;
			const prims = [];
			for (let i = 0; i < count; i++) {
				const p0 = pts[i], p1 = pts[(i + 1) % n], b = g.bulges ? g.bulges[i] || 0 : 0;
				if (b) {
					const arc = bulge_arc(p0, p1, b);
					prims.push({ kind: "arc", c: arc.center, r: arc.radius, a0: arc.a0, s: arc.sweep, z: p0[2] });
				} else prims.push({ kind: "line", p0, p1 });
			}
			return { prims, exact: true, closed: Boolean(g.closed) };
		}
		case "Circle":
			if (is_up(g.normal)) return { prims: [{ kind: "arc", c: g.center, r: g.radius, a0: 0, s: TAU, z: g.center[2], full: true }], exact: true, closed: true };
			break;
		case "Arc":
			if (is_up(g.normal)) return { prims: [{ kind: "arc", c: g.center, r: g.radius, a0: g.start_angle, s: sweep(g.start_angle, g.end_angle), z: g.center[2] }], exact: true };
			break;
		case "Ellipse": {
			const pts = ellipse_dense(g);
			return { prims: lines_of(pts), exact: false, closed: sweep(g.start_param ?? 0, g.end_param ?? TAU) >= TAU - 1e-12 };
		}
		case "Spline":
			return { prims: lines_of(sample_spline(g, Math.min(4096, g.control_points.length * 32))), exact: false };
		case "Construction Line": {
			const far = 1e6;
			const a = g.ray ? g.point : g.point.map((v, k) => v - g.direction[k] * far);
			return { prims: [{ kind: "line", p0: a, p1: g.point.map((v, k) => v + g.direction[k] * far), infinite: true }], exact: true };
		}
		default:
			break;
	}
	const pts = entity.geometry && entity.geometry.normal ? arc_points(g.center, g.radius, g.start_angle ?? 0, g.end_angle ?? TAU, g.normal, 256) : null;
	return pts ? { prims: lines_of(pts), exact: false } : { prims: [], exact: false };
}

const ELLIPSE_SEGMENTS = 1024;

function ellipse_dense(g) {
	const t0 = g.start_param ?? 0, s = sweep(t0, g.end_param ?? TAU);
	const out = [];
	for (let i = 0; i <= ELLIPSE_SEGMENTS; i++) out.push(ellipse_point(g, t0 + (s * i) / ELLIPSE_SEGMENTS));
	return out;
}

function lines_of(pts) {
	const prims = [];
	for (let i = 0; i < pts.length - 1; i++) prims.push({ kind: "line", p0: pts[i], p1: pts[i + 1] });
	return prims;
}

export function point_at(prim, t) {
	if (prim.kind === "line") return [0, 1, 2].map((k) => prim.p0[k] + (prim.p1[k] - prim.p0[k]) * t);
	const a = prim.a0 + prim.s * t;
	return [prim.c[0] + prim.r * Math.cos(a), prim.c[1] + prim.r * Math.sin(a), prim.z];
}

function angle_param(prim, a) {
	// parameter (0…1 along the arc's sweep) of angle a, possibly outside [0, 1]
	const rel = prim.s >= 0 ? ((a - prim.a0) % TAU + TAU) % TAU : ((prim.a0 - a) % TAU + TAU) % TAU;
	return rel / Math.abs(prim.s);
}

// -------------------------------------------------------------------------------- intersections

// Intersections of two primitives: [{t1, t2, point}]. unbounded1/2: ignore the parameter range (extend).
export function intersect(a, b, unbounded1 = false, unbounded2 = false) {
	const out = [];
	const ok = (t, unbounded, prim) => unbounded || (prim.kind === "arc" && prim.full) || (t >= -EPS && t <= 1 + EPS);
	if (a.kind === "line" && b.kind === "line") {
		const d1 = [a.p1[0] - a.p0[0], a.p1[1] - a.p0[1]], d2 = [b.p1[0] - b.p0[0], b.p1[1] - b.p0[1]];
		const den = d1[0] * d2[1] - d1[1] * d2[0];
		if (Math.abs(den) < 1e-15 * Math.hypot(...d1) * Math.hypot(...d2)) return out;
		const w = [b.p0[0] - a.p0[0], b.p0[1] - a.p0[1]];
		const t1 = (w[0] * d2[1] - w[1] * d2[0]) / den, t2 = (w[0] * d1[1] - w[1] * d1[0]) / den;
		if (ok(t1, unbounded1, a) && ok(t2, unbounded2, b)) out.push({ t1, t2, point: point_at(a, t1) });
		return out;
	}
	if (a.kind === "arc" && b.kind === "line") return intersect(b, a, unbounded2, unbounded1).map((x) => ({ t1: x.t2, t2: x.t1, point: x.point }));
	if (a.kind === "line" && b.kind === "arc") {
		const d = [a.p1[0] - a.p0[0], a.p1[1] - a.p0[1]], f = [a.p0[0] - b.c[0], a.p0[1] - b.c[1]];
		const A = d[0] * d[0] + d[1] * d[1], B = 2 * (f[0] * d[0] + f[1] * d[1]), C = f[0] * f[0] + f[1] * f[1] - b.r * b.r;
		let disc = B * B - 4 * A * C;
		if (disc < -1e-12 * B * B) return out;
		disc = Math.max(disc, 0);
		const roots = disc === 0 ? [-B / (2 * A)] : [(-B - Math.sqrt(disc)) / (2 * A), (-B + Math.sqrt(disc)) / (2 * A)];
		for (const t1 of roots) {
			const p = point_at(a, t1);
			const t2 = angle_param(b, Math.atan2(p[1] - b.c[1], p[0] - b.c[0]));
			if (ok(t1, unbounded1, a) && (ok(t2, unbounded2, b) || near_end(t2, b))) out.push({ t1, t2: t2 > 1 + EPS ? 0 : t2, point: p });
		}
		return out;
	}
	// arc – arc
	const dx = b.c[0] - a.c[0], dy = b.c[1] - a.c[1], dist = Math.hypot(dx, dy);
	if (dist < 1e-12 || dist > a.r + b.r + 1e-9 || dist < Math.abs(a.r - b.r) - 1e-9) return out;
	const along = (a.r * a.r - b.r * b.r + dist * dist) / (2 * dist);
	const h = Math.sqrt(Math.max(a.r * a.r - along * along, 0));
	const mx = a.c[0] + (along * dx) / dist, my = a.c[1] + (along * dy) / dist;
	const candidates = h < 1e-12 ? [[mx, my]] : [[mx + (h * dy) / dist, my - (h * dx) / dist], [mx - (h * dy) / dist, my + (h * dx) / dist]];
	for (const [x, y] of candidates) {
		const t1 = angle_param(a, Math.atan2(y - a.c[1], x - a.c[0]));
		const t2 = angle_param(b, Math.atan2(y - b.c[1], x - b.c[0]));
		if ((ok(t1, unbounded1, a) || near_end(t1, a)) && (ok(t2, unbounded2, b) || near_end(t2, b))) out.push({ t1: t1 > 1 + EPS ? 0 : t1, t2: t2 > 1 + EPS ? 0 : t2, point: [x, y, a.z] });
	}
	return out;
}

function near_end(t, arc) {
	// an angle just below the start wraps to ~1/|s|·2π: treat as t = 0
	return arc.kind === "arc" && Math.abs(t * Math.abs(arc.s) - TAU) < 1e-9;
}

// All intersections between two entities: [{T1, T2, point}] with T = primitive index + local parameter.
export function entity_intersections(e1, e2) {
	const A = primitives(e1).prims, B = primitives(e2).prims;
	const out = [];
	for (let i = 0; i < A.length; i++) {
		for (let j = 0; j < B.length; j++) {
			if (!boxes_meet(A[i], B[j])) continue;
			for (const x of intersect(A[i], B[j])) out.push({ T1: i + x.t1, T2: j + x.t2, point: x.point });
		}
	}
	return out;
}

function prim_box(p) {
	if (p._box) return p._box;
	if (p.kind === "line") p._box = [Math.min(p.p0[0], p.p1[0]), Math.min(p.p0[1], p.p1[1]), Math.max(p.p0[0], p.p1[0]), Math.max(p.p0[1], p.p1[1])];
	else p._box = [p.c[0] - p.r, p.c[1] - p.r, p.c[0] + p.r, p.c[1] + p.r];
	return p._box;
}

function boxes_meet(a, b) {
	const x = prim_box(a), y = prim_box(b), tol = 1e-9;
	return x[0] <= y[2] + tol && y[0] <= x[2] + tol && x[1] <= y[3] + tol && y[1] <= x[3] + tol;
}

// ------------------------------------------------------------------------------------ nearest points

export function nearest_on(prim, q) {
	if (prim.kind === "line") {
		const d = [prim.p1[0] - prim.p0[0], prim.p1[1] - prim.p0[1]];
		const l2 = d[0] * d[0] + d[1] * d[1];
		const t = l2 ? Math.min(1, Math.max(0, ((q[0] - prim.p0[0]) * d[0] + (q[1] - prim.p0[1]) * d[1]) / l2)) : 0;
		const p = point_at(prim, t);
		return { t, point: p, dist: Math.hypot(p[0] - q[0], p[1] - q[1]) };
	}
	let t = angle_param(prim, Math.atan2(q[1] - prim.c[1], q[0] - prim.c[0]));
	if (!prim.full && t > 1) t = t - 1 < TAU / Math.abs(prim.s) - t ? 1 : 0;
	const p = point_at(prim, Math.min(t, 1));
	return { t: Math.min(t, 1), point: p, dist: Math.hypot(p[0] - q[0], p[1] - q[1]) };
}

export function entity_nearest(entity, q) {
	const { prims } = primitives(entity);
	let best = null;
	prims.forEach((prim, i) => {
		const n = nearest_on(prim, q);
		if (!best || n.dist < best.dist) best = { ...n, T: i + n.t, index: i };
	});
	return best;
}

// Foot of the perpendicular from q onto the primitive (unbounded for lines, on the circle for arcs).
export function perpendicular_foot(prim, q) {
	if (prim.kind === "line") {
		const d = [prim.p1[0] - prim.p0[0], prim.p1[1] - prim.p0[1]];
		const t = ((q[0] - prim.p0[0]) * d[0] + (q[1] - prim.p0[1]) * d[1]) / (d[0] * d[0] + d[1] * d[1]);
		return t >= -EPS && t <= 1 + EPS ? point_at(prim, t) : null;
	}
	const a = Math.atan2(q[1] - prim.c[1], q[0] - prim.c[0]);
	return [prim.c[0] + prim.r * Math.cos(a), prim.c[1] + prim.r * Math.sin(a), prim.z];
}

// Tangent points on a circle/arc from an outside point q.
export function tangent_points(prim, q) {
	if (prim.kind !== "arc") return [];
	const dx = q[0] - prim.c[0], dy = q[1] - prim.c[1], d = Math.hypot(dx, dy);
	if (d <= prim.r) return [];
	const base = Math.atan2(dy, dx), off = Math.acos(prim.r / d);
	return [base + off, base - off]
		.filter((a) => prim.full || angle_param(prim, a) <= 1 + EPS)
		.map((a) => [prim.c[0] + prim.r * Math.cos(a), prim.c[1] + prim.r * Math.sin(a), prim.z]);
}

// ------------------------------------------------------------------------------------- sub-curves

function point_at_T(prims, T) {
	const i = Math.min(Math.floor(T), prims.length - 1);
	return point_at(prims[i], T - i);
}

// The part of an entity between parameters T0 < T1 (closed curves wrap when T1 ≤ T0: through the start).
export function sub_curve(entity, T0, T1) {
	const g = entity.geometry;
	const { prims } = primitives(entity);
	const N = prims.length;
	switch (entity.entity_type) {
		case "Line":
			return { entity_type: "Line", geometry: { p0: point_at(prims[0], T0), p1: point_at(prims[0], T1) } };
		case "Arc":
		case "Circle": {
			const p = prims[0];
			const a0 = p.a0 + p.s * T0, a1 = p.a0 + p.s * (T1 > T0 ? T1 : T1 + 1);
			return { entity_type: "Arc", geometry: { center: g.center, radius: g.radius, start_angle: a0, end_angle: a1, normal: [0, 0, 1] } };
		}
		case "Ellipse": {
			const t0 = g.start_param ?? 0, s = sweep(t0, g.end_param ?? TAU);
			const u0 = t0 + (s * T0) / N, u1 = t0 + (s * (T1 > T0 ? T1 : T1 + N)) / N;
			return { entity_type: "Ellipse", geometry: { ...g, start_param: u0, end_param: u1 } };
		}
		case "Polyline": {
			const end = T1 > T0 ? T1 : T1 + N; // closed curves wrap through the start
			const points = [point_at_T(prims, T0)], bulges = [];
			let T = T0;
			while (T < end - 1e-12) {
				const i = Math.floor(T + 1e-12) % N;
				const seg_start = Math.floor(T + 1e-12);
				const next = Math.min(seg_start + 1, end);
				const prim = prims[i];
				const local0 = T - seg_start, local1 = next - seg_start;
				points.push(point_at(prim, local1));
				bulges.push(prim.kind === "arc" ? Math.tan((prim.s * (local1 - local0)) / 4) : 0);
				T = next;
			}
			const clean = { points: [], bulges: [] };
			points.forEach((p, k) => {
				const last = clean.points[clean.points.length - 1];
				if (last && Math.hypot(p[0] - last[0], p[1] - last[1], p[2] - last[2]) < 1e-9) return;
				if (k > 0) clean.bulges.push(bulges[k - 1]);
				clean.points.push(p);
			});
			if (clean.points.length < 2) return null;
			const geometry = { points: clean.points, closed: false };
			if (clean.bulges.some(Boolean)) geometry.bulges = clean.bulges;
			return { entity_type: "Polyline", geometry };
		}
		default:
			return null;
	}
}

function param_length(entity) {
	return primitives(entity).prims.length;
}

function is_closed(entity) {
	const g = entity.geometry;
	return entity.entity_type === "Circle" || (entity.entity_type === "Polyline" && g.closed) || (entity.entity_type === "Ellipse" && sweep(g.start_param ?? 0, g.end_param ?? TAU) >= TAU - 1e-12);
}

const TRIMMABLE = new Set(["Line", "Arc", "Circle", "Polyline", "Ellipse"]);

// ------------------------------------------------------------------------------------------ trim

// Remove the piece of `entity` around `pick` that lies between its nearest intersections with `cutters`.
// Returns the replacement pieces ([] = the whole entity goes), or null when nothing can be trimmed.
export function trim(entity, pick, cutters) {
	if (!TRIMMABLE.has(entity.entity_type)) return null;
	const near = entity_nearest(entity, pick);
	if (!near) return null;
	const cuts = [];
	for (const other of cutters) {
		if (other === entity || other.entity_id === entity.entity_id) continue;
		for (const x of entity_intersections(entity, other)) cuts.push(x.T1);
	}
	const N = param_length(entity);
	const Tp = near.T;
	const closed = is_closed(entity);
	// distinct cut parameters, kept exact (no rounding: that would move the cut)
	const unique = [];
	for (const t of cuts.filter((t) => (t > 1e-9 && t < N - 1e-9) || closed).sort((a, b) => a - b)) if (!unique.length || t - unique[unique.length - 1] > 1e-12) unique.push(t);
	const below = unique.filter((t) => t < Tp - 1e-9), above = unique.filter((t) => t > Tp + 1e-9);
	if (closed) {
		if (unique.length < 2) return null;
		const ta = below.length ? below[below.length - 1] : unique[unique.length - 1];
		const tb = above.length ? above[0] : unique[0];
		const piece = sub_curve(entity, tb, ta);
		return piece ? [piece] : null;
	}
	const ta = below.length ? below[below.length - 1] : null;
	const tb = above.length ? above[0] : null;
	if (ta === null && tb === null) return null;
	const pieces = [];
	if (ta !== null) pieces.push(sub_curve(entity, 0, ta));
	if (tb !== null) pieces.push(sub_curve(entity, tb, N));
	return pieces.filter(Boolean);
}

// ------------------------------------------------------------------------------------------ extend

// Extend the end of an open Line/Arc/Polyline nearest to `pick` to the first boundary it meets.
export function extend(entity, pick, boundaries) {
	const g = entity.geometry;
	if (!["Line", "Arc", "Polyline"].includes(entity.entity_type) || is_closed(entity)) return null;
	const { prims } = primitives(entity);
	const near = entity_nearest(entity, pick);
	const N = prims.length;
	const at_end = near.T > N / 2;
	const prim = at_end ? prims[N - 1] : prims[0];
	let best = null;
	for (const other of boundaries) {
		if (other.entity_id === entity.entity_id) continue;
		for (const b of primitives(other).prims) {
			const ext = prim.kind === "arc" ? { ...prim, full: true } : prim;
			for (const x of intersect(ext, b, true, false)) {
				let t = x.t1;
				if (prim.kind === "arc") {
					t = angle_param(prim, Math.atan2(x.point[1] - prim.c[1], x.point[0] - prim.c[0]));
					if (!at_end) t = t > 1 ? t - TAU / Math.abs(prim.s) : t; // before the start: negative
				}
				const gain = at_end ? t - 1 : -t;
				if (gain > 1e-9 && (!best || gain < best.gain)) best = { gain, t, point: x.point };
			}
		}
	}
	if (!best) return null;
	const out = JSON.parse(JSON.stringify(g));
	if (entity.entity_type === "Line") {
		if (at_end) out.p1 = best.point;
		else out.p0 = best.point;
	} else if (entity.entity_type === "Arc") {
		const a = Math.atan2(best.point[1] - g.center[1], best.point[0] - g.center[0]);
		if (at_end) out.end_angle = a;
		else out.start_angle = a;
		if (sweep(out.start_angle, out.end_angle) >= TAU - 1e-9) return null;
	} else {
		const k = at_end ? out.points.length - 1 : 0;
		if (prim.kind === "arc") {
			// keep the arc's centre: the bulge grows with the sweep
			const s_new = prim.s * (at_end ? best.t : 1 - best.t);
			const bi = at_end ? N - 1 : 0;
			out.bulges[bi] = Math.tan(s_new / 4);
		}
		out.points[k] = [best.point[0], best.point[1], prim.kind === "arc" ? prim.z : best.point[2]];
	}
	return out;
}

// ------------------------------------------------------------------------------------------ break

export function break_at(entity, point) {
	if (!TRIMMABLE.has(entity.entity_type) || entity.entity_type === "Circle") return null;
	const near = entity_nearest(entity, point);
	const N = param_length(entity);
	if (is_closed(entity)) {
		const piece = sub_curve(entity, near.T, near.T);
		return piece ? [piece] : null;
	}
	if (near.T < 1e-9 || near.T > N - 1e-9) return null;
	return [sub_curve(entity, 0, near.T), sub_curve(entity, near.T, N)].filter(Boolean);
}

// ------------------------------------------------------------------------------------------- join

// Join lines, arcs and open polylines that meet end to end into one polyline (closed if it closes).
export function join(entities, tolerance = 1e-6) {
	const pieces = [];
	for (const e of entities) {
		if (!["Line", "Arc", "Polyline"].includes(e.entity_type) || is_closed(e)) return null;
		if (e.entity_type === "Arc" && !is_up(e.geometry.normal)) return null;
		const { prims } = primitives(e);
		pieces.push(prims);
	}
	const chain = [...pieces.shift()];
	const same = (p, q) => Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2]) <= tolerance;
	const start = (prims) => point_at(prims[0], 0), finish = (prims) => point_at(prims[prims.length - 1], 1);
	let progress = true;
	while (pieces.length && progress) {
		progress = false;
		for (let i = 0; i < pieces.length; i++) {
			const p = pieces[i];
			if (same(finish(chain), start(p))) chain.push(...p);
			else if (same(finish(chain), finish(p))) chain.push(...reverse_prims(p));
			else if (same(start(chain), finish(p))) chain.unshift(...p);
			else if (same(start(chain), start(p))) chain.unshift(...reverse_prims(p));
			else continue;
			pieces.splice(i, 1);
			progress = true;
			break;
		}
	}
	if (pieces.length) return null;
	const points = [start(chain)], bulges = [];
	for (const prim of chain) {
		points.push(point_at(prim, 1));
		bulges.push(prim.kind === "arc" ? Math.tan(prim.s / 4) : 0);
	}
	const closed = same(points[0], points[points.length - 1]) && points.length > 2;
	if (closed) points.pop();
	const geometry = { points, closed };
	if (bulges.some(Boolean)) geometry.bulges = closed ? bulges : bulges;
	return { entity_type: "Polyline", geometry };
}

function reverse_prims(prims) {
	return [...prims].reverse().map((p) => (p.kind === "line" ? { kind: "line", p0: p.p1, p1: p.p0 } : { ...p, a0: p.a0 + p.s, s: -p.s }));
}

// ------------------------------------------------------------------------------- fillet / chamfer

function line_of(entity, pick) {
	if (entity.entity_type === "Line") return { p0: entity.geometry.p0, p1: entity.geometry.p1 };
	return null;
}

function corner(a, b, pa, pb) {
	// intersection X of two infinite lines and the unit directions from X towards the picked sides
	const x = intersect({ kind: "line", p0: a.p0, p1: a.p1 }, { kind: "line", p0: b.p0, p1: b.p1 }, true, true)[0];
	if (!x) return null;
	const X = x.point;
	const side = (l, pick) => {
		const d = [l.p1[0] - l.p0[0], l.p1[1] - l.p0[1]];
		const len = Math.hypot(...d);
		const u = [d[0] / len, d[1] / len];
		const s = (pick[0] - X[0]) * u[0] + (pick[1] - X[1]) * u[1];
		return s >= 0 ? u : [-u[0], -u[1]];
	};
	return { X, ua: side(a, pa), ub: side(b, pb) };
}

function far_end(l, X, u) {
	// the endpoint of l on the kept side (furthest along u from X)
	const s0 = (l.p0[0] - X[0]) * u[0] + (l.p0[1] - X[1]) * u[1];
	const s1 = (l.p1[0] - X[0]) * u[0] + (l.p1[1] - X[1]) * u[1];
	return s0 > s1 ? l.p0 : l.p1;
}

function z_on(l, p) {
	const d = Math.hypot(l.p1[0] - l.p0[0], l.p1[1] - l.p0[1]);
	const t = d ? Math.hypot(p[0] - l.p0[0], p[1] - l.p0[1]) / d : 0;
	return l.p0[2] + (l.p1[2] - l.p0[2]) * t;
}

// Two lines → [new geometry of a, new geometry of b, arc entity or null].
export function fillet_lines(ea, eb, pa, pb, radius) {
	const a = line_of(ea), b = line_of(eb);
	if (!a || !b) return null;
	const k = corner(a, b, pa, pb);
	if (!k) return null;
	const { X, ua, ub } = k;
	const cos = Math.max(-1, Math.min(1, ua[0] * ub[0] + ua[1] * ub[1]));
	const theta = Math.acos(cos);
	if (theta < 1e-9 || Math.PI - theta < 1e-9) return null;
	if (!(radius > 0)) {
		const Xa = [X[0], X[1], z_on(a, X)], Xb = [X[0], X[1], z_on(b, X)];
		return [{ p0: Xa, p1: far_end(a, X, ua) }, { p0: Xb, p1: far_end(b, X, ub) }, null];
	}
	const d = radius / Math.tan(theta / 2);
	const Ta = [X[0] + ua[0] * d, X[1] + ua[1] * d], Tb = [X[0] + ub[0] * d, X[1] + ub[1] * d];
	const bis = [ua[0] + ub[0], ua[1] + ub[1]];
	const bl = Math.hypot(...bis);
	const h = radius / Math.sin(theta / 2);
	const z = z_on(a, X);
	const C = [X[0] + (bis[0] / bl) * h, X[1] + (bis[1] / bl) * h, z];
	let a0 = Math.atan2(Ta[1] - C[1], Ta[0] - C[0]), a1 = Math.atan2(Tb[1] - C[1], Tb[0] - C[0]);
	if (Math.abs(sweep(a0, a1) - (Math.PI - theta)) > 1e-6) [a0, a1] = [a1, a0];
	return [
		{ p0: [Ta[0], Ta[1], z_on(a, Ta)], p1: far_end(a, X, ua) },
		{ p0: [Tb[0], Tb[1], z_on(b, Tb)], p1: far_end(b, X, ub) },
		{ entity_type: "Arc", geometry: { center: C, radius, start_angle: a0, end_angle: a1, normal: [0, 0, 1] } },
	];
}

export function chamfer_lines(ea, eb, pa, pb, d1, d2) {
	const a = line_of(ea), b = line_of(eb);
	if (!a || !b) return null;
	const k = corner(a, b, pa, pb);
	if (!k) return null;
	const { X, ua, ub } = k;
	const Ca = [X[0] + ua[0] * d1, X[1] + ua[1] * d1], Cb = [X[0] + ub[0] * d2, X[1] + ub[1] * d2];
	const pa3 = [Ca[0], Ca[1], z_on(a, Ca)], pb3 = [Cb[0], Cb[1], z_on(b, Cb)];
	return [{ p0: pa3, p1: far_end(a, X, ua) }, { p0: pb3, p1: far_end(b, X, ub) }, { entity_type: "Line", geometry: { p0: pa3, p1: pb3 } }];
}

// Round (radius) or bevel (d1, d2) the polyline vertex nearest to `pick` (both neighbouring segments straight).
export function polyline_corner(entity, pick, { radius = null, d1 = null, d2 = null }) {
	const g = JSON.parse(JSON.stringify(entity.geometry));
	const pts = g.points, n = pts.length;
	let k = -1, best = Infinity;
	pts.forEach((p, i) => {
		const d = Math.hypot(p[0] - pick[0], p[1] - pick[1]);
		if (d < best && (g.closed || (i > 0 && i < n - 1))) [best, k] = [d, i];
	});
	if (k < 0) return null;
	const prev = (k - 1 + n) % n, next = (k + 1) % n;
	const bulges = g.bulges || new Array(g.closed ? n : n - 1).fill(0);
	if (bulges[prev] || bulges[k]) return null;
	const P = pts[k];
	const unit = (q) => {
		const d = [q[0] - P[0], q[1] - P[1]];
		const l = Math.hypot(...d);
		return [d[0] / l, d[1] / l, l];
	};
	const ua = unit(pts[prev]), ub = unit(pts[next]);
	const theta = Math.acos(Math.max(-1, Math.min(1, ua[0] * ub[0] + ua[1] * ub[1])));
	let da, db, bulge = 0;
	if (radius !== null) {
		da = db = radius / Math.tan(theta / 2);
		const turning = ua[0] * ub[1] - ua[1] * ub[0]; // >0: the path turns clockwise at P
		bulge = Math.tan((turning > 0 ? -1 : 1) * (Math.PI - theta) / 4);
	} else {
		[da, db] = [d1, d2];
	}
	if (da >= ua[2] - 1e-9 || db >= ub[2] - 1e-9) return null;
	const A = [P[0] + ua[0] * da, P[1] + ua[1] * da, P[2]], B = [P[0] + ub[0] * db, P[1] + ub[1] * db, P[2]];
	pts.splice(k, 1, A, B);
	bulges.splice(k, 0, bulge);
	g.points = pts;
	if (bulges.some(Boolean)) g.bulges = bulges;
	else delete g.bulges;
	return g;
}

// ------------------------------------------------------------------------------------------ offset

// Offsets that stay exact in the browser (lines, circles, arcs). Polylines go to the CAD worker (OCCT).
export function offset_simple(entity, distance, side_point) {
	const g = entity.geometry;
	if (entity.entity_type === "Line") {
		const d = [g.p1[0] - g.p0[0], g.p1[1] - g.p0[1]];
		const l = Math.hypot(...d);
		const n = [-d[1] / l, d[0] / l];
		const s = (side_point[0] - g.p0[0]) * n[0] + (side_point[1] - g.p0[1]) * n[1] >= 0 ? 1 : -1;
		const o = [n[0] * distance * s, n[1] * distance * s];
		return { p0: [g.p0[0] + o[0], g.p0[1] + o[1], g.p0[2]], p1: [g.p1[0] + o[0], g.p1[1] + o[1], g.p1[2]] };
	}
	if ((entity.entity_type === "Circle" || entity.entity_type === "Arc") && is_up(g.normal)) {
		const outside = Math.hypot(side_point[0] - g.center[0], side_point[1] - g.center[1]) > g.radius;
		const r = g.radius + (outside ? distance : -distance);
		return r > 0 ? { ...g, radius: r } : null;
	}
	return null;
}

// Which way a polyline offset goes for OCCT: +d outward (closed) / right of travel (open).
export function offset_sign(entity, side_point) {
	const g = entity.geometry;
	if (g.closed) return point_in_loop(polyline_points(g.points, g.bulges, true), side_point) ? -1 : 1;
	const near = entity_nearest(entity, side_point);
	const prim = primitives(entity).prims[near.index];
	let tangent;
	if (prim.kind === "line") tangent = [prim.p1[0] - prim.p0[0], prim.p1[1] - prim.p0[1]];
	else {
		const a = prim.a0 + prim.s * near.t;
		tangent = prim.s > 0 ? [-Math.sin(a), Math.cos(a)] : [Math.sin(a), -Math.cos(a)];
	}
	const cross = tangent[0] * (side_point[1] - near.point[1]) - tangent[1] * (side_point[0] - near.point[0]);
	return cross > 0 ? -1 : 1; // left of travel = negative for OCCT
}

export function point_in_loop(points, q) {
	let inside = false;
	for (let i = 0, j = points.length - 1; i < points.length; j = i++) {
		const [xi, yi] = points[i], [xj, yj] = points[j];
		if (yi > q[1] !== yj > q[1] && q[0] < ((xj - xi) * (q[1] - yi)) / (yj - yi) + xi) inside = !inside;
	}
	return inside;
}

// ---------------------------------------------------------------------------------------- stretch

const inside_box = (p, box) => p[0] >= box[0] && p[0] <= box[2] && p[1] >= box[1] && p[1] <= box[3];

// Move every defining point inside the plan box by d (crossing-window stretch).
export function stretch(entity, box, d) {
	const g = JSON.parse(JSON.stringify(entity.geometry));
	const move = (p) => (inside_box(p, box) ? [p[0] + d[0], p[1] + d[1], p[2] + d[2]] : p);
	let changed = false;
	const touch = (p) => {
		const q = move(p);
		if (q !== p) changed = true;
		return q;
	};
	switch (entity.entity_type) {
		case "Line":
			g.p0 = touch(g.p0);
			g.p1 = touch(g.p1);
			break;
		case "Polyline":
			g.points = g.points.map(touch);
			break;
		case "Spline":
			g.control_points = g.control_points.map(touch);
			if (g.fit_points) g.fit_points = g.fit_points.map(touch);
			break;
		case "Dimension":
			for (const k of ["p1", "p2", "location", "center"]) if (g[k]) g[k] = touch(g[k]);
			break;
		case "Hatch":
			g.loops = g.loops.map((loop) => ({ ...loop, points: loop.points.map(touch) }));
			break;
		default: {
			const anchor = { Point: "p", Circle: "center", Arc: "center", Ellipse: "center", Text: "position", "Multiline Text": "position", "Block Reference": "insert", "Construction Line": "point" }[entity.entity_type];
			if (anchor) g[anchor] = touch(g[anchor]);
		}
	}
	return changed ? g : null;
}

// ---------------------------------------------------------------------------------------- explode

// An entity broken into simpler ones: [{entity_type, geometry, color?, line_type?, layer?}], or null.
export function explode(entity, blocks) {
	const g = entity.geometry;
	switch (entity.entity_type) {
		case "Polyline":
			return primitives(entity).prims.map((p) =>
				p.kind === "line"
					? { entity_type: "Line", geometry: { p0: p.p0, p1: p.p1 } }
					: { entity_type: "Arc", geometry: { center: p.c, radius: p.r, start_angle: p.s > 0 ? p.a0 : p.a0 + p.s, end_angle: p.s > 0 ? p.a0 + p.s : p.a0, normal: [0, 0, 1] } }
			);
		case "Hatch":
			return g.loops.map((loop) => ({ entity_type: "Polyline", geometry: { points: loop.points, closed: true, ...(loop.bulges ? { bulges: loop.bulges } : {}) } }));
		case "Multiline Text": {
			const lines = String(g.text || "").split("\n");
			const step = g.height * 1.667 * (g.line_spacing || 1);
			const c = Math.cos(g.rotation || 0), s = Math.sin(g.rotation || 0);
			return lines.map((text, i) => ({
				entity_type: "Text",
				geometry: { position: [g.position[0] + s * step * i, g.position[1] - c * step * i - g.height, g.position[2]], text, height: g.height, rotation: g.rotation || 0, width_factor: 1, halign: "left", valign: "baseline" },
			}));
		}
		case "Block Reference": {
			const block = blocks && blocks.get(g.block);
			if (!block) return null;
			const [sx, sy, sz] = g.scale;
			const base = block.base_point || [0, 0, 0];
			if (Math.abs(Math.abs(sx) - Math.abs(sy)) > 1e-9 * Math.abs(sx)) return explode_affine(block, g, base);
			const T = block_similarity(g, base);
			return (block.entities || []).map((e) => ({ ...e, geometry: transform_geometry(e, T) })).filter((e) => e.geometry);
		}
		default:
			return null;
	}
}

// The plan similarity of a block reference with |sx| = |sy| (sy may be negative: a mirrored reference).
export function block_similarity(g, base) {
	const [sx, sy, sz] = g.scale;
	const mirror = sx * sy < 0;
	const s = Math.abs(sx);
	const T = similarity({ angle: g.rotation || 0, scale: s });
	if (mirror) {
		// R(r)·diag(sx, sy) with sx·sy < 0 = s·R(r')·diag(1, -1), r' = r (sx > 0) or r + π (sx < 0)
		const r = (g.rotation || 0) + (sx < 0 ? Math.PI : 0);
		const c = Math.cos(r) * s, si = Math.sin(r) * s;
		T.m = [c, si, si, -c];
		T.theta = r;
		T.mirror = true;
	} else if (sx < 0) {
		// diag(-s, -s) = R(π)·s
		const r = (g.rotation || 0) + Math.PI;
		T.m = [Math.cos(r) * s, -Math.sin(r) * s, Math.sin(r) * s, Math.cos(r) * s];
		T.theta = r;
	}
	T.t = [g.insert[0] - (T.m[0] * base[0] + T.m[1] * base[1]), g.insert[1] - (T.m[2] * base[0] + T.m[3] * base[1])];
	T.sz = sz;
	T.tz = g.insert[2] - sz * base[2];
	return T;
}

function explode_affine(block, g, base) {
	// non-uniform scale: curves become polylines through their transformed points (approximation)
	const [sx, sy, sz] = g.scale;
	const c = Math.cos(g.rotation || 0), s = Math.sin(g.rotation || 0);
	const M = (p) => {
		const x = (p[0] - base[0]) * sx, y = (p[1] - base[1]) * sy;
		return [g.insert[0] + c * x - s * y, g.insert[1] + s * x + c * y, g.insert[2] + ((p[2] || 0) - base[2]) * sz];
	};
	const out = [];
	for (const e of block.entities || []) {
		const ge = e.geometry;
		if (e.entity_type === "Point") out.push({ ...e, geometry: { p: M(ge.p) } });
		else if (e.entity_type === "Line") out.push({ ...e, geometry: { p0: M(ge.p0), p1: M(ge.p1) } });
		else if (e.entity_type === "Spline") out.push({ ...e, geometry: { ...ge, control_points: ge.control_points.map(M), ...(ge.fit_points ? { fit_points: ge.fit_points.map(M) } : {}) } });
		else {
			const { prims } = primitives(e);
			if (!prims.length) continue;
			const pts = [];
			for (const p of prims) {
				const n = p.kind === "arc" ? Math.max(8, Math.ceil((Math.abs(p.s) / TAU) * 64)) : 1;
				for (let i = pts.length ? 1 : 0; i <= n; i++) pts.push(M(point_at(p, i / n)));
			}
			out.push({ ...e, entity_type: "Polyline", geometry: { points: pts, closed: false } });
		}
	}
	return out;
}

export { block_extent, spline_domain };
