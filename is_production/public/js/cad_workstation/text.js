// Text as triangles: glyph outlines of Droid Sans (Apache-2.0, public/cad_fonts) triangulated once per
// character and cached, then placed per text entity. Text height is the capital height, as in CAD.
// Output is in model coordinates (doubles); the viewport subtracts its origin before using float32.

import * as THREE from "three";
import { Font } from "three/examples/jsm/loaders/FontLoader.js";

const FONT_URL = "/assets/is_production/cad_fonts/droid_sans_regular.typeface.json";
const MTEXT_LINE = 1.667; // line pitch = height × 1.667 × line spacing (as AutoCAD MTEXT)

let font = null;
let loading = null;
const glyphs = new Map(); // char -> {positions: Float32Array (em units), index, advance}
const glyph_geometries = new Map(); // char -> THREE.BufferGeometry in em units (shared by every instance)
let cap_ratio = 0.714;

export function load_font() {
	if (font) return Promise.resolve(font);
	if (!loading) {
		loading = fetch(FONT_URL)
			.then((r) => r.json())
			.then((data) => {
				font = new Font(data);
				const h = glyph("H");
				let top = 0;
				for (let i = 1; i < h.positions.length; i += 2) top = Math.max(top, h.positions[i]);
				cap_ratio = top || cap_ratio;
				return font;
			});
	}
	return loading;
}

export function font_ready() {
	return Boolean(font);
}

function glyph(ch) {
	if (glyphs.has(ch)) return glyphs.get(ch);
	const data = font.data;
	const known = data.glyphs[ch] ? ch : "?";
	const shapes = font.generateShapes(known, 1);
	const positions = [], index = [];
	for (const shape of shapes) {
		const points = shape.extractPoints(4);
		const contour = points.shape, holes = points.holes;
		if (THREE.ShapeUtils.isClockWise(contour)) contour.reverse();
		for (const hole of holes) if (!THREE.ShapeUtils.isClockWise(hole)) hole.reverse();
		const faces = THREE.ShapeUtils.triangulateShape(contour, holes);
		const base = positions.length / 2;
		for (const p of [...contour, ...holes.flat()]) positions.push(p.x, p.y);
		for (const f of faces) index.push(base + f[0], base + f[1], base + f[2]);
	}
	const g = { positions: new Float32Array(positions), index, advance: (data.glyphs[known].ha || 0) / data.resolution };
	glyphs.set(ch, g);
	return g;
}

function em_size(height) {
	return height / cap_ratio;
}

export function text_width(text, height, width_factor = 1) {
	if (!font) return height * 0.6 * text.length * width_factor;
	let w = 0;
	for (const ch of text) w += glyph(ch).advance;
	return w * em_size(height) * width_factor;
}

function descent(height) {
	return font ? (-font.data.descender / font.data.resolution) * em_size(height) : height * 0.25;
}

// Lay out one line: triangles appended to out ({positions: number[] (x, y, z triples), index: number[]}).
function place_line(out, text, origin, height, rotation, width_factor, dx, dy) {
	const size = em_size(height);
	const c = Math.cos(rotation), s = Math.sin(rotation);
	let x = dx;
	for (const ch of text) {
		const g = glyph(ch);
		if (g.index.length) {
			const base = out.positions.length / 3;
			for (let i = 0; i < g.positions.length; i += 2) {
				const lx = x + g.positions[i] * size * width_factor, ly = dy + g.positions[i + 1] * size;
				out.positions.push(origin[0] + c * lx - s * ly, origin[1] + s * lx + c * ly, origin[2]);
			}
			for (const k of g.index) out.index.push(base + k);
		}
		x += g.advance * size * width_factor;
	}
}

// Triangles of a Text entity's geometry.
export function text_mesh(g, out = { positions: [], index: [] }) {
	if (!font || !g.text) return out;
	const wf = g.width_factor || 1;
	const width = text_width(g.text, g.height, wf);
	const dx = { left: 0, center: -width / 2, right: -width }[g.halign || "left"] ?? 0;
	const dy = { baseline: 0, bottom: descent(g.height), middle: -g.height / 2, top: -g.height }[g.valign || "baseline"] ?? 0;
	place_line(out, g.text, g.position, g.height, g.rotation || 0, wf, dx, dy);
	return out;
}

function wrap(text, height, width) {
	if (!(width > 0)) return text.split("\n");
	const lines = [];
	for (const paragraph of text.split("\n")) {
		let line = "";
		for (const word of paragraph.split(" ")) {
			const trial = line ? `${line} ${word}` : word;
			if (line && text_width(trial, height) > width) {
				lines.push(line);
				line = word;
			} else line = trial;
		}
		lines.push(line);
	}
	return lines;
}

