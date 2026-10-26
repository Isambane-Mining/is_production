// Drawing, modify, annotation, block, measuring and solid tools. Each tool is a small state machine fed with
// points (clicks, snaps or typed coordinates), picked entities, numbers and text from the command line; it
// previews on hover and commits through the EditSession as one undoable step. Solids, and offsets of
// polylines, are made by the CAD worker (Open CASCADE) through a job.

import { interpolate } from "./bspline";
import {
	block_similarity,
	break_at,
	chamfer_lines,
	explode,
	extend,
	fillet_lines,
	join,
	offset_sign,
	offset_simple,
	polyline_corner,
	stretch,
	trim,
} from "./geom2d";
import {
	TAU,
	closed_loop,
	dimension_measurement,
	distance,
	format_number,
	format_point,
	is_closed_profile,
	loop_area,
	new_entity_id,
	similarity,
	solid_matrix,
	sweep,
	transform_geometry,
	translation,
} from "./geometry";

const DEG = 180 / Math.PI;
const RAD = Math.PI / 180;

export const TOOLS = {
	point: { label: __("Point"), group: "draw" },
	line: { label: __("Line"), group: "draw" },
	polyline: { label: __("Polyline"), group: "draw" },
	rectangle: { label: __("Rectangle"), group: "draw" },
	polygon: { label: __("Polygon"), group: "draw" },
	circle: { label: __("Circle"), group: "draw" },
	arc: { label: __("Arc (centre, start, end)"), group: "draw" },
	arc3: { label: __("Arc (3 points)"), group: "draw" },
	ellipse: { label: __("Ellipse"), group: "draw" },
	spline: { label: __("Spline"), group: "draw" },
	xline: { label: __("Construction Line"), group: "draw" },
	text: { label: __("Text"), group: "draw" },
	mtext: { label: __("Multiline Text"), group: "draw" },
	dim_aligned: { label: __("Aligned Dimension"), group: "annotate" },
	dim_linear: { label: __("Linear Dimension"), group: "annotate" },
	dim_angular: { label: __("Angular Dimension"), group: "annotate" },
	dim_radius: { label: __("Radius Dimension"), group: "annotate", picks: true },
	dim_diameter: { label: __("Diameter Dimension"), group: "annotate", picks: true },
	hatch: { label: __("Hatch"), group: "annotate", needs_selection: true },
	move: { label: __("Move"), group: "modify", needs_selection: true },
	copy: { label: __("Copy"), group: "modify", needs_selection: true },
	rotate: { label: __("Rotate"), group: "modify", needs_selection: true },
	scale: { label: __("Scale"), group: "modify", needs_selection: true },
	mirror: { label: __("Mirror"), group: "modify", needs_selection: true },
	stretch: { label: __("Stretch"), group: "modify" },
	array_rect: { label: __("Rectangular Array"), group: "modify", needs_selection: true },
	array_polar: { label: __("Polar Array"), group: "modify", needs_selection: true },
	offset: { label: __("Offset"), group: "modify", picks: true },
	trim: { label: __("Trim"), group: "modify", picks: true },
	extend: { label: __("Extend"), group: "modify", picks: true },
	break: { label: __("Break at Point"), group: "modify", picks: true },
	join: { label: __("Join"), group: "modify", needs_selection: true, immediate: true },
	explode: { label: __("Explode"), group: "modify", needs_selection: true, immediate: true },
	fillet: { label: __("Fillet"), group: "modify", picks: true },
	chamfer: { label: __("Chamfer"), group: "modify", picks: true },
	block_create: { label: __("Create Block"), group: "block", needs_selection: true },
	block_insert: { label: __("Insert Block"), group: "block" },
	box: { label: __("Box"), group: "solid", worker: true },
	cylinder: { label: __("Cylinder"), group: "solid", worker: true },
	extrude: { label: __("Extrude"), group: "solid", worker: true, needs_selection: true },
	distance: { label: __("Measure Distance"), group: "tools" },
	area: { label: __("Measure Area"), group: "tools" },
	angle: { label: __("Measure Angle"), group: "tools" },
	id: { label: __("Identify Point"), group: "tools" },
};

const PROMPTS = {
	point: [__("Point location")],
	line: [__("First point"), __("Next point (Enter to finish)")],
	polyline: [__("First point"), __("Next point (Enter to finish, C to close, A for arc segment)")],
	rectangle: [__("First corner"), __("Opposite corner")],
	polygon: [__("Centre"), __("Corner (vertex) — or a radius")],
	circle: [__("Centre"), __("Radius (point or number)")],
	arc: [__("Centre"), __("Start point"), __("End point (counter-clockwise)")],
	arc3: [__("Start point"), __("Second point"), __("End point")],
	ellipse: [__("Centre"), __("End of the major axis"), __("Minor radius (point or number)")],
	spline: [__("First fit point"), __("Next fit point (Enter to finish)")],
	xline: [__("Through point"), __("Second point (direction)")],
	text: [__("Insertion point"), __("Text")],
	mtext: [__("Top-left corner"), __("Text (\\n for a new line)")],
	dim_aligned: [__("First point"), __("Second point"), __("Dimension line location")],
	dim_linear: [__("First point"), __("Second point"), __("Dimension line location (sets horizontal/vertical)")],
	dim_angular: [__("Vertex"), __("First line point"), __("Second line point"), __("Arc location")],
	dim_radius: [__("Pick a circle or arc"), __("Text location")],
	dim_diameter: [__("Pick a circle or arc"), __("Text location")],
	hatch: [__("Pattern: SOLID, ANSI31, ANSI37, LINES, GRID or DOTS (Enter = {0})"), __("Pattern scale (Enter = {0})")],
	move: [__("Base point"), __("Destination (point, @dx,dy or distance)")],
	copy: [__("Base point"), __("Destination (point, @dx,dy or distance)")],
	rotate: [__("Base point"), __("Rotation angle (degrees, or a point)")],
	scale: [__("Base point"), __("Scale factor (number, or a point at distance 1·factor)")],
	mirror: [__("First point of the mirror line"), __("Second point (Shift: keep originals is the default)")],
	stretch: [__("First corner of the crossing window"), __("Opposite corner"), __("Base point"), __("Destination")],
	array_rect: [__("Rows × columns (e.g. 3,4)"), __("Row spacing, column spacing (e.g. 10,20)")],
	array_polar: [__("Centre"), __("Number of items (total)"), __("Angle to fill (degrees, Enter = 360)")],
	offset: [__("Offset distance (Enter = {0})"), __("Pick the object to offset"), __("Pick the side")],
	trim: [__("Pick the part to remove (cutting edges: everything visible)")],
	extend: [__("Pick near the end to extend (boundaries: everything visible)")],
	break: [__("Pick the object"), __("Break point")],
	fillet: [__("Radius (Enter = {0})"), __("Pick the first line (or a polyline corner)"), __("Pick the second line")],
	chamfer: [__("Distances d1,d2 (Enter = {0})"), __("Pick the first line (or a polyline corner)"), __("Pick the second line")],
	block_create: [__("Base point"), __("Block name")],
	block_insert: [__("Insertion point"), __("Scale (Enter = 1)"), __("Rotation in degrees (Enter = 0)")],
	box: [__("First corner"), __("Opposite corner"), __("Height")],
	cylinder: [__("Base centre"), __("Radius"), __("Height")],
	extrude: [__("Height (number)")],
	distance: [__("First point"), __("Second point")],
	area: [__("First point (or Enter to measure the selection)"), __("Next point (Enter to finish)")],
	angle: [__("Vertex"), __("First point"), __("Second point")],
	id: [__("Point")],
};

