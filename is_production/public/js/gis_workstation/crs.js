// Coordinate reference systems for the coordinate readout.
//
// The map itself is EPSG:3857. Other CRSs come from the server (pyproj), so their definitions are
// PROJ's own. proj4js ignores +axis, so the server sends the axis order separately and the signs
// are flipped here: the South African Lo zones (+axis=wsu) read as westing/southing, the same x/y
// as pyproj and QGIS.

import proj4 from "proj4";
import { register } from "ol/proj/proj4.js";
import { transform } from "ol/proj.js";
import { call } from "./api.js";

const definitions = {
	"EPSG:4326": { code: "EPSG:4326", name: "WGS 84", axis: "enu", geographic: true },
	"EPSG:3857": { code: "EPSG:3857", name: "WGS 84 / Pseudo-Mercator", axis: "enu", geographic: false },
};

export async function load_crs(code) {
	if (definitions[code]) return definitions[code];
	const definition = await call("get_crs_definition", { crs: code });
	proj4.defs(definition.code, definition.proj4);
	register(proj4);
	definitions[definition.code] = definition;
	return definition;
}

export function crs_label(code) {
	const d = definitions[code];
	return d ? `${d.code} · ${d.name}` : code;
}

// [x, y] of a map (EPSG:3857) coordinate in the given CRS, in that CRS's axis order.
export function to_display(coordinate, code) {
	const d = definitions[code];
	if (!d) return null;
	const [x, y] = code === "EPSG:3857" ? coordinate : transform(coordinate, "EPSG:3857", d.code);
	return [d.axis[0] === "w" ? -x : x, d.axis[1] === "s" ? -y : y];
}

export function format_coordinate(coordinate, code) {
	const d = definitions[code];
	const xy = coordinate && to_display(coordinate, code);
	if (!xy || !xy.every(Number.isFinite)) return "";
	if (d.geographic) return `${xy[0].toFixed(6)}°, ${xy[1].toFixed(6)}°`;
	return `${xy[0].toFixed(2)}, ${xy[1].toFixed(2)}`;
}

// Map (EPSG:3857) coordinate of an [x, y] typed in the given CRS's axis order.
export function from_display(xy, code) {
	const d = definitions[code];
	if (!d) return null;
	const native = [d.axis[0] === "w" ? -xy[0] : xy[0], d.axis[1] === "s" ? -xy[1] : xy[1]];
	return code === "EPSG:3857" ? native : transform(native, d.code, "EPSG:3857");
}