// Triangles of a Multiline Text entity (attachment 1-9: top/middle/bottom × left/centre/right).
export function mtext_mesh(g, out = { positions: [], index: [] }) {
	if (!font || !g.text) return out;
	const lines = wrap(g.text, g.height, g.width);
	const pitch = g.height * MTEXT_LINE * (g.line_spacing || 1);
	const total = g.height + pitch * (lines.length - 1);
	const column = (g.attachment - 1) % 3, row = Math.floor((g.attachment - 1) / 3);
	const top = [0, total / 2, total][row]; // first line's cap top relative to the attachment point
	lines.forEach((line, i) => {
		const w = text_width(line, g.height);
		const dx = [0, -w / 2, -w][column];
		place_line(out, line, g.position, g.height, g.rotation || 0, 1, dx, top - g.height - i * pitch);
	});
	return out;
}

export function mtext_lines(g) {
	return wrap(g.text || "", g.height, g.width);
}

// ------------------------------------------------------------------------------------- instanced glyphs
//
// For drawing, text is not triangulated per label: each character is one instance of its shared glyph
// geometry, placed by [x, y, z, rotation, x scale, y scale] (model coordinates). 20 000 spot-height labels
// cost a few hundred thousand numbers instead of millions of triangles.

export function glyph_geometry(ch) {
	let geometry = glyph_geometries.get(ch);
	if (!geometry) {
		const g = glyph(ch);
		geometry = new THREE.BufferGeometry();
		const positions = new Float32Array((g.positions.length / 2) * 3);
		for (let i = 0, j = 0; i < g.positions.length; i += 2, j += 3) {
			positions[j] = g.positions[i];
			positions[j + 1] = g.positions[i + 1];
		}
		geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
		geometry.setIndex(g.index);
		glyph_geometries.set(ch, geometry);
	}
	return geometry;
}

function place_glyphs(out, text, origin, height, rotation, width_factor, dx, dy) {
	const size = em_size(height);
	const c = Math.cos(rotation), s = Math.sin(rotation);
	let x = dx;
	for (const ch of text) {
		const g = glyph(ch);
		if (g.index.length) out.push({ ch, x: origin[0] + c * x - s * dy, y: origin[1] + s * x + c * dy, z: origin[2], rotation, sx: size * width_factor, sy: size });
		x += g.advance * size * width_factor;
	}
}

// Turn a text's glyphs 180° about the text's centre (local cx, cy): for a plan view that is itself turned
// 180° (westing/southing coordinates), so text stays readable. Display only; the stored text is unchanged.
function upright(list, from, g, cx, cy) {
	const r = g.rotation || 0, c = Math.cos(r), s = Math.sin(r);
	const C = [g.position[0] + c * cx - s * cy, g.position[1] + s * cx + c * cy];
	for (let i = from; i < list.length; i++) {
		const q = list[i];
		q.x = 2 * C[0] - q.x;
		q.y = 2 * C[1] - q.y;
		q.rotation += Math.PI;
	}
}

// Glyph instances of a Text entity's geometry: [{ch, x, y, z, rotation, sx, sy}]
export function text_glyphs(g, out = [], flip = false) {
	if (!font || !g.text) return out;
	const wf = g.width_factor || 1;
	const width = text_width(g.text, g.height, wf);
	const dx = { left: 0, center: -width / 2, right: -width }[g.halign || "left"] ?? 0;
	const dy = { baseline: 0, bottom: descent(g.height), middle: -g.height / 2, top: -g.height }[g.valign || "baseline"] ?? 0;
	const from = out.length;
	place_glyphs(out, g.text, g.position, g.height, g.rotation || 0, wf, dx, dy);
	if (flip) upright(out, from, g, dx + width / 2, dy + g.height / 2);
	return out;
}

export function mtext_glyphs(g, out = [], flip = false) {
	if (!font || !g.text) return out;
	const from = out.length;
	const lines = wrap(g.text, g.height, g.width);
	const pitch = g.height * MTEXT_LINE * (g.line_spacing || 1);
	const total = g.height + pitch * (lines.length - 1);
	const column = (g.attachment - 1) % 3, row = Math.floor((g.attachment - 1) / 3);
	const top = [0, total / 2, total][row];
	lines.forEach((line, i) => {
		const w = text_width(line, g.height);
		place_glyphs(out, line, g.position, g.height, g.rotation || 0, 1, [0, -w / 2, -w][column], top - g.height - i * pitch);
	});
	if (flip) {
		const width = Math.max(...lines.map((line) => text_width(line, g.height)));
		upright(out, from, g, [width / 2, 0, -width / 2][column], top - total / 2);
	}
	return out;
}
