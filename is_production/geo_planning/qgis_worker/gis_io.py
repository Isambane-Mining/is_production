"""GIS import/export worker: GDAL/OGR for data, PyQGIS for QGIS project files.

Runs under the system Python that has python3-qgis and python3-gdal (``/usr/bin/python3 -I gis_io.py``),
never inside the bench, and does not import Frappe. Reads one JSON request on stdin and prints one JSON
report on stdout. Commands:

- ``inspect``  {path}: the layers of a file (or of the files in a zip): geometry types actually present,
  CRS, fields, feature count, extent. Zips are read in place through GDAL's /vsizip/ (nothing is
  extracted here).
- ``read``     {source, layer, out, ...}: write a layer's features as NDJSON (GeoJSON geometry, with Z,
  curves linearised; typed properties), optionally transforming coordinates, for Frappe to import.
- ``write``    {input, out, driver, ...}: write NDJSON features from Frappe to a GIS file (one layer).
- ``project_write`` {gpkg, qgz, ...}: a QGIS project (.qgz) over a GeoPackage, with layer order,
  visibility, opacity, colour, CRS and extent; paths stored relative so the pair can be moved.
- ``project_read``  {qgz, root}: the vector layers of a QGIS project whose data lies inside ``root``.

Coordinates use the traditional GIS axis order (x = easting or westing, y = northing or southing), the
same as pyproj's always_xy in Frappe and as QGIS.

This file imports QGIS (GPL-2.0-or-later); see the licence note in the app README.
"""

import json
import math
import os
import sys
import tempfile
import zipfile

SCAN_LIMIT = 200000
DATA_EXTENSIONS = (".gpkg", ".shp", ".geojson", ".json", ".kml", ".kmz", ".gml", ".gpx", ".csv", ".dxf", ".tab", ".mif")
CSV_X = "x,lon,long,longitude,easting,east,e"
CSV_Y = "y,lat,latitude,northing,north,n"
CSV_Z = "z,elev,elevation,height,rl,altitude"


def main():
	request = json.loads(sys.stdin.read() or "{}")
	from osgeo import gdal, ogr, osr

	gdal.UseExceptions()
	ogr.UseExceptions()
	osr.UseExceptions()
	try:
		handler = COMMANDS[request["command"]]
		report = handler(request)
		report.setdefault("ok", True)
	except Exception as e:
		report = {"ok": False, "message": str(e)}
	print(json.dumps(report, default=str))


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def srs_info(srs):
	if srs is None:
		return {"epsg": None, "name": None, "wkt": None}
	srs = srs.Clone()
	try:
		srs.AutoIdentifyEPSG()
	except Exception:
		pass
	code = srs.GetAuthorityCode(None)
	if not code:
		# e.g. an ESRI .prj: ask PROJ for the best EPSG match (95% or better)
		try:
			matches = srs.FindMatches()
			if matches and matches[0][1] >= 95:
				code = matches[0][0].GetAuthorityCode(None)
		except Exception:
			code = None
	return {"epsg": f"EPSG:{code}" if code else None, "name": srs.GetName(), "wkt": srs.ExportToWkt()}


def make_srs(user_input):
	from osgeo import osr

	srs = osr.SpatialReference()
	srs.SetFromUserInput(user_input)
	srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
	return srs


def family(flat_name):
	"""Map an OGR geometry type name to the dataset types (curves are linearised on read)."""
	name = flat_name.replace(" ", "").lower()
	table = {
		"point": "Point",
		"multipoint": "MultiPoint",
		"linestring": "LineString",
		"circularstring": "LineString",
		"compoundcurve": "LineString",
		"multilinestring": "MultiLineString",
		"multicurve": "MultiLineString",
		"polygon": "Polygon",
		"curvepolygon": "Polygon",
		"triangle": "Polygon",
		"multipolygon": "MultiPolygon",
		"multisurface": "MultiPolygon",
		"polyhedralsurface": "MultiPolygon",
		"tin": "MultiPolygon",
	}
	return table.get(name, "Geometry")


def suggest_type(types):
	"""Dataset geometry type for the geometry types found in a layer."""
	present = {t for t in types if t}
	if not present:
		return "No Geometry"
	for base in ("Point", "LineString", "Polygon"):
		if present <= {base, "Multi" + base}:
			return "Multi" + base if "Multi" + base in present else base
	return "Geometry"


