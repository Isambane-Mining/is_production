"""IFC for the isolated CAD worker (Phase 6), with IfcOpenShell (LGPL-3).

inspect   schema, project, element counts by IFC class, georeferencing (IfcMapConversion / IfcProjectedCRS), extents
import    every product with geometry as a triangle mesh (written as a TIN file, like STL / OBJ meshes), one per
          element, or merged per IFC class or per storey; coordinates through the IfcMapConversion (eastings,
          northings, orthogonal height, rotation, scale) when the file has one
export    an IFC4 file: surfaces and meshes (IfcGeographicElement for terrain, IfcBuildingElementProxy otherwise) and
          solids (their display tessellation) as face sets, polylines as IfcAnnotation curves; georeferenced with
          the model's CRS name and an IfcMapConversion whose eastings / northings / height are the model's origin
          (coordinates in the file are relative to it, so they stay small)

Protocol as the other workers: one JSON request on stdin, JSON lines out (progress / log / result / error).
"""

import json
import math
import os
import struct
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # -I leaves the script's folder off sys.path

import surface_worker as sw  # noqa: E402
from surface_worker import Tin, emit, inside  # noqa: E402

MAX_MESHES = 5000  # more elements than this are merged per IFC class


def open_ifc(path):
	import ifcopenshell

	return ifcopenshell.open(path)


def map_conversion(f):
	"""(eastings, northings, height, cos, sin, scale, crs name) of the file's IfcMapConversion, or None."""
	if f.schema == "IFC2X3":
		return None
	for m in f.by_type("IfcMapConversion"):
		a, o = m.XAxisAbscissa, m.XAxisOrdinate
		if a is None or o is None:
			a, o = 1.0, 0.0
		norm = math.hypot(a, o) or 1.0
		crs = m.TargetCRS.Name if m.TargetCRS is not None else None
		return {"eastings": float(m.Eastings or 0), "northings": float(m.Northings or 0), "height": float(m.OrthogonalHeight or 0), "cos": a / norm, "sin": o / norm, "scale": float(m.Scale or 1.0), "crs": crs}
	return None


def to_map(xyz, mc):
	if not mc:
		return xyz
	s, c, n = mc["scale"], mc["cos"], mc["sin"]
	x, y = xyz[:, 0] * s, xyz[:, 1] * s
	return np.c_[mc["eastings"] + x * c - y * n, mc["northings"] + x * n + y * c, xyz[:, 2] * s + mc["height"]]


def storey_of(element):
	import ifcopenshell.util.element as ue

	c = ue.get_container(element)
	while c is not None and not c.is_a("IfcBuildingStorey") and not c.is_a("IfcSite"):
		c = ue.get_container(c) or (ue.get_aggregate(c) if hasattr(ue, "get_aggregate") else None)
	return c.Name if c is not None else None


def shapes(f, log):
	"""(element, xyz (n, 3), triangles (m, 3)) for every product with a body, in IFC model coordinates (metres)."""
	import ifcopenshell.geom

	settings = ifcopenshell.geom.settings()
	settings.set("use-world-coords", True)
	it = ifcopenshell.geom.iterator(settings, f)
	if not it.initialize():
		return
	n = 0
	while True:
		sh = it.get()
		g = sh.geometry
		xyz = np.asarray(g.verts, float).reshape(-1, 3)
		tri = np.asarray(g.faces, np.int64).reshape(-1, 3)
		if len(tri):
			yield f.by_id(sh.id), xyz, tri
		n += 1
		if n % 200 == 0:
			emit(log=f"{n} IFC products read…")
		if not it.next():
			break


