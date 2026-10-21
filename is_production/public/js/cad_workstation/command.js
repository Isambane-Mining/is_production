// Command-line input: coordinates and numbers as typed in a CAD command line.
//
//   x,y  or  x,y,z      absolute coordinates (model units)
//   @dx,dy[,dz]         relative to the last point
//   @distance<angle     polar, relative to the last point (angle in degrees, counter-clockwise from +X)
//   number              a distance, radius or height; for points, "direct distance" along the cursor
//
// Returns {kind: "point", point} | {kind: "number", value} | {kind: "text", text} | {kind: "error", message}.

const NUMBER = "[-+]?(?:\\d+\\.?\\d*|\\.\\d+)(?:[eE][-+]?\\d+)?";
const ABSOLUTE = new RegExp(`^(${NUMBER})\\s*[,;\\s]\\s*(${NUMBER})(?:\\s*[,;\\s]\\s*(${NUMBER}))?$`);
const RELATIVE = new RegExp(`^@\\s*(${NUMBER})\\s*[,;]\\s*(${NUMBER})(?:\\s*[,;]\\s*(${NUMBER}))?$`);
const POLAR = new RegExp(`^@\\s*(${NUMBER})\\s*<\\s*(${NUMBER})$`);
const SINGLE = new RegExp(`^(${NUMBER})$`);

export function parse_input(text, { last = null, cursor = null, elevation = 0 } = {}) {
	const value = String(text || "").trim();
	if (!value) return { kind: "empty" };
	let m = value.match(RELATIVE);
	if (m) {
		if (!last) return { kind: "error", message: __("There is no last point for @ input yet.") };
		return { kind: "point", point: [last[0] + +m[1], last[1] + +m[2], last[2] + (m[3] !== undefined ? +m[3] : 0)] };
	}
	m = value.match(POLAR);
	if (m) {
		if (!last) return { kind: "error", message: __("There is no last point for @ input yet.") };
		const d = +m[1], a = (+m[2] * Math.PI) / 180;
		return { kind: "point", point: [last[0] + d * Math.cos(a), last[1] + d * Math.sin(a), last[2]] };
	}
	m = value.match(ABSOLUTE);
	if (m) return { kind: "point", point: [+m[1], +m[2], m[3] !== undefined ? +m[3] : elevation] };
	m = value.match(SINGLE);
	if (m) {
		const number = +m[1];
		// direct distance entry: along the line from the last point towards the cursor
		if (last && cursor) {
			const dx = cursor[0] - last[0], dy = cursor[1] - last[1], dz = cursor[2] - last[2];
			const length = Math.hypot(dx, dy, dz);
			if (length > 0) return { kind: "number", value: number, point: [last[0] + (dx / length) * number, last[1] + (dy / length) * number, last[2] + (dz / length) * number] };
		}
		return { kind: "number", value: number };
	}
	return { kind: "text", text: value };
}

// Command aliases (AutoCAD-style where they exist).
export const ALIASES = {
	L: "line",
	LINE: "line",
	PL: "polyline",
	PLINE: "polyline",
	POLYLINE: "polyline",
	REC: "rectangle",
	RECTANGLE: "rectangle",
	POL: "polygon",
	POLYGON: "polygon",
	C: "circle",
	CIRCLE: "circle",
	A: "arc",
	ARC: "arc",
	A3: "arc3",
	EL: "ellipse",
	ELLIPSE: "ellipse",
	SPL: "spline",
	SPLINE: "spline",
	XL: "xline",
	XLINE: "xline",
	PO: "point",
	POINT: "point",
	T: "text",
	DT: "text",
	TEXT: "text",
	MT: "mtext",
	MTEXT: "mtext",
	DAL: "dim_aligned",
	DLI: "dim_linear",
	DAN: "dim_angular",
	DRA: "dim_radius",
	DDI: "dim_diameter",
	H: "hatch",
	HATCH: "hatch",
	M: "move",
	MOVE: "move",
	CO: "copy",
	CP: "copy",
	COPY: "copy",
	RO: "rotate",
	ROTATE: "rotate",
	SC: "scale",
	SCALE: "scale",
	MI: "mirror",
	MIRROR: "mirror",
	S: "stretch",
	STRETCH: "stretch",
	AR: "array_rect",
	ARRAY: "array_rect",
	ARRAYPOLAR: "array_polar",
	O: "offset",
	OFFSET: "offset",
	TR: "trim",
	TRIM: "trim",
	EX: "extend",
	EXTEND: "extend",
	BR: "break",
	BREAK: "break",
	J: "join",
	JOIN: "join",
	X: "explode",
	EXPLODE: "explode",
	F: "fillet",
	FILLET: "fillet",
	CHA: "chamfer",
	CHAMFER: "chamfer",
	B: "block_create",
	BLOCK: "block_create",
	I: "block_insert",
	INSERT: "block_insert",
	E: "erase",
	ERASE: "erase",
	DEL: "erase",
	BOX: "box",
	CYL: "cylinder",
	CYLINDER: "cylinder",
	EXT: "extrude",
	EXTRUDE: "extrude",
	DI: "distance",
	DIST: "distance",
	AA: "area",
	AREA: "area",
	ANG: "angle",
	ID: "id",
	U: "undo",
	UNDO: "undo",
	REDO: "redo",
	ZE: "zoom_extents",
	Z: "zoom_extents",
	LWT: "toggle_lineweights",
	IMPORT: "import",
	DXFIN: "import",
	EXPORT: "export",
	DXFOUT: "export",
	SAVE: "save",
	QSAVE: "save",
};