def open_vector(source, csv=None):
	from osgeo import gdal

	options = []
	if source.lower().endswith(".csv") or (csv and csv.get("force")):
		csv = csv or {}
		options = [
			"X_POSSIBLE_NAMES=" + (csv.get("x") or CSV_X),
			"Y_POSSIBLE_NAMES=" + (csv.get("y") or CSV_Y),
			"Z_POSSIBLE_NAMES=" + (csv.get("z") or CSV_Z),
			"GEOM_POSSIBLE_NAMES=wkt,geometry,geom,the_geom",
			"KEEP_GEOM_COLUMNS=NO",
			"AUTODETECT_TYPE=YES",
		]
	return gdal.OpenEx(source, gdal.OF_VECTOR | gdal.OF_READONLY, open_options=options)


def sources_in(path):
	"""GDAL paths of the data files in a file or a zip (read through /vsizip/, never extracted here)."""
	lower = path.lower()
	if lower.endswith(".kmz"):
		return ["/vsizip/" + path]
	if not lower.endswith(".zip"):
		return [path]
	with zipfile.ZipFile(path) as archive:
		names = archive.namelist()
	out = []
	for name in names:
		n = name.lower()
		if n.startswith("__macosx/") or name.endswith("/"):
			continue
		if n.endswith(".gdb/gdb") or (".gdb/" in n and n.endswith("timestamps")):
			gdb = name[: n.index(".gdb/") + 4]
			if f"/vsizip/{path}/{gdb}" not in out:
				out.append(f"/vsizip/{path}/{gdb}")
		elif n.endswith(DATA_EXTENSIONS):
			out.append(f"/vsizip/{path}/{name}")
	return out


def field_info(defn):
	from osgeo import ogr

	fields = []
	for i in range(defn.GetFieldCount()):
		fd = defn.GetFieldDefn(i)
		fields.append(
			{
				"name": fd.GetName(),
				"type": ogr.GetFieldTypeName(fd.GetType()),
				"subtype": ogr.GetFieldSubTypeName(fd.GetSubType()),
				"width": fd.GetWidth(),
			}
		)
	return fields


# ---------------------------------------------------------------------------
# inspect
# ---------------------------------------------------------------------------


def inspect(request):
	from osgeo import ogr

	layers, problems = [], []
	for source in sources_in(request["path"]):
		try:
			ds = open_vector(source, request.get("csv"))
		except Exception as e:
			problems.append(f"{os.path.basename(source)}: {e}")
			continue
		for index in range(ds.GetLayerCount()):
			lyr = ds.GetLayer(index)
			defn = lyr.GetLayerDefn()
			types, has_z, scanned, empty = {}, False, 0, 0
			for feature in lyr:
				scanned += 1
				geom = feature.GetGeometryRef()
				if geom is None or geom.IsEmpty():
					empty += 1
				else:
					kind = family(ogr.GeometryTypeToName(ogr.GT_Flatten(geom.GetGeometryType())))
					types[kind] = types.get(kind, 0) + 1
					has_z = has_z or bool(geom.Is3D())
				if scanned >= SCAN_LIMIT:
					break
			lyr.ResetReading()
			try:
				extent = list(lyr.GetExtent())  # (minx, maxx, miny, maxy)
			except Exception:
				extent = None
			declared = ogr.GeometryTypeToName(lyr.GetGeomType())
			if types:
				suggested = suggest_type(types)
			elif scanned:  # features, but none with geometry
				suggested = "No Geometry"
			else:  # an empty layer: go by what it declares
				suggested = "No Geometry" if lyr.GetGeomType() == ogr.wkbNone else family(ogr.GeometryTypeToName(ogr.GT_Flatten(lyr.GetGeomType())))
			layers.append(
				{
					"source": source,
					"layer": lyr.GetName(),
					"driver": ds.GetDriver().ShortName,
					"declared_geometry": declared,
					"geometry_types": types,
					"suggested_type": suggested,
					"has_z": has_z,
					"feature_count": lyr.GetFeatureCount(),
					"scanned": scanned,
					"empty_geometries": empty,
					"crs": srs_info(lyr.GetSpatialRef()),
					"fields": field_info(defn),
					"extent": extent,
				}
			)
	return {"layers": layers, "problems": problems}