def inspect(request, log):
	workspace = request["workspace"]
	f = open_ifc(inside(workspace, request["input"]))
	mc = map_conversion(f)
	by_class, lo, hi, count = {}, None, None, 0
	for element, xyz, _tri in shapes(f, log):
		by_class[element.is_a()] = by_class.get(element.is_a(), 0) + 1
		p = to_map(xyz, mc)
		lo = p.min(axis=0) if lo is None else np.minimum(lo, p.min(axis=0))
		hi = p.max(axis=0) if hi is None else np.maximum(hi, p.max(axis=0))
		count += 1
	project = f.by_type("IfcProject")
	warnings = []
	if f.schema == "IFC2X3":
		warnings.append("IFC2X3 has no IfcMapConversion: the coordinates are the file's own (use the axis options to place it).")
	elif not mc:
		warnings.append("The file is not georeferenced (no IfcMapConversion): the coordinates are the file's own.")
	return {
		"format": "IFC",
		"schema": f.schema,
		"project": project[0].Name if project else None,
		"elements": count,
		"by_class": dict(sorted(by_class.items(), key=lambda kv: -kv[1])),
		"georeferencing": mc,
		"extents": [float(v) for v in (*lo, *hi)] if lo is not None else None,
		"warnings": warnings,
	}


def import_ifc(request, log):
	"""Meshes from the file's products: one per element, or merged per IFC class / storey."""
	workspace = request["workspace"]
	o = request.get("options") or {}
	f = open_ifc(inside(workspace, request["input"]))
	mc = map_conversion(f) if o.get("georeference", True) else None
	group = o.get("group_by") or "element"
	scale = float(o.get("unit_scale") or 1.0)
	groups = {}
	n = 0
	for element, xyz, tri in shapes(f, log):
		p = sw.axis_map(to_map(xyz, mc) * scale, o.get("axis_mapping") or "As in file")
		cls = element.is_a()
		if group == "class":
			key, meta = cls, {"ifc_class": cls}
		elif group == "storey":
			st = storey_of(element) or "(no storey)"
			key, meta = st, {"storey": st}
		else:
			key = element.GlobalId
			meta = {"ifc_class": cls, "global_id": element.GlobalId, "name": element.Name, "storey": storey_of(element)}
		g = groups.setdefault(key, {"meta": meta, "parts": [], "count": 0})
		g["parts"].append((p, tri))
		g["count"] += 1
		n += 1
	if group == "element" and len(groups) > MAX_MESHES:
		log.append(f"{len(groups)} elements: more than {MAX_MESHES}, merged per IFC class.")
		merged = {}
		for g in groups.values():
			m = merged.setdefault(g["meta"]["ifc_class"], {"meta": {"ifc_class": g["meta"]["ifc_class"]}, "parts": [], "count": 0})
			m["parts"] += g["parts"]
			m["count"] += g["count"]
		groups = merged
	out = []
	os.makedirs(os.path.join(workspace, "outputs"), exist_ok=True)
	for k, (key, g) in enumerate(groups.items()):
		xyz, tris, start = [], [], 0
		for p, t in g["parts"]:
			xyz.append(p)
			tris.append(t + start)
			start += len(p)
		tin = Tin(np.concatenate(xyz), np.concatenate(tris))
		path = os.path.join(workspace, "outputs", f"ifc_{k}.tin")
		tin.write(path)
		stats = tin.stats()
		stats.update(sw.mesh_checks(tin))
		label = g["meta"].get("name") or g["meta"].get("ifc_class") or g["meta"].get("storey") or str(key)
		if group == "element" and g["meta"].get("ifc_class"):
			label = f"{g['meta']['ifc_class'].replace('Ifc', '')} {g['meta'].get('name') or ''}".strip()
		out.append({"name": label[:120], "tin": path, "stats": stats, "metadata": {**g["meta"], "elements": g["count"]}})
		emit(progress=int(100 * (k + 1) / max(len(groups), 1)))
	return {"meshes": out, "elements": n, "georeferencing": mc, "schema": f.schema}


def read_cadmesh(path):
	"""A solid's display mesh (ISPCADM1, see surface_worker.write_mesh): positions relative to its origin + triangles."""
	with open(path, "rb") as fh:
		b = fh.read()
	if b[:8] != b"ISPCADM1":
		raise ValueError("Not a CAD display mesh.")
	nv, ni = struct.unpack("<II", b[8:16])
	origin = np.array(struct.unpack("<3d", b[20:44]))
	pos = np.frombuffer(b, "<f4", nv * 3, 44).reshape(-1, 3).astype(float) + origin
	idx = np.frombuffer(b, "<u4", ni, 44 + nv * 12).reshape(-1, 3).astype(np.int64)
	return pos, idx