const NUMBER_FIRST = new Set(["offset", "fillet", "chamfer", "hatch", "array_rect"]);

export class ToolController {
	constructor(ctx) {
		// ctx: session, viewport, message(text, level), current_layer(), current_block(), settings, build_solids(items),
		//      offset_curves(items), selection(), select(ids), cutters(), visible_entities()
		this.ctx = ctx;
		this.name = null;
		this.points = [];
		this.values = {};
		this.picks = [];
		this.prompt = "";
		this.hover_point = null;
		this.memory = { offset: 1, fillet: 0, chamfer: [1, 1], hatch_pattern: "ANSI31", hatch_scale: 1, polygon_sides: 6, array: [2, 2], array_spacing: [10, 10] };
	}

	get active() {
		return Boolean(this.name);
	}

	get wants_pick() {
		return Boolean(this.name && TOOLS[this.name].picks && !(this.name === "offset" && this.picks.length === 1) && !(this.name === "break" && this.picks.length === 1) && !(this.name.startsWith("dim_") && this.picks.length === 1));
	}

	get last_point() {
		return this.points.length ? this.points[this.points.length - 1] : null;
	}

	start(name) {
		const tool = TOOLS[name];
		if (!tool) return false;
		this.cancel(true);
		const selection = this.ctx.selection();
		if (tool.needs_selection && !selection.length) {
			this.ctx.message(__("{0}: select objects first (click, Shift+click, or drag a window).", [tool.label]), "warning");
			return false;
		}
		if (name === "extrude" && !selection.every((id) => is_closed_profile(this.ctx.session.entities.get(id)))) {
			this.ctx.message(__("Extrude: select closed polylines with straight segments only."), "warning");
			return false;
		}
		if (name === "block_insert" && !this.ctx.current_block()) {
			this.ctx.message(__("Insert Block: choose a block in the Blocks panel first."), "warning");
			return false;
		}
		this.name = name;
		this.points = [];
		this.values = {};
		this.picks = [];
		this.step = 0;
		if (tool.immediate) {
			this[name === "join" ? "do_join" : "do_explode"]();
			return true;
		}
		this.set_prompt();
		return true;
	}

	cancel(silent = false) {
		if (this.name && !silent) this.ctx.message(__("{0} cancelled.", [TOOLS[this.name].label]));
		this.name = null;
		this.points = [];
		this.values = {};
		this.picks = [];
		this.step = 0;
		this.prompt = "";
		this.ctx.viewport.clear_preview();
	}

	set_prompt() {
		const list = PROMPTS[this.name] || [""];
		const memory = { offset: this.memory.offset, fillet: this.memory.fillet, chamfer: this.memory.chamfer.join(","), hatch: this.memory.hatch_pattern };
		let text = list[Math.min(this.step, list.length - 1)];
		if (this.name === "hatch" && this.step === 1) text = __("Pattern scale (Enter = {0})", [this.memory.hatch_scale]);
		this.prompt = `${TOOLS[this.name].label}: ${__(text, [memory[this.name] ?? ""])}`;
	}

	advance() {
		this.step += 1;
		this.set_prompt();
	}

	// Ortho / polar tracking: from the last point, keep the larger axis move or snap to the angle step.
	constrain(point) {
		const last = this.last_point;
		if (!last || !point) return point;
		if (this.ctx.settings.ortho) {
			const dx = point[0] - last[0], dy = point[1] - last[1];
			return Math.abs(dx) >= Math.abs(dy) ? [point[0], last[1], point[2]] : [last[0], point[1], point[2]];
		}
		const step = this.ctx.settings.polar ? this.ctx.settings.polar_angle * RAD : 0;
		if (step > 0) {
			const dx = point[0] - last[0], dy = point[1] - last[1];
			const d = Math.hypot(dx, dy);
			const a = Math.round(Math.atan2(dy, dx) / step) * step;
			return [last[0] + d * Math.cos(a), last[1] + d * Math.sin(a), point[2]];
		}
		return point;
	}

	hover(point, marker) {
		if (!this.name) {
			this.ctx.viewport.preview([], marker);
			return;
		}
		this.hover_point = point = this.constrain(point);
		const ghost = this.preview_entities(point);
		this.ctx.viewport.preview(this.preview_lines(point), marker, ghost, this.ctx.session);
	}

	preview_lines(p) {
		const pts = this.points;
		if (!p) return [];
		const last = this.last_point;
		switch (this.name) {
			case "line":
			case "distance":
			case "move":
			case "copy":
			case "xline":
			case "mirror":
			case "dim_aligned":
			case "dim_linear":
				return last ? [[last, p]] : [];
			case "polyline":
			case "spline":
			case "area":
				return pts.length ? [[...pts, p]] : [];
			case "rectangle":
			case "box":
			case "stretch":
				if (pts.length === 1) return [rectangle(pts[0], p)];
				if (pts.length >= 2) return [rectangle(pts[0], pts[1]), ...(pts.length === 3 ? [[pts[2], p]] : [])];
				return [];
			case "polygon":
				return pts.length === 1 ? [polygon(pts[0], p, this.memory.polygon_sides)] : [];
			case "circle":
			case "cylinder":
				if (pts.length === 1) return [circle_points(pts[0], distance(pts[0], p))];
				if (pts.length === 2) return [circle_points(pts[0], this.values.radius)];
				return [];
			case "arc":
				if (pts.length === 1) return [[pts[0], p]];
				if (pts.length === 2) return [arc_preview(pts[0], distance(pts[0], pts[1]), angle_of(pts[0], pts[1]), angle_of(pts[0], p))];
				return [];
			case "arc3": {
				if (pts.length < 2) return last ? [[last, p]] : [];
				const arc = arc_through(pts[0], pts[1], p);
				return arc ? [arc_preview(arc.center, arc.radius, arc.start_angle, arc.end_angle)] : [[pts[0], pts[1], p]];
			}
			case "ellipse":
				return pts.length ? [[pts[0], p]] : [];
			case "rotate":
			case "scale":
			case "angle":
				return pts.length ? [[pts[0], p]] : [];
			case "array_polar":
				return pts.length ? [[pts[0], p]] : [];
			default:
				return [];
		}
	}

