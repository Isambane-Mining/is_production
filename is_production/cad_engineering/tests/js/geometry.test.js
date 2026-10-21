// Unit tests of the CAD Workstation's browser geometry (plan operations, transforms, splines).
// Run: node is_production/cad_engineering/tests/js/run.js   (bundles with esbuild, then runs with Node)

import assert from "node:assert/strict";

globalThis.__ = (text, args) => String(text).replace(/\{(\d+)\}/g, (m, i) => (args && args[i] !== undefined ? args[i] : m));

const { interpolate, evaluate, domain } = await import("../../../public/js/cad_workstation/bspline.js");
const G = await import("../../../public/js/cad_workstation/geometry.js");
const P = await import("../../../public/js/cad_workstation/geom2d.js");
const { arc_through } = await import("../../../public/js/cad_workstation/tools.js");

const close = (a, b, tol = 1e-9, msg = "") => assert.ok(Math.abs(a - b) <= tol, `${msg} ${a} ≠ ${b}`);
const near = (p, q, tol = 1e-9, msg = "") => [0, 1, 2].forEach((k) => close(p[k] ?? 0, q[k] ?? 0, tol, `${msg}[${k}]`));
const line = (id, p0, p1) => ({ entity_id: id, entity_type: "Line", geometry: { p0, p1 } });
const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test("line-line, line-arc and arc-arc intersections", () => {
	const x = P.intersect({ kind: "line", p0: [0, 0, 0], p1: [10, 0, 0] }, { kind: "line", p0: [5, -5, 0], p1: [5, 5, 0] });
	near(x[0].point, [5, 0, 0]);
	const circle = { kind: "arc", c: [0, 0, 0], r: 5, a0: 0, s: G.TAU, z: 0, full: true };
	const y = P.intersect({ kind: "line", p0: [-10, 3, 0], p1: [10, 3, 0] }, circle);
	assert.equal(y.length, 2);
	near(y[0].point, [-4, 3, 0]);
	near(y[1].point, [4, 3, 0]);
	const z = P.intersect(circle, { kind: "arc", c: [8, 0, 0], r: 5, a0: 0, s: G.TAU, z: 0, full: true });
	assert.equal(z.length, 2);
	close(z[0].point[0], 4);
	close(Math.abs(z[0].point[1]), 3);
});

test("trim removes the piece between the nearest cutting edges", () => {
	const target = line("T", [0, 0, 0], [10, 0, 0]);
	const cutters = [line("A", [3, -1, 0], [3, 1, 0]), line("B", [7, -1, 0], [7, 1, 0])];
	const pieces = P.trim(target, [5, 0.1, 0], [target, ...cutters]);
	assert.equal(pieces.length, 2);
	near(pieces[0].geometry.p1, [3, 0, 0]);
	near(pieces[1].geometry.p0, [7, 0, 0]);
	const end = P.trim(target, [9, 0, 0], [target, ...cutters]);
	assert.equal(end.length, 1);
	near(end[0].geometry.p1, [7, 0, 0]);
});

test("trimming a circle leaves the opposite arc", () => {
	const circle = { entity_id: "C", entity_type: "Circle", geometry: { center: [0, 0, 0], radius: 5, normal: [0, 0, 1] } };
	const pieces = P.trim(circle, [5, 0, 0], [circle, line("L", [3, -10, 0], [3, 10, 0])]);
	assert.equal(pieces[0].entity_type, "Arc");
	const g = pieces[0].geometry;
	const keep = G.sweep(g.start_angle, g.end_angle);
	close(keep, G.TAU - 2 * Math.acos(3 / 5), 1e-9, "kept sweep");
	near(G.arc_points(g.center, g.radius, g.start_angle, g.end_angle, [0, 0, 0 + 1], 2)[1], [-5, 0, 0], 1e-9, "midpoint on the far side");
});

test("extend a line and an arc to a boundary", () => {
	const l = line("L", [0, 0, 0], [5, 0, 0]);
	const g = P.extend(l, [4.9, 0, 0], [l, line("B", [8, -1, 0], [8, 1, 0])]);
	near(g.p1, [8, 0, 0]);
	const arc = { entity_id: "A", entity_type: "Arc", geometry: { center: [0, 0, 0], radius: 5, start_angle: 0, end_angle: Math.PI / 4, normal: [0, 0, 1] } };
	const h = P.extend(arc, [3.5, 3.5, 0], [arc, line("B", [0, 0, 0], [0, 10, 0])]);
	close(h.end_angle, Math.PI / 2);
});