# ---------------------------------------------------------------------------
# read (file -> NDJSON for Frappe)
# ---------------------------------------------------------------------------


def read(request):
	from osgeo import ogr, osr

	ds = open_vector(request["source"], request.get("csv"))
	lyr = ds.GetLayerByName(request["layer"]) if request.get("layer") else ds.GetLayer(0)
	if lyr is None:
		raise ValueError(f"Layer {request.get('layer')} not found.")

	transform = None
	if request.get("reproject_from") and request.get("reproject_to"):
		transform = osr.CoordinateTransformation(make_srs(request["reproject_from"]), make_srs(request["reproject_to"]))
	keep_z = bool(request.get("keep_z"))
	wanted = request.get("fields")  # source field names to keep (None = all)
	defn = lyr.GetLayerDefn()
	columns = [
		(i, defn.GetFieldDefn(i))
		for i in range(defn.GetFieldCount())
		if wanted is None or defn.GetFieldDefn(i).GetName() in wanted
	]

	written = 0
	skipped = []
	with open(request["out"], "w", encoding="utf-8") as out:
		for feature in lyr:
			geometry = None
			geom = feature.GetGeometryRef()
			try:
				if geom is not None and not geom.IsEmpty():
					geom = geom.Clone()
					if geom.HasCurveGeometry():
						geom = geom.GetLinearGeometry()
					geom.SetMeasured(False)
					if not keep_z:
						geom.FlattenTo2D()
					if transform is not None:
						geom.Transform(transform)
					geometry = json.loads(geom.ExportToJson())
			except Exception as e:
				skipped.append({"fid": feature.GetFID(), "message": f"geometry: {e}"})
				continue
			properties = {defn_field.GetName(): value_of(feature, i, defn_field) for i, defn_field in columns}
			out.write(json.dumps({"fid": feature.GetFID(), "geometry": geometry, "properties": properties}, default=str))
			out.write("\n")
			written += 1
	return {"written": written, "skipped": skipped[:500], "skipped_count": len(skipped)}


def value_of(feature, index, fd):
	from osgeo import ogr

	if not feature.IsFieldSetAndNotNull(index):
		return None
	kind = fd.GetType()
	if kind in (ogr.OFTInteger, ogr.OFTInteger64):
		value = feature.GetFieldAsInteger64(index)
		return bool(value) if fd.GetSubType() == ogr.OFSTBoolean else value
	if kind == ogr.OFTReal:
		value = feature.GetFieldAsDouble(index)
		return value if math.isfinite(value) else None
	if kind == ogr.OFTDate:
		y, m, d, *_ = feature.GetFieldAsDateTime(index)
		return f"{y:04d}-{m:02d}-{d:02d}"
	if kind == ogr.OFTDateTime:
		y, m, d, hh, mm, ss, _tz = feature.GetFieldAsDateTime(index)
		return f"{y:04d}-{m:02d}-{d:02d} {hh:02d}:{mm:02d}:{int(ss):02d}"
	if kind in (ogr.OFTIntegerList, ogr.OFTInteger64List, ogr.OFTRealList, ogr.OFTStringList):
		return json.dumps(feature.GetField(index))
	if kind == ogr.OFTBinary:
		return None
	return feature.GetFieldAsString(index)


# ---------------------------------------------------------------------------
# write (NDJSON from Frappe -> file)
# ---------------------------------------------------------------------------

DRIVERS = {
	"GeoPackage": ("GPKG", [], []),
	"GeoJSON": ("GeoJSON", [], ["RFC7946=NO", "WRITE_NAME=NO"]),
	"Shapefile": ("ESRI Shapefile", [], ["ENCODING=UTF-8"]),
	"KML": ("LIBKML", [], []),
	"GML": ("GML", ["FORMAT=GML3"], []),
	"CSV": ("CSV", [], ["GEOMETRY=AS_WKT", "CREATE_CSVT=YES"]),
}
FIELD_TYPES = {"Text": "OFTString", "Integer": "OFTInteger64", "Float": "OFTReal", "Boolean": "OFTInteger", "Date": "OFTDate", "Datetime": "OFTDateTime"}
GEOMETRY_TYPES = {
	"Point": "wkbPoint",
	"MultiPoint": "wkbMultiPoint",
	"LineString": "wkbLineString",
	"MultiLineString": "wkbMultiLineString",
	"Polygon": "wkbPolygon",
	"MultiPolygon": "wkbMultiPolygon",
	"Geometry": "wkbUnknown",
	"No Geometry": "wkbNone",
}