	// ghost entities while placing (text, dimensions, blocks)
	preview_entities(p) {
		const pts = this.points;
		const h = this.ctx.settings.text_height;
		if (this.name === "dim_aligned" && pts.length === 2) return [{ entity_type: "Dimension", geometry: { kind: "aligned", p1: pts[0], p2: pts[1], location: p, text_height: h, arrow_size: h * 0.8 } }];
		if (this.name === "dim_linear" && pts.length === 2) return [{ entity_type: "Dimension", geometry: { kind: "linear", p1: pts[0], p2: pts[1], location: p, angle: linear_angle(pts[0], pts[1], p), text_height: h, arrow_size: h * 0.8 } }];
		if (this.name === "dim_angular" && pts.length === 3) return [{ entity_type: "Dimension", geometry: { kind: "angular", center: pts[0], p1: pts[1], p2: pts[2], location: p, text_height: h, arrow_size: h * 0.8 } }];
		if (this.name === "block_insert" && pts.length === 0) return [{ entity_type: "Block Reference", geometry: { block: this.ctx.current_block(), insert: p, scale: [1, 1, 1], rotation: 0 } }];
		return [];
	}

	// ---------------------------------------------------------------- input

	async input_pick(id, point) {
		const e = this.ctx.session.entities.get(id);
		if (!e) return this.ctx.message(__("Pick an object."), "warning");
		const s = this.ctx.session;
		switch (this.name) {
			case "trim": {
				const pieces = trim(e, point, this.ctx.cutters());
				if (!pieces) return this.ctx.message(__("Nothing to trim there (no cutting edges cross it)."), "warning");
				return this.replace(e, pieces, __("Trim"), false);
			}
			case "extend": {
				const g = extend(e, point, this.ctx.cutters());
				if (!g) return this.ctx.message(__("No boundary to extend to."), "warning");
				s.apply(__("Extend"), (w) => w.put({ ...e, geometry: g, validation_status: "Not Checked" }));
				return this.ctx.message(__("Extended."));
			}
			case "break":
				this.picks = [e];
				this.advance();
				return;
			case "offset": {
				this.picks = [e];
				this.advance();
				return;
			}
			case "fillet":
			case "chamfer": {
				if (e.entity_type === "Polyline" && !this.picks.length) {
					const g = polyline_corner(e, point, this.name === "fillet" ? { radius: this.memory.fillet } : { d1: this.memory.chamfer[0], d2: this.memory.chamfer[1] });
					if (g) {
						s.apply(TOOLS[this.name].label, (w) => w.put({ ...e, geometry: g, validation_status: "Not Checked" }));
						this.ctx.message(__("{0}: polyline corner done.", [TOOLS[this.name].label]));
						return;
					}
				}
				if (e.entity_type !== "Line") return this.ctx.message(__("Pick lines (or a corner of a polyline with straight sides)."), "warning");
				this.picks.push({ e, point });
				if (this.picks.length === 1) return this.advance();
				const [a, b] = this.picks;
				const result = this.name === "fillet" ? fillet_lines(a.e, b.e, a.point, b.point, this.memory.fillet) : chamfer_lines(a.e, b.e, a.point, b.point, ...this.memory.chamfer);
				this.picks = [];
				this.step = 1;
				this.set_prompt();
				if (!result) return this.ctx.message(__("These lines are parallel or the distance is too large."), "error");
				s.apply(TOOLS[this.name].label, (w) => {
					w.put({ ...a.e, geometry: result[0], validation_status: "Not Checked" });
					w.put({ ...b.e, geometry: result[1], validation_status: "Not Checked" });
					if (result[2]) w.put({ entity_id: new_entity_id(), cad_layer: a.e.cad_layer, validation_status: "Not Checked", ...result[2] });
				});
				return this.ctx.message(__("{0} done.", [TOOLS[this.name].label]));
			}
			case "dim_radius":
			case "dim_diameter": {
				if (!["Circle", "Arc"].includes(e.entity_type)) return this.ctx.message(__("Pick a circle or an arc."), "warning");
				this.picks = [e];
				this.values.at = point;
				this.advance();
				return;
			}
			default:
				return this.input_point(point);
		}
	}