def export_ifc(request, log):
	import ifcopenshell.api
	import ifcopenshell.api.georeference

	workspace = request["workspace"]
	origin = np.asarray(request.get("origin") or [0, 0, 0], float)
	f = ifcopenshell.api.run("project.create_file", version="IFC4")
	project = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcProject", name=request.get("project") or "CAD model")
	ifcopenshell.api.run("unit.assign_unit", f)
	model = ifcopenshell.api.run("context.add_context", f, context_type="Model")
	body = ifcopenshell.api.run("context.add_context", f, context_type="Model", context_identifier="Body", target_view="MODEL_VIEW", parent=model)
	annotation = ifcopenshell.api.run("context.add_context", f, context_type="Model", context_identifier="Annotation", target_view="MODEL_VIEW", parent=model)
	site = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcSite", name=request.get("site") or "Site")
	ifcopenshell.api.run("aggregate.assign_object", f, relating_object=project, products=[site])
	ifcopenshell.api.georeference.add_georeferencing(f)
	ifcopenshell.api.georeference.edit_georeferencing(f, projected_crs={"Name": request.get("crs") or "unknown"}, coordinate_operation={"Eastings": float(origin[0]), "Northings": float(origin[1]), "OrthogonalHeight": float(origin[2])})
	written = {}
	products = []
	for item in request.get("meshes") or []:
		if item.get("tin"):
			t = Tin.read(inside(workspace, item["tin"]))
			xyz, tri = t.xyz, t.tri
		else:
			xyz, tri = read_cadmesh(inside(workspace, item["mesh"]))
		if not len(tri):
			continue
		cls = "IfcGeographicElement" if item.get("kind") == "surface" else "IfcBuildingElementProxy"
		el = ifcopenshell.api.run("root.create_entity", f, ifc_class=cls, name=item.get("name") or item.get("kind"))
		rep = ifcopenshell.api.run("geometry.add_mesh_representation", f, context=body, vertices=[(xyz - origin).tolist()], faces=[tri.tolist()])
		ifcopenshell.api.run("geometry.assign_representation", f, product=el, representation=rep)
		products.append(el)
		written[item.get("kind") or "mesh"] = written.get(item.get("kind") or "mesh", 0) + 1
	for item in request.get("polylines") or []:
		pts = np.asarray(item["points"], float)
		if len(pts) < 2:
			continue
		el = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcAnnotation", name=item.get("name") or "Line")
		local = (pts - origin).tolist()
		if item.get("closed"):
			local.append(local[0])
		curve = f.createIfcPolyline([f.createIfcCartesianPoint([float(v) for v in p]) for p in local])
		rep = f.createIfcShapeRepresentation(annotation, "Annotation", "Curve", [curve])
		ifcopenshell.api.run("geometry.assign_representation", f, product=el, representation=rep)
		products.append(el)
		written["polyline"] = written.get("polyline", 0) + 1
	if products:
		ifcopenshell.api.run("spatial.assign_container", f, relating_structure=site, products=products)
	out = inside(workspace, request["out"])
	os.makedirs(os.path.dirname(out), exist_ok=True)
	f.write(out)
	return {"out": out, "written": written, "bytes": os.path.getsize(out)}


COMMANDS = {"inspect": inspect, "import": import_ifc, "export": export_ifc}


def main():
	log = []
	try:
		request = json.loads(sys.stdin.read() or "{}")
		command = COMMANDS.get(request.get("command"))
		if not command:
			raise ValueError(f"Unknown command {request.get('command')!r}.")
		sw.apply_limits(request)
		result = command(request, log)
		for line in log:
			emit(log=line)
		emit(result=result)
	except MemoryError:
		emit(error="The CAD worker ran out of its memory limit (CAD Settings → Worker Memory Limit).")
	except Exception as e:
		for line in log:
			emit(log=line)
		emit(error=f"{type(e).__name__}: {e}")


if __name__ == "__main__":
	main()