def write(request):
	from osgeo import gdal, ogr, osr

	driver_name, dataset_options, layer_options = DRIVERS[request["format"]]
	out = request["out"]
	if request.get("append") and os.path.exists(out):
		ds = gdal.OpenEx(out, gdal.OF_VECTOR | gdal.OF_UPDATE)
	else:
		ds = ogr.GetDriverByName(driver_name).CreateDataSource(out, options=dataset_options)

	native = make_srs(request["crs"])
	target = make_srs(request["target_crs"]) if request.get("target_crs") else native
	transform = None if native.IsSame(target) else osr.CoordinateTransformation(native, target)

	geometry_type = getattr(ogr, GEOMETRY_TYPES[request["geometry_type"]])
	if request.get("has_z") and geometry_type not in (ogr.wkbNone, ogr.wkbUnknown):
		geometry_type = ogr.GT_SetZ(geometry_type)
	lyr = ds.CreateLayer(
		request["layer"],
		target if geometry_type != ogr.wkbNone else None,
		geometry_type,
		options=layer_options,
	)

	fields = [{"name": "gis_feature_id", "data_type": "Integer"}, {"name": "label", "data_type": "Text"}] + request["fields"]
	for field in fields:
		fd = ogr.FieldDefn(field["name"], getattr(ogr, FIELD_TYPES[field["data_type"]]))
		if field["data_type"] == "Boolean":
			fd.SetSubType(ogr.OFSTBoolean)
		lyr.CreateField(fd)
	defn = lyr.GetLayerDefn()
	# formats such as Shapefile shorten field names: report what each became
	written_names = [defn.GetFieldDefn(i).GetName() for i in range(defn.GetFieldCount())]
	renamed = {f["name"]: n for f, n in zip(fields, written_names) if f["name"] != n}

	count, failed = 0, []
	use_transactions = lyr.TestCapability(ogr.OLCTransactions)
	if use_transactions:
		lyr.StartTransaction()
	with open(request["input"], encoding="utf-8") as source:
		for line in source:
			item = json.loads(line)
			feature = ogr.Feature(defn)
			try:
				if item.get("geometry") and geometry_type != ogr.wkbNone:
					geom = ogr.CreateGeometryFromJson(json.dumps(item["geometry"]))
					if transform is not None:
						geom.Transform(transform)
					feature.SetGeometry(geom)
				values = dict(item.get("attributes") or {}, gis_feature_id=item["id"], label=item.get("label"))
				for i, field in enumerate(fields):
					value = values.get(field.get("key", field["name"]))  # key: the dataset's attribute name
					if value is None:
						continue
					feature.SetField(i, int(value) if field["data_type"] == "Boolean" else value)
				lyr.CreateFeature(feature)
				count += 1
			except Exception as e:
				failed.append({"id": item.get("id"), "message": str(e)})
			if use_transactions and count and count % 10000 == 0:
				lyr.CommitTransaction()
				lyr.StartTransaction()
	if use_transactions:
		lyr.CommitTransaction()
	ds = None  # flush and close
	return {"written": count, "failed": failed[:500], "failed_count": len(failed), "renamed_fields": renamed}


# ---------------------------------------------------------------------------
# QGIS projects
# ---------------------------------------------------------------------------


def start_qgis():
	os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
	os.environ["QGIS_CUSTOM_CONFIG_PATH"] = tempfile.mkdtemp(prefix="isp-qgis-io-")
	from qgis.core import QgsApplication

	app = QgsApplication([], False)
	app.initQgis()
	return app