	async input_point(point) {
		if (!this.name) return;
		point = this.constrain(point);
		const pts = this.points;
		const h = this.ctx.settings.text_height;
		switch (this.name) {
			case "point":
				return this.add([{ entity_type: "Point", geometry: { p: point } }], __("Point"));
			case "id":
				this.ctx.message(`${__("Point")}: ${format_point(point, this.decimals)}`, "result");
				return this.cancel(true);
			case "line":
				if (pts.length) this.add([{ entity_type: "Line", geometry: { p0: pts[pts.length - 1], p1: point } }], __("Line"), false);
				pts.push(point);
				break;
			case "polyline":
				if (pts.length) {
					this.values.bulges = this.values.bulges || [];
					this.values.bulges.push(this.values.arc_next ? tangent_bulge(pts, this.values.bulges, point) : 0);
				}
				pts.push(point);
				break;
			case "spline":
			case "area":
				pts.push(point);
				break;
			case "rectangle":
				if (pts.length === 1) return this.add([{ entity_type: "Polyline", geometry: { points: rectangle(pts[0], point).slice(0, 4), closed: true } }], __("Rectangle"));
				pts.push(point);
				break;
			case "polygon":
				if (pts.length === 1) return this.add([{ entity_type: "Polyline", geometry: { points: polygon(pts[0], point, this.memory.polygon_sides).slice(0, -1), closed: true } }], __("Polygon"));
				pts.push(point);
				this.ctx.message(__("Polygon: {0} sides (type a number first to change it).", [this.memory.polygon_sides]));
				break;
			case "circle":
				if (pts.length === 1) return this.make_circle(distance(pts[0], point));
				pts.push(point);
				break;
			case "arc":
				pts.push(point);
				if (pts.length === 3) {
					const r = distance(pts[0], pts[1]);
					if (!(r > 0)) return this.ctx.message(__("The radius must be greater than zero."), "error");
					return this.add([{ entity_type: "Arc", geometry: { center: pts[0], radius: r, start_angle: angle_of(pts[0], pts[1]), end_angle: angle_of(pts[0], pts[2]), normal: [0, 0, 1] } }], __("Arc"));
				}
				break;
			case "arc3":
				pts.push(point);
				if (pts.length === 3) {
					const arc = arc_through(...pts);
					if (!arc) return this.ctx.message(__("The three points are on one line."), "error");
					return this.add([{ entity_type: "Arc", geometry: { ...arc, center: [...arc.center.slice(0, 2), pts[0][2]], normal: [0, 0, 1] } }], __("Arc"));
				}
				break;
			case "ellipse":
				if (pts.length === 2) return this.make_ellipse(distance(pts[0], point));
				pts.push(point);
				break;
			case "xline":
				if (pts.length === 1) {
					const d = [point[0] - pts[0][0], point[1] - pts[0][1], point[2] - pts[0][2]];
					if (!Math.hypot(...d)) return this.ctx.message(__("Pick a different second point."), "warning");
					return this.add([{ entity_type: "Construction Line", geometry: { point: pts[0], direction: d, ray: false } }], __("Construction Line"));
				}
				pts.push(point);
				break;
			case "text":
			case "mtext":
				if (!pts.length) pts.push(point);
				break;
			case "dim_aligned":
			case "dim_linear":
				if (pts.length === 2) {
					const g = { kind: this.name === "dim_aligned" ? "aligned" : "linear", p1: pts[0], p2: pts[1], location: point, text_height: h, arrow_size: h * 0.8 };
					if (g.kind === "linear") g.angle = linear_angle(pts[0], pts[1], point);
					if (!(dimension_measurement(g) > 0)) return this.ctx.message(__("The dimension measures nothing."), "error");
					return this.add([{ entity_type: "Dimension", geometry: g }], TOOLS[this.name].label);
				}
				pts.push(point);
				break;
			case "dim_angular":
				if (pts.length === 3) return this.add([{ entity_type: "Dimension", geometry: { kind: "angular", center: pts[0], p1: pts[1], p2: pts[2], location: point, text_height: h, arrow_size: h * 0.8 } }], TOOLS[this.name].label);
				pts.push(point);
				break;
			case "dim_radius":
			case "dim_diameter": {
				const e = this.picks[0];
				const g = e.geometry;
				const a = Math.atan2(point[1] - g.center[1], point[0] - g.center[0]);
				const p1 = [g.center[0] + g.radius * Math.cos(a), g.center[1] + g.radius * Math.sin(a), g.center[2]];
				return this.add([{ entity_type: "Dimension", geometry: { kind: this.name === "dim_radius" ? "radius" : "diameter", center: g.center, p1, p2: p1, location: point, text_height: h, arrow_size: h * 0.8 } }], TOOLS[this.name].label);
			}
			case "move":
			case "copy":
				if (pts.length === 1) {
					const d = [point[0] - pts[0][0], point[1] - pts[0][1], point[2] - pts[0][2]];
					return this.transform_selection(translation(d), this.name === "copy", TOOLS[this.name].label);
				}
				pts.push(point);
				break;
			case "rotate":
				if (pts.length === 1) return this.transform_selection(similarity({ base: pts[0], angle: angle_of(pts[0], point) }), false, __("Rotate"));
				pts.push(point);
				break;
			case "scale":
				if (pts.length === 1) return this.transform_selection(similarity({ base: pts[0], scale: distance(pts[0], point) }), false, __("Scale"));
				pts.push(point);
				break;
			case "mirror":
				if (pts.length === 1) {
					const phi = angle_of(pts[0], point);
					return this.transform_selection(similarity({ base: pts[0], mirror: true, mirror_axis_angle: phi }), true, __("Mirror"));
				}
				pts.push(point);
				break;
			case "stretch":
				pts.push(point);
				if (pts.length === 4) return this.do_stretch();
				break;
			case "array_polar":
				if (!pts.length) pts.push(point);
				break;
			case "offset":
				if (this.picks.length === 1) return this.do_offset(this.picks[0], point);
				return this.ctx.message(__("Pick the object to offset."), "warning");
			case "break":
				if (this.picks.length === 1) {
					const pieces = break_at(this.picks[0], point);
					if (!pieces) return this.ctx.message(__("This object cannot be broken there."), "warning");
					return this.replace(this.picks[0], pieces, __("Break"));
				}
				return;
			case "block_create":
				if (!pts.length) pts.push(point);
				break;
			case "block_insert":
				if (!pts.length) {
					pts.push(point);
					this.values.scale = 1;
				}
				break;
			case "box":
				if (pts.length < 2) pts.push(point);
				break;
			case "cylinder":
				if (pts.length === 0) pts.push(point);
				else if (pts.length === 1) {
					this.values.radius = distance(pts[0], point);
					pts.push(point);
				}
				break;
			case "distance":
				if (pts.length === 1) {
					this.report_distance(pts[0], point);
					return this.cancel(true);
				}
				pts.push(point);
				break;
			case "angle":
				pts.push(point);
				if (pts.length === 3) {
					const a = sweep(angle_of(pts[0], pts[1]), angle_of(pts[0], pts[2])) * DEG;
					this.ctx.message(`${__("Angle")} ${format_number(Math.min(a, 360 - a), 4)}° (${__("counter-clockwise")} ${format_number(a, 4)}°)`, "result");
					return this.cancel(true);
				}
				break;
			default:
				break;
		}
		this.step = this.points.length;
		this.set_prompt();
		this.hover(this.hover_point, null);
	}