test("fillet two lines: tangent points, centre and a 90° arc", () => {
	const [a, b, arc] = P.fillet_lines(line("A", [0, 0, 0], [10, 0, 0]), line("B", [10, 0, 0], [10, 10, 0]), [2, 0, 0], [10, 8, 0], 2);
	near(a.p0, [8, 0, 0]);
	near(b.p0, [10, 2, 0]);
	near(arc.geometry.center, [8, 2, 0]);
	close(G.sweep(arc.geometry.start_angle, arc.geometry.end_angle), Math.PI / 2);
	const [c] = P.fillet_lines(line("A", [0, 0, 0], [5, 0, 0]), line("B", [10, 1, 0], [10, 10, 0]), [2, 0, 0], [10, 8, 0], 0);
	near(c.p0, [10, 0, 0], 1e-9, "radius 0 meets at the corner");
});

test("polyline corner fillet bulges the way the path turns", () => {
	const pl = { entity_id: "P", entity_type: "Polyline", geometry: { points: [[0, 0, 0], [10, 0, 0], [10, 10, 0]], closed: false } };
	const g = P.polyline_corner(pl, [10, 0, 0], { radius: 2 });
	assert.equal(g.points.length, 4);
	close(g.bulges[1], Math.tan(Math.PI / 8), 1e-12, "left turn = positive quarter-circle bulge");
	const right = P.polyline_corner({ ...pl, geometry: { ...pl.geometry, points: [[0, 0, 0], [10, 0, 0], [10, -10, 0]] } }, [10, 0, 0], { radius: 2 });
	close(right.bulges[1], -Math.tan(Math.PI / 8), 1e-12);
});

test("join lines and an arc into one polyline with a bulge", () => {
	const arc = { entity_id: "A", entity_type: "Arc", geometry: { center: [10, 5, 0], radius: 5, start_angle: -Math.PI / 2, end_angle: Math.PI / 2, normal: [0, 0, 1] } };
	const j = P.join([line("1", [0, 0, 0], [10, 0, 0]), arc, line("2", [10, 10, 0], [0, 10, 0])]);
	assert.equal(j.geometry.points.length, 4);
	close(j.geometry.bulges[1], 1, 1e-12, "semicircle");
	close(Math.abs(G.loop_area([...j.geometry.points], [...j.geometry.bulges, 0])), 100 + (Math.PI * 25) / 2, 1e-9, "joined area");
});

test("explode a bulged polyline into lines and an exact arc", () => {
	const pl = { entity_id: "P", entity_type: "Polyline", geometry: { points: [[0, 0, 0], [2, 0, 0], [2, 2, 0]], closed: false, bulges: [1, 0] } };
	const parts = P.explode(pl);
	assert.equal(parts[0].entity_type, "Arc");
	const pts = G.arc_points(parts[0].geometry.center, parts[0].geometry.radius, parts[0].geometry.start_angle, parts[0].geometry.end_angle, [0, 0, 1], 2);
	near(pts[1], [1, -1, 0], 1e-9, "positive bulge swings right of an eastward chord");
});

test("mirrored and rotated block references explode onto the drawn points", () => {
	const block = { block_name: "B", base_point: [1, 0, 0], entities: [line("x", [1, 0, 0], [3, 1, 0]), { entity_type: "Arc", geometry: { center: [1, 0, 0], radius: 1, start_angle: 0, end_angle: Math.PI / 2, normal: [0, 0, 1] } }] };
	for (const scale of [[2, 2, 1], [2, -2, 1], [-2, 2, 1], [-2, -2, 1]]) {
		const ref = { insert: [100, 50, 7], scale, rotation: 0.3, block: "B" };
		const M = G.block_matrix(ref, block.base_point);
		const parts = P.explode({ entity_type: "Block Reference", geometry: ref }, new Map([["B", block]]));
		near(parts[0].geometry.p1, M([3, 1, 0]), 1e-9, `line ${scale}`);
		const a = parts[1].geometry;
		const ends = G.arc_points(a.center, a.radius, a.start_angle, a.end_angle, [0, 0, 1], 2);
		const expected = [M([2, 0, 0]), M([1, 1, 0])];
		const got = [ends[0], ends[2]];
		const match = (u, v) => Math.hypot(u[0] - v[0], u[1] - v[1]) < 1e-9;
		assert.ok((match(got[0], expected[0]) && match(got[1], expected[1])) || (match(got[0], expected[1]) && match(got[1], expected[0])), `arc ends ${scale}`);
		near(G.arc_points(a.center, a.radius, a.start_angle, a.end_angle, [0, 0, 1], 2)[1], M([1 + Math.SQRT1_2, Math.SQRT1_2, 0]), 1e-9, `arc middle ${scale}`);
	}
});