def project_write(request):
	sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
	from publish import style  # the same single-symbol style as the published QGIS Server projects

	app = start_qgis()
	from qgis.core import (
		Qgis,
		QgsCoordinateReferenceSystem,
		QgsProject,
		QgsRectangle,
		QgsReferencedRectangle,
		QgsVectorLayer,
	)

	project = QgsProject.instance()
	project.clear()
	project.setTitle(request["title"])
	project.setCrs(QgsCoordinateReferenceSystem(request.get("crs") or "EPSG:3857"))
	project.setFilePathStorage(Qgis.FilePathType.Relative)
	root = project.layerTreeRoot()
	added = []
	from urllib.parse import quote

	from qgis.core import QgsRasterLayer

	for item in request["layers"]:  # top of the layer tree first
		if item.get("kind") in ("xyz", "wms"):  # a web map service (basemap or overlay)
			if item["kind"] == "xyz":
				uri = f"type=xyz&url={quote(item['url'], safe=':/')}&zmin={int(item.get('min_zoom') or 0)}&zmax={int(item.get('max_zoom') or 19)}"
			else:
				uri = f"url={item['url']}&layers={item['layers']}&styles={item.get('styles') or ''}&format={item.get('format') or 'image/png'}&crs=EPSG:3857"
			raster = QgsRasterLayer(uri, item["title"], "wms")
			if not raster.isValid():
				continue
			raster.setOpacity(float(item.get("opacity", 1)))
			project.addMapLayer(raster, False)
			node = root.addLayer(raster)
			node.setItemVisibilityChecked(bool(item.get("visible", True)))
			added.append(item["name"])
			continue
		layer = QgsVectorLayer(f"{request['gpkg']}|layername={item['name']}", item["title"], "ogr")
		if not layer.isValid():
			continue
		if item.get("geometry_type") not in (None, "No Geometry"):
			style(layer, item["geometry_type"], item.get("color"))
		layer.setOpacity(float(item.get("opacity", 1)))
		project.addMapLayer(layer, False)
		node = root.addLayer(layer)
		node.setItemVisibilityChecked(bool(item.get("visible", True)))
		added.append(item["name"])
	extent = request.get("extent")
	if extent and all(v is not None for v in extent):
		project.viewSettings().setDefaultViewExtent(
			QgsReferencedRectangle(QgsRectangle(*extent), QgsCoordinateReferenceSystem("EPSG:3857"))
		)
	if not project.write(request["qgz"]):
		raise RuntimeError(f"QGIS could not write the project: {project.error()}")
	app.exitQgis()
	return {"layers": added}


def project_read(request):
	app = start_qgis()
	from qgis.core import (
		QgsCoordinateReferenceSystem,
		QgsCoordinateTransform,
		QgsProject,
		QgsProviderRegistry,
		QgsSingleSymbolRenderer,
		QgsVectorLayer,
	)

	root_dir = os.path.realpath(request["root"])
	project = QgsProject.instance()
	if not project.read(request["qgz"]):
		raise RuntimeError(f"QGIS could not read the project: {project.error()}")

	layers, unsupported = [], []
	for node in project.layerTreeRoot().findLayers():  # top of the tree first
		layer = node.layer()
		name = node.name()
		if not isinstance(layer, QgsVectorLayer) or layer.providerType() != "ogr":
			unsupported.append({"name": name, "reason": "not a file-based vector layer"})
			continue
		parts = QgsProviderRegistry.instance().decodeUri("ogr", layer.source())
		path = os.path.realpath(parts.get("path") or "")
		if not path.startswith(root_dir + os.sep) or not os.path.exists(path):
			unsupported.append({"name": name, "reason": "its data is not inside the package"})
			continue
		color = None
		renderer = layer.renderer()
		if isinstance(renderer, QgsSingleSymbolRenderer) and renderer.symbol():
			color = renderer.symbol().color().name()
		layers.append(
			{
				"title": name,
				"path": os.path.relpath(path, root_dir),
				"layer": parts.get("layerName") or None,
				"visible": node.isVisible(),
				"opacity": layer.opacity(),
				"color": color,
			}
		)

	extent = None
	view = project.viewSettings().defaultViewExtent()
	if not view.isEmpty():
		to_web = QgsCoordinateTransform(view.crs(), QgsCoordinateReferenceSystem("EPSG:3857"), project)
		box = to_web.transformBoundingBox(view)
		extent = [box.xMinimum(), box.yMinimum(), box.xMaximum(), box.yMaximum()]
	report = {"title": project.title(), "crs": project.crs().authid(), "extent": extent, "layers": layers, "unsupported": unsupported}
	app.exitQgis()
	return report


COMMANDS = {"inspect": inspect, "read": read, "write": write, "project_write": project_write, "project_read": project_read}


if __name__ == "__main__":
	main()