	async input_number(value, point = null) {
		if (!this.name) return;
		const pts = this.points;
		const n = this.name;
		if (NUMBER_FIRST.has(n) && this.step === 0) {
			if (n === "offset") this.memory.offset = Math.abs(value);
			if (n === "fillet") this.memory.fillet = Math.abs(value);
			if (n === "chamfer") this.memory.chamfer = [Math.abs(value), Math.abs(value)];
			if (n === "hatch") return this.input_text(String(value));
			if (n === "array_rect") return this.input_text(String(value));
			return this.advance();
		}
		if (n === "hatch" && this.step === 1) {
			this.memory.hatch_scale = Math.abs(value) || 1;
			return this.do_hatch();
		}
		if (n === "polygon" && pts.length === 0) {
			const sides = Math.round(value);
			if (sides < 3 || sides > 1024) return this.ctx.message(__("A polygon has 3 to 1024 sides."), "error");
			this.memory.polygon_sides = sides;
			return this.ctx.message(__("Polygon: {0} sides.", [sides]));
		}
		if (n === "polygon" && pts.length === 1) {
			const p = [pts[0][0] + value, pts[0][1], pts[0][2]];
			return this.input_point(p);
		}
		if (n === "circle" && pts.length === 1) return this.make_circle(value);
		if (n === "ellipse" && pts.length === 2) return this.make_ellipse(value);
		if (n === "cylinder" && pts.length === 1) {
			this.values.radius = value;
			pts.push(pts[0]);
			this.step = 2;
			return this.set_prompt();
		}
		if ((n === "cylinder" && pts.length === 2) || (n === "box" && pts.length === 2) || n === "extrude") return this.make_solid(value);
		if ((n === "text" || n === "mtext") && pts.length === 1 && this.values.text !== undefined) return this.make_text(value);
		if (n === "rotate" && pts.length === 1) return this.transform_selection(similarity({ base: pts[0], angle: value * RAD }), false, __("Rotate"));
		if (n === "scale" && pts.length === 1) {
			if (!(value > 0)) return this.ctx.message(__("The scale factor must be greater than zero."), "error");
			return this.transform_selection(similarity({ base: pts[0], scale: value }), false, __("Scale"));
		}
		if (n === "array_polar" && pts.length === 1 && this.values.count === undefined) {
			this.values.count = Math.round(value);
			if (this.values.count < 2) return this.ctx.message(__("At least 2 items."), "error");
			this.step = 2;
			return this.set_prompt();
		}
		if (n === "array_polar" && this.values.count !== undefined) return this.do_array_polar(value);
		if (n === "block_insert" && pts.length === 1) {
			if (this.values.scale_set) return this.make_block_ref(value * RAD);
			this.values.scale = value || 1;
			this.values.scale_set = true;
			this.step = 2;
			return this.set_prompt();
		}
		if (point) return this.input_point(point); // direct distance entry
		this.ctx.message(__("A point is expected here."), "warning");
	}

	input_text(text) {
		const n = this.name;
		if (n === "polyline" && /^c(lose)?$/i.test(text)) return this.finish(true);
		if (n === "polyline" && /^a(rc)?$/i.test(text)) {
			this.values.arc_next = this.points.length >= 2;
			return this.ctx.message(this.values.arc_next ? __("Arc segments, tangent to the previous segment (L for lines).") : __("Draw a first straight segment, then A."));
		}
		if (n === "polyline" && /^l(ine)?$/i.test(text)) {
			this.values.arc_next = false;
			return this.ctx.message(__("Straight segments."));
		}
		if ((n === "text" || n === "mtext") && this.points.length === 1) {
			this.values.text = text.replace(/\\n/g, "\n");
			this.prompt = `${TOOLS[n].label}: ${__("Height (Enter = {0})", [format_number(this.ctx.settings.text_height, 2)])}`;
			return;
		}
		if (n === "hatch" && this.step === 0) {
			const name = text.trim().toUpperCase() || this.memory.hatch_pattern;
			if (!["SOLID", "ANSI31", "ANSI37", "LINES", "GRID", "DOTS"].includes(name)) return this.ctx.message(__("Unknown pattern {0}.", [name]), "error");
			this.memory.hatch_pattern = name;
			if (name === "SOLID") return this.do_hatch();
			return this.advance();
		}
		if (n === "chamfer" && this.step === 0) {
			const parts = text.split(/[,;\s]+/).map(Number);
			if (parts.length === 2 && parts.every((v) => v >= 0)) {
				this.memory.chamfer = parts;
				return this.advance();
			}
		}
		if (n === "array_rect") {
			const parts = text.split(/[,;x×\s]+/).map(Number).filter((v) => Number.isFinite(v));
			if (this.step === 0 && parts.length === 2 && parts.every((v) => v >= 1)) {
				this.memory.array = parts.map(Math.round);
				return this.advance();
			}
			if (this.step === 1 && parts.length === 2) {
				this.memory.array_spacing = parts;
				return this.do_array_rect();
			}
			return this.ctx.message(__("Type two numbers, e.g. 3,4."), "error");
		}
		if (n === "block_create" && this.points.length === 1) return this.do_block_create(text.trim());
		this.ctx.message(__("Unknown input {0}.", [text]), "warning");
	}

	// Enter / right-click
	finish(close = false) {
		const n = this.name;
		const pts = this.points;
		if (n === "polyline") {
			if (pts.length < (close ? 3 : 2)) return this.cancel();
			const bulges = [...(this.values.bulges || []), ...(close ? [0] : [])];
			return this.add([{ entity_type: "Polyline", geometry: { points: pts, closed: close, ...(bulges.some(Boolean) ? { bulges } : {}) } }], __("Polyline"));
		}
		if (n === "spline") {
			if (pts.length < 2) return this.cancel();
			try {
				return this.add([{ entity_type: "Spline", geometry: { ...interpolate(pts, 3), closed: false } }], __("Spline"));
			} catch (e) {
				return this.ctx.message(e.message, "error");
			}
		}
		if (n === "area") {
			if (pts.length >= 3) {
				const a = Math.abs(loop_area(pts, null));
				let perimeter = 0;
				for (let i = 0; i < pts.length; i++) perimeter += Math.hypot(pts[(i + 1) % pts.length][0] - pts[i][0], pts[(i + 1) % pts.length][1] - pts[i][1]);
				this.ctx.message(`${__("Plan area")} ${format_number(a, this.decimals)} · ${__("Perimeter")} ${format_number(perimeter, this.decimals)}`, "result");
			} else this.measure_selection_area();
			return this.cancel(true);
		}
		if ((n === "text" || n === "mtext") && this.values.text !== undefined) return this.make_text(this.ctx.settings.text_height);
		if (NUMBER_FIRST.has(n) && this.step === 0) {
			if (n === "hatch") return this.input_text("");
			if (n === "array_rect") return this.ctx.message(__("Type rows,columns."), "warning");
			return this.advance();
		}
		if (n === "hatch" && this.step === 1) return this.do_hatch();
		if (n === "array_polar" && this.values.count !== undefined) return this.do_array_polar(360);
		if (n === "block_insert" && this.points.length === 1) {
			if (!this.values.scale_set) {
				this.values.scale_set = true;
				this.step = 2;
				return this.set_prompt();
			}
			return this.make_block_ref(0);
		}
		this.cancel(true);
	}

	// ---------------------------------------------------------------- results

	get decimals() {
		return this.ctx.settings.decimals;
	}

