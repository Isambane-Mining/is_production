"""Writes tests/fixtures/sample_r2018.dxf: one of every entity the DXF import handles, at Lo29-scale
coordinates, including mirrored (extrusion 0,0,-1) arcs/polylines, nested and mirrored blocks with
attributes, five dimension types, hatches with holes and arc edges, and entities that are not supported.

Run with the CAD worker Python (it has ezdxf):  /opt/cad-worker/venv/bin/python make_sample_dxf.py
"""

import math
import os

import ezdxf
from ezdxf.enums import TextEntityAlignment

X, Y, Z = -19500.0, 2860500.0, 1500.0


def p(dx, dy, dz=0.0):
	return (X + dx, Y + dy, Z + dz)


def main(path):
	doc = ezdxf.new("R2018", setup=True)
	doc.units = 6  # metres
	doc.layers.add("CREST", color=1, linetype="DASHED", lineweight=35)
	doc.layers.add("TOE", color=3)
	doc.layers.get("TOE").rgb = (20, 160, 90)
	doc.layers.add("SURVEY", color=5).lock()
	doc.layers.add("HIDDEN_LAYER", color=7).off()
	doc.layers.add("ANNOTATION", color=2)
	msp = doc.modelspace()

	msp.add_point(p(0, 0), dxfattribs={"layer": "SURVEY"})
	msp.add_line(p(0, 0), p(100, 50, 10), dxfattribs={"layer": "CREST"})
	msp.add_line(p(0, 0), p(100, 50, 10), dxfattribs={"layer": "CREST"})  # duplicate
	msp.add_line(p(5, 5), p(5, 5), dxfattribs={"layer": "CREST"})  # zero length: invalid
	msp.add_circle(p(50, 50), 12.5, dxfattribs={"layer": "TOE", "color": 4})
	msp.add_arc(p(80, 20), 10, 30, 150, dxfattribs={"layer": "TOE"})
	# a mirrored arc: extrusion (0, 0, -1), centre in OCS (x negated)
	arc = msp.add_arc((-(X + 120), Y + 20, -(Z)), 8, 0, 90, dxfattribs={"layer": "TOE", "extrusion": (0, 0, -1)})
	arc.rgb = (250, 128, 0)
	pl = msp.add_lwpolyline([(X, Y - 50, 0), (X + 40, Y - 50, 1.0), (X + 40, Y - 10, 0), (X, Y - 10, 0)], format="xyb", close=True, dxfattribs={"layer": "CREST", "elevation": Z - 5})
	pl.dxf.const_width = 0
	msp.add_lwpolyline([(-(X + 60), Y - 50, 0.5), (-(X + 90), Y - 50, 0)], format="xyb", dxfattribs={"layer": "CREST", "extrusion": (0, 0, -1), "elevation": -(Z)})
	msp.add_polyline3d([p(0, 100, 0), p(20, 110, 2), p(40, 105, 4), p(60, 120, 1)], dxfattribs={"layer": "SURVEY"})
	msp.add_polyline2d([(X, Y + 150), (X + 30, Y + 150), (X + 30, Y + 170)], dxfattribs={"layer": "TOE"})
	msp.add_ellipse(p(150, 50), major_axis=(30, 10, 0), ratio=0.4, start_param=0.2, end_param=4.5, dxfattribs={"layer": "TOE"})
	msp.add_ellipse(p(150, 120), major_axis=(0, 20, 0), ratio=0.5, dxfattribs={"layer": "TOE"})
	msp.add_open_spline([p(200, 0), p(210, 30), p(240, 30), p(250, 0), p(270, 10)], degree=3, dxfattribs={"layer": "CREST"})
	msp.add_spline(fit_points=[p(200, 60), p(220, 80), p(250, 70), p(270, 90)], dxfattribs={"layer": "CREST"})
	msp.add_xline(p(0, -100), (1, 1, 0), dxfattribs={"layer": "ANNOTATION"})
	msp.add_ray(p(0, -120), (1, 0, 0), dxfattribs={"layer": "ANNOTATION"})

	t = msp.add_text("Pit A — Bench 3", height=2.5, rotation=15, dxfattribs={"layer": "ANNOTATION"})
	t.set_placement(p(10, 60)[:2], align=TextEntityAlignment.MIDDLE_CENTER)
	t.dxf.insert = p(10, 60)
	msp.add_text("Left baseline %%d", height=1.8, dxfattribs={"layer": "ANNOTATION", "insert": p(10, 70)})
	msp.add_mtext("Line one\\PLine {\\fArial;two}", dxfattribs={"layer": "ANNOTATION", "insert": p(10, 80), "char_height": 2.0, "width": 40, "attachment_point": 5})

	style = {"dimtxt": 2.0, "dimasz": 1.5}
	msp.add_linear_dim(base=p(0, -30)[:2], p1=p(0, -20)[:2], p2=p(40, -25)[:2], angle=0, override=style, dxfattribs={"layer": "ANNOTATION"}).render()
	msp.add_aligned_dim(p1=p(0, 0)[:2], p2=p(100, 50)[:2], distance=8, override=style, dxfattribs={"layer": "ANNOTATION"}).render()
	msp.add_radius_dim(center=p(50, 50)[:2], radius=12.5, angle=45, override=style, dxfattribs={"layer": "ANNOTATION"}).render()
	msp.add_diameter_dim(center=p(50, 50)[:2], radius=12.5, angle=135, override=style, dxfattribs={"layer": "ANNOTATION"}).render()
	msp.add_angular_dim_3p(base=p(95, 35)[:2], center=p(80, 20)[:2], p1=p(90, 20)[:2], p2=p(80, 30)[:2], override=style, dxfattribs={"layer": "ANNOTATION"}).render()

	hatch = msp.add_hatch(color=1, dxfattribs={"layer": "TOE"})
	hatch.dxf.elevation = (0, 0, Z)
	hatch.paths.add_polyline_path([(X + 300, Y), (X + 360, Y), (X + 360, Y + 60), (X + 300, Y + 60)], is_closed=True, flags=1)
	hatch.paths.add_polyline_path([(X + 320, Y + 20), (X + 340, Y + 20), (X + 340, Y + 40), (X + 320, Y + 40)], is_closed=True, flags=0)
	hatch.set_solid_fill()
	pattern = msp.add_hatch(dxfattribs={"layer": "TOE"})
	pattern.dxf.elevation = (0, 0, Z)
	edges = pattern.paths.add_edge_path(flags=1)
	edges.add_line((X + 400, Y), (X + 440, Y))
	edges.add_arc((X + 420, Y), radius=20, start_angle=0, end_angle=180, ccw=True)
	pattern.set_pattern_fill("ANSI31", scale=0.5, angle=0)
	msp.add_solid([p(500, 0)[:2], p(520, 0)[:2], p(500, 20)[:2], p(520, 20)[:2]], dxfattribs={"layer": "TOE"})

	marker = doc.blocks.new("MARKER", base_point=(0, 0, 0))
	marker.add_circle((0, 0), 1.0)
	marker.add_line((-1.5, 0), (1.5, 0))
	marker.add_attdef("ID", (1.2, 1.2), dxfattribs={"height": 0.8})
	beacon = doc.blocks.new("BEACON", base_point=(2, 0, 0))
	beacon.add_lwpolyline([(0, 0), (4, 0), (2, 3)], close=True)
	beacon.add_blockref("MARKER", (2, 1), dxfattribs={"xscale": 0.5, "yscale": 0.5})
	ref = msp.add_blockref("MARKER", p(600, 0), dxfattribs={"layer": "SURVEY", "rotation": 30, "xscale": 2, "yscale": 2})
	ref.add_auto_attribs({"ID": "BM-17"})
	msp.add_blockref("BEACON", p(620, 0), dxfattribs={"layer": "SURVEY", "xscale": -1.5, "yscale": 1.5, "rotation": 10})
	msp.add_blockref("BEACON", (-(X + 650), Y, -Z), dxfattribs={"layer": "SURVEY", "extrusion": (0, 0, -1)})

	mesh = msp.add_mesh(dxfattribs={"layer": "SURVEY"})  # unsupported (Phase 3)
	with mesh.edit_data() as data:
		data.vertices = [p(700, 0), p(710, 0), p(700, 10)]
		data.faces = [(0, 1, 2)]
	doc.paperspace().add_line((0, 0), (10, 10))  # paper space: not imported
	doc.saveas(path)


if __name__ == "__main__":
	main(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "sample_r2018.dxf"))