test("similarity transforms keep circles and arcs exact (rotate, scale, mirror)", () => {
	const arc = { entity_type: "Arc", geometry: { center: [1, 1, 0], radius: 2, start_angle: 0, end_angle: Math.PI / 2, normal: [0, 0, 1] } };
	for (const T of [G.similarity({ base: [5, 5, 0], angle: 0.7, scale: 1.5 }), G.similarity({ base: [5, 5, 0], mirror: true, mirror_axis_angle: 0.4 })]) {
		const g = G.transform_geometry(arc, T);
		const before = G.arc_points(arc.geometry.center, 2, 0, Math.PI / 2, [0, 0, 1], 2).map((p) => G.apply_point(T, p));
		const after = G.arc_points(g.center, g.radius, g.start_angle, g.end_angle, [0, 0, 1], 2);
		near(after[1], before[1], 1e-9, "middle");
		near(T.mirror ? after[0] : after[0], T.mirror ? before[2] : before[0], 1e-9, "ends");
	}
});

test("spline interpolation passes through its fit points", () => {
	const fit = [[0, 0, 0], [3, 4, 0], [7, 3, 1], [10, 6, 2], [12, 0, 0]];
	const g = interpolate(fit, 3);
	assert.equal(g.knots.length, g.control_points.length + g.degree + 1);
	const [u0, u1] = domain(g);
	near(evaluate(g, u0), fit[0], 1e-9);
	near(evaluate(g, u1), fit[4], 1e-9);
	// interior fit points lie on the curve: find the nearest sample
	for (const q of fit.slice(1, -1)) {
		let best = Infinity;
		for (let i = 0; i <= 4000; i++) best = Math.min(best, G.distance(evaluate(g, u0 + ((u1 - u0) * i) / 4000), q));
		assert.ok(best < 2e-3, `fit point ${q} off by ${best}`);
	}
});

test("three-point arcs and bulge areas", () => {
	const a = arc_through([1, 0, 0], [0, 1, 0], [-1, 0, 0]);
	near(a.center, [0, 0, 0]);
	close(G.sweep(a.start_angle, a.end_angle), Math.PI);
	const b = arc_through([-1, 0, 0], [0, -1, 0], [1, 0, 0]);
	near(G.arc_points(b.center, b.radius, b.start_angle, b.end_angle, [0, 0, 1], 2)[1], [0, -1, 0]);
	close(G.loop_area([[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]], [0, 0, Math.tan(Math.PI / 8), 0]), 100 + 25 * (Math.PI / 2 - 1), 1e-9);
});

test("break a closed polyline and a line", () => {
	const sq = { entity_id: "S", entity_type: "Polyline", geometry: { points: [[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]], closed: true } };
	const [open] = P.break_at(sq, [5, 0, 0]);
	assert.equal(open.geometry.closed, false);
	near(open.geometry.points[0], [5, 0, 0]);
	near(open.geometry.points[open.geometry.points.length - 1], [5, 0, 0]);
	const parts = P.break_at(line("L", [0, 0, 0], [10, 0, 0]), [4, 0.2, 0]);
	near(parts[0].geometry.p1, [4, 0, 0]);
});

let failed = 0;
for (const [name, fn] of tests) {
	try {
		await fn();
		console.log(`ok   ${name}`);
	} catch (e) {
		failed++;
		console.log(`FAIL ${name}\n     ${e.message}`);
	}
}
console.log(`${tests.length - failed}/${tests.length} passed`);
process.exit(failed ? 1 : 0);