	add(rows, label, end = true) {
		const layer = this.ctx.current_layer();
		const entities = rows.map((row) => ({ entity_id: new_entity_id(), cad_layer: layer, validation_status: "Not Checked", ...row }));
		this.ctx.session.apply(label, (w) => entities.forEach((e) => w.put(e)));
		this.ctx.message(__("{0} added.", [label]));
		if (entities.length === 1 && this.ctx.on_created) this.ctx.on_created(entities[0]); // the object tool window
		if (end) {
			const name = this.name;
			this.cancel(true);
			if (["point", "line", "circle", "arc", "arc3", "rectangle", "polyline", "polygon", "ellipse", "spline", "xline", "dim_aligned", "dim_linear", "dim_angular"].includes(name)) this.start(name); // repeat
		}
		return entities;
	}

	replace(entity, pieces, label, end = true) {
		this.ctx.session.apply(label, (w) => {
			w.remove(entity.entity_id);
			pieces.forEach((piece, i) => {
				w.put({ ...entity, ...piece, entity_id: i === 0 ? entity.entity_id : new_entity_id(), validation_status: "Not Checked" });
			});
		});
		this.ctx.message(__("{0}: {1} piece(s).", [label, pieces.length]));
		if (end) this.cancel(true);
	}

	make_circle(radius) {
		if (!(radius > 0)) return this.ctx.message(__("The radius must be greater than zero."), "error");
		this.add([{ entity_type: "Circle", geometry: { center: this.points[0], radius, normal: [0, 0, 1] } }], __("Circle"));
	}

	make_ellipse(minor) {
		const [c, end] = this.points;
		const major = [end[0] - c[0], end[1] - c[1], 0];
		const a = Math.hypot(major[0], major[1]);
		if (!(a > 0) || !(minor > 0)) return this.ctx.message(__("Both axes must be longer than zero."), "error");
		const [M, ratio] = minor <= a ? [major, minor / a] : [[-major[1] * (minor / a), major[0] * (minor / a), 0], a / minor];
		this.add([{ entity_type: "Ellipse", geometry: { center: c, major_axis: M, ratio, start_param: 0, end_param: TAU, normal: [0, 0, 1] } }], __("Ellipse"));
	}

	make_text(height) {
		const text = this.values.text;
		if (!text) return this.cancel();
		const h = height > 0 ? height : this.ctx.settings.text_height;
		if (this.name === "mtext") return this.add([{ entity_type: "Multiline Text", geometry: { position: this.points[0], text, height: h, rotation: 0, width: 0, attachment: 1, line_spacing: 1 } }], __("Multiline Text"));
		this.add([{ entity_type: "Text", geometry: { position: this.points[0], text, height: h, rotation: 0, width_factor: 1, halign: "left", valign: "baseline" } }], __("Text"));
	}

	make_block_ref(rotation) {
		const s = this.values.scale || 1;
		this.add([{ entity_type: "Block Reference", geometry: { block: this.ctx.current_block(), insert: this.points[0], scale: [s, s, s], rotation } }], __("Block reference"));
	}

	report_distance(p, q) {
		const d = this.decimals;
		const dx = q[0] - p[0], dy = q[1] - p[1], dz = q[2] - p[2];
		const plan = Math.hypot(dx, dy);
		const bearing = (Math.atan2(dx, dy) * DEG + 360) % 360;
		const grade = plan > 0 ? (dz / plan) * 100 : 0;
		this.ctx.message(
			`${__("Distance")} ${format_number(distance(p, q), d)} · ${__("Plan")} ${format_number(plan, d)} · ΔX ${format_number(dx, d)} ΔY ${format_number(dy, d)} ΔZ ${format_number(dz, d)} · ${__("Bearing")} ${format_number(bearing, 4)}° · ${__("Grade")} ${format_number(grade, 2)}%`,
			"result"
		);
	}

	measure_selection_area() {
		const loops = this.ctx.selection().map((id) => closed_loop(this.ctx.session.entities.get(id))).filter(Boolean);
		if (!loops.length) return this.ctx.message(__("Click points, or select closed objects first."), "warning");
		const total = loops.reduce((sum, l) => sum + Math.abs(loop_area(l.points, l.bulges)), 0);
		this.ctx.message(`${__("Plan area")} ${format_number(total, this.decimals)} (${loops.length} ${__("closed object(s)")}; ${__("checked by the CAD worker after Save")})`, "result");
	}

	// Move/copy/rotate/scale/mirror the selection; solids through the CAD worker.
	async transform_selection(T, copy, label) {
		const s = this.ctx.session;
		const ids = this.ctx.selection().filter((id) => s.entities.has(id));
		const locked = ids.filter((id) => s.is_locked(s.entities.get(id)));
		if (locked.length && !copy) {
			this.cancel(true);
			return this.ctx.message(__("{0} object(s) are on locked layers.", [locked.length]), "error");
		}
		this.cancel(true);
		const moved = await this.transform_solids(ids, T);
		if (moved === null) return;
		const created = [];
		s.apply(label, (w) => {
			for (const id of ids) {
				const next = this.transformed(s.entities.get(id), T, moved, copy);
				if (!next) continue;
				w.put(next);
				created.push(next.entity_id);
			}
		});
		this.ctx.message(__("{0}: {1} object(s).", [label, created.length]));
		if (copy) this.ctx.select(created);
	}

	transformed(e, T, moved, copy) {
		const next = { ...e, entity_id: copy ? new_entity_id() : e.entity_id }; // copies stay on their layer
		if (e.entity_type === "Solid") {
			const r = moved[e.entity_id];
			if (!r) return null;
			return { ...next, ...solid_fields(r, e.geometry) };
		}
		const g = transform_geometry(e, T);
		return g ? { ...next, geometry: g, validation_status: "Not Checked" } : null;
	}

	async transform_solids(ids, T) {
		const solids = ids.filter((id) => this.ctx.session.entities.get(id).entity_type === "Solid");
		if (!solids.length) return {};
		const items = solids.map((id) => ({ key: id, op: "transform", geometry_ref: this.ctx.session.entities.get(id).geometry_ref, matrix: solid_matrix(T) }));
		const results = await this.ctx.build_solids(items);
		if (!results) return null;
		return Object.fromEntries(results.filter((r) => r.geometry_ref).map((r) => [r.key, r]));
	}

	async do_array_rect() {
		const [rows, cols] = this.memory.array, [dy, dx] = this.memory.array_spacing;
		const s = this.ctx.session;
		const ids = this.ctx.selection().filter((id) => s.entities.has(id));
		this.cancel(true);
		const copies = [];
		for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) if (r || c) copies.push(translation([c * dx, r * dy, 0]));
		await this.place_copies(ids, copies, __("Rectangular Array"));
	}

	async do_array_polar(total_deg) {
		const count = this.values.count, center = this.points[0];
		const s = this.ctx.session;
		const ids = this.ctx.selection().filter((id) => s.entities.has(id));
		this.cancel(true);
		const full = Math.abs(total_deg) >= 360 - 1e-9;
		const step = (total_deg * RAD) / (full ? count : count - 1);
		const copies = [];
		for (let k = 1; k < count; k++) copies.push(similarity({ base: center, angle: step * k }));
		await this.place_copies(ids, copies, __("Polar Array"));
	}

	async place_copies(ids, transforms, label) {
		const s = this.ctx.session;
		const results = [];
		for (const T of transforms) {
			const moved = await this.transform_solids(ids, T);
			if (moved === null) return;
			results.push([T, moved]);
		}
		let count = 0;
		s.apply(label, (w) => {
			for (const [T, moved] of results)
				for (const id of ids) {
					const next = this.transformed(s.entities.get(id), T, moved, true);
					if (next) {
						w.put(next);
						count++;
					}
				}
		});
		this.ctx.message(__("{0}: {1} new object(s).", [label, count]));
	}

	do_stretch() {
		const [a, b, base, dest] = this.points;
		const box = [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])];
		const d = [dest[0] - base[0], dest[1] - base[1], dest[2] - base[2]];
		const s = this.ctx.session;
		this.cancel(true);
		let count = 0;
		s.apply(__("Stretch"), (w) => {
			for (const e of this.ctx.visible_entities()) {
				if (s.is_locked(e) || e.entity_type === "Solid") continue;
				const g = stretch(e, box, d);
				if (g) {
					w.put({ ...e, geometry: g, validation_status: "Not Checked" });
					count++;
				}
			}
		});
		this.ctx.message(__("Stretch: {0} object(s).", [count]));
	}

	async do_offset(e, side) {
		const distance_value = this.memory.offset;
		const s = this.ctx.session;
		const simple = offset_simple(e, distance_value, side);
		if (simple) {
			s.apply(__("Offset"), (w) => w.put({ ...e, entity_id: new_entity_id(), geometry: simple, validation_status: "Not Checked" }));
			this.picks = [];
			this.step = 1;
			this.set_prompt();
			return this.ctx.message(__("Offset by {0}. Pick the next object, or Esc.", [format_number(distance_value, this.decimals)]));
		}
		if (e.entity_type !== "Polyline") return this.ctx.message(__("{0} cannot be offset yet.", [__(e.entity_type)]), "warning");
		const g = e.geometry;
		const signed = distance_value * offset_sign(e, side);
		const results = await this.ctx.offset_curves([{ key: e.entity_id, points: g.points, bulges: g.bulges || null, closed: g.closed, distance: signed, join: "intersection" }]);
		this.picks = [];
		this.step = 1;
		this.set_prompt();
		if (!results) return;
		const item = results[0];
		if (item.error) return this.ctx.message(`${__("Offset")}: ${item.error}`, "error");
		s.apply(__("Offset"), (w) => {
			for (const pl of item.polylines) w.put({ ...e, entity_id: new_entity_id(), geometry: { points: pl.points, closed: pl.closed, ...(pl.bulges ? { bulges: pl.bulges } : {}) }, validation_status: "Not Checked" });
		});
		this.ctx.message(__("Offset by {0} with Open CASCADE: {1} polyline(s).", [format_number(distance_value, this.decimals), item.polylines.length]));
	}

	do_hatch() {
		const s = this.ctx.session;
		const loops = this.ctx.selection().map((id) => closed_loop(s.entities.get(id))).filter(Boolean);
		if (!loops.length) {
			this.cancel(true);
			return this.ctx.message(__("Hatch: select closed polylines or circles first."), "warning");
		}
		// the largest loop is the outer boundary, the others are holes
		loops.sort((a, b) => Math.abs(loop_area(b.points, b.bulges)) - Math.abs(loop_area(a.points, a.bulges)));
		const geometry = { loops: loops.map((l) => ({ points: l.points, ...(l.bulges ? { bulges: l.bulges } : {}) })), pattern: this.memory.hatch_pattern, scale: this.memory.hatch_scale, angle: 0 };
		this.add([{ entity_type: "Hatch", geometry }], __("Hatch"));
	}

	do_join() {
		const s = this.ctx.session;
		const entities = this.ctx.selection().map((id) => s.entities.get(id)).filter(Boolean);
		const joined = entities.length > 1 ? join(entities) : null;
		this.cancel(true);
		if (!joined) return this.ctx.message(__("Join: select lines, arcs and open polylines that meet end to end."), "warning");
		const keep = entities[0];
		s.apply(__("Join"), (w) => {
			entities.slice(1).forEach((e) => w.remove(e.entity_id));
			w.put({ ...keep, ...joined, validation_status: "Not Checked" });
		});
		this.ctx.select([keep.entity_id]);
		this.ctx.message(__("Joined {0} objects into a polyline.", [entities.length]));
	}

	do_explode() {
		const s = this.ctx.session;
		const entities = this.ctx.selection().map((id) => s.entities.get(id)).filter(Boolean);
		this.cancel(true);
		let count = 0, skipped = 0;
		const created = [];
		s.apply(__("Explode"), (w) => {
			for (const e of entities) {
				const parts = explode(e, this.ctx.viewport.blocks);
				if (!parts || !parts.length || s.is_locked(e)) {
					skipped++;
					continue;
				}
				w.remove(e.entity_id);
				for (const p of parts) {
					const id = new_entity_id();
					const layer = p.layer && p.layer !== "0" ? (s.layer_by_name(p.layer) || {}).name || e.cad_layer : e.cad_layer;
					w.put({ entity_id: id, entity_type: p.entity_type, geometry: p.geometry, cad_layer: layer, color: p.color || e.color || null, line_type: p.line_type || null, validation_status: "Not Checked" });
					created.push(id);
				}
				count++;
			}
		});
		this.ctx.select(created);
		this.ctx.message(__("Exploded {0} object(s); {1} could not be exploded.", [count, skipped]));
	}

	do_block_create(name) {
		if (!name) return this.ctx.message(__("Type a block name."), "warning");
		if (this.ctx.viewport.blocks.has(name)) return this.ctx.message(__("Block {0} already exists.", [name]), "error");
		const s = this.ctx.session;
		const base = this.points[0];
		const entities = this.ctx.selection().map((id) => s.entities.get(id)).filter(Boolean);
		if (entities.some((e) => e.entity_type === "Solid")) {
			this.cancel(true);
			return this.ctx.message(__("Solids cannot be part of a block."), "error");
		}
		const T = translation([-base[0], -base[1], -base[2]]);
		const content = entities.map((e) => {
			const layer = s.layer(e.cad_layer) || s.layer_by_name(e.cad_layer);
			return { entity_type: e.entity_type, geometry: transform_geometry(e, T), layer: layer ? layer.layer_name : "0", color: e.color || null, line_type: e.line_type || null };
		});
		const block = { block_name: name, base_point: [0, 0, 0], entities: content };
		this.ctx.define_block(block);
		this.cancel(true);
		s.apply(__("Create Block"), (w) => {
			entities.forEach((e) => w.remove(e.entity_id));
			w.put({ entity_id: new_entity_id(), entity_type: "Block Reference", cad_layer: this.ctx.current_layer(), validation_status: "Not Checked", geometry: { block: name, insert: base, scale: [1, 1, 1], rotation: 0 } });
		});
		this.ctx.message(__("Block {0} created from {1} object(s) and placed at its base point.", [name, entities.length]));
	}

	async make_solid(height) {
		if (!(Math.abs(height) > 0)) return this.ctx.message(__("The height must not be zero."), "error");
		const pts = this.points;
		let items;
		if (this.name === "box") {
			const [a, b] = pts;
			const origin = [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.min(a[2], a[2] + height)];
			items = [{ key: "box", op: "box", origin, size: [Math.abs(b[0] - a[0]), Math.abs(b[1] - a[1]), Math.abs(height)] }];
		} else if (this.name === "cylinder") {
			const base = height > 0 ? pts[0] : [pts[0][0], pts[0][1], pts[0][2] + height];
			items = [{ key: "cylinder", op: "cylinder", base, axis: [0, 0, 1], radius: this.values.radius, height: Math.abs(height) }];
		} else {
			items = this.ctx.selection().map((id) => ({ key: id, op: "extrude", profile: this.ctx.session.entities.get(id).geometry.points, vector: [0, 0, height] }));
		}
		const label = TOOLS[this.name].label;
		this.cancel(true);
		const results = await this.ctx.build_solids(items);
		if (!results) return;
		const ok = results.filter((r) => r.geometry_ref);
		for (const r of results.filter((r) => !r.geometry_ref)) this.ctx.message(`${label}: ${r.message || __("invalid geometry")}`, "error");
		if (!ok.length) return;
		const layer = this.ctx.current_layer();
		const entities = ok.map((r) => ({ entity_id: new_entity_id(), entity_type: "Solid", cad_layer: layer, ...solid_fields(r, { source: r.source }) }));
		this.ctx.session.apply(label, (w) => entities.forEach((e) => w.put(e)));
		this.ctx.message(__("{0}: volume {1}.", [label, ok.map((r) => format_number(r.volume, this.decimals)).join(", ")]), "result");
	}
}

// bulge of an arc from the last vertex to p, tangent to the previous segment
function tangent_bulge(pts, bulges, p) {
	const n = pts.length;
	const a = pts[n - 2], b = pts[n - 1];
	let t = Math.atan2(b[1] - a[1], b[0] - a[0]);
	const prev = bulges[n - 2] || 0;
	if (prev) t += 2 * Math.atan(prev); // end tangent of an arc = chord direction turned by half its sweep
	const c = Math.atan2(p[1] - b[1], p[0] - b[0]);
	let half = c - t;
	half = Math.atan2(Math.sin(half), Math.cos(half));
	return Math.tan((2 * half) / 4);
}

function solid_fields(result, geometry) {
	return {
		geometry: { source: result.source || (geometry && geometry.source) },
		geometry_ref: result.geometry_ref,
		bbox: result.bbox,
		volume: result.volume,
		area: result.area,
		validation_status: result.valid ? "Valid" : "Invalid",
	};
}

function rectangle(a, b) {
	return [a, [b[0], a[1], a[2]], [b[0], b[1], a[2]], [a[0], b[1], a[2]], a];
}

function polygon(center, corner, sides) {
	const r = Math.hypot(corner[0] - center[0], corner[1] - center[1]);
	const a0 = Math.atan2(corner[1] - center[1], corner[0] - center[0]);
	const out = [];
	for (let i = 0; i <= sides; i++) out.push([center[0] + r * Math.cos(a0 + (TAU * i) / sides), center[1] + r * Math.sin(a0 + (TAU * i) / sides), center[2]]);
	return out;
}

function angle_of(c, p) {
	const a = Math.atan2(p[1] - c[1], p[0] - c[0]);
	return a < 0 ? a + TAU : a;
}

// a linear dimension is horizontal or vertical, whichever the location is further out along
function linear_angle(p1, p2, location) {
	const mx = (p1[0] + p2[0]) / 2, my = (p1[1] + p2[1]) / 2;
	const out_x = Math.abs(location[0] - mx) - Math.abs(p2[0] - p1[0]) / 2;
	const out_y = Math.abs(location[1] - my) - Math.abs(p2[1] - p1[1]) / 2;
	return out_y >= out_x ? 0 : Math.PI / 2;
}

// circle through three points → arc from the first to the third through the second
export function arc_through(a, b, c) {
	const d = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]));
	if (Math.abs(d) < 1e-12) return null;
	const sq = (p) => p[0] * p[0] + p[1] * p[1];
	const ux = (sq(a) * (b[1] - c[1]) + sq(b) * (c[1] - a[1]) + sq(c) * (a[1] - b[1])) / d;
	const uy = (sq(a) * (c[0] - b[0]) + sq(b) * (a[0] - c[0]) + sq(c) * (b[0] - a[0])) / d;
	const center = [ux, uy, a[2]];
	const radius = Math.hypot(a[0] - ux, a[1] - uy);
	const aa = angle_of(center, a), ab = angle_of(center, b), ac = angle_of(center, c);
	// counter-clockwise from a to c passes b? otherwise the arc runs from c to a
	return sweep(aa, ab) < sweep(aa, ac) ? { center, radius, start_angle: aa, end_angle: ac } : { center, radius, start_angle: ac, end_angle: aa };
}

function circle_points(c, r, n = 96) {
	const out = [];
	for (let i = 0; i <= n; i++) out.push([c[0] + r * Math.cos((TAU * i) / n), c[1] + r * Math.sin((TAU * i) / n), c[2]]);
	return out;
}

function arc_preview(c, r, a0, a1, n = 64) {
	if (a1 < a0) a1 += TAU;
	const out = [];
	for (let i = 0; i <= n; i++) {
		const a = a0 + ((a1 - a0) * i) / n;
		out.push([c[0] + r * Math.cos(a), c[1] + r * Math.sin(a), c[2]]);
	}
	return out;
}

export { block_similarity };
