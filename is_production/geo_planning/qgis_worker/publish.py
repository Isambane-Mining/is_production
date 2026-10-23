"""Write the QGIS Server project for one GIS Dataset.

Runs under the system Python that has python3-qgis (``/usr/bin/python3 -I publish.py``), never inside
the bench, and does not import Frappe. Reads a JSON request on stdin::

    {
      "db": {"host", "port", "name", "user", "password"},
      "output": "<project base>/<site>/<dataset>.qgs",
      "dataset": {"name", "title", "geometry_type", "crs", "has_z", "color",
                  "extent": [min_x, min_y, max_x, max_y] | null,
                  "attributes": [{"field_name", "data_type", "label"}]}
    }

and prints one JSON report on stdout. The project reads features live from ``tabGIS Feature``
(OGR MySQL driver, read-only account) filtered to the dataset, so feature edits need no republish.

Each attribute becomes a typed expression field read from the ``attributes`` JSON column. Internal
columns (raw JSON, Frappe bookkeeping) are hidden from WMS and WFS. The file is written to a temporary
name, checked by loading it back, and then moved into place with mode 0640 (it holds the read-only
account's password; QGIS Server reads it through the folder's group, which is QGIS Server's own).

This file imports QGIS (GPL-2.0-or-later); see the licence note in the app README.
"""

import json
import os
import shutil
import sys
import tempfile

FEATURE_TABLE = "tabGIS Feature"
MAP_CRS = "EPSG:3857"
VISIBLE_COLUMNS = {"name", "feature_label", "revision", "validation_status", "min_z", "max_z"}
COLUMN_ALIASES = {
	"name": "Feature ID",
	"feature_label": "Label",
	"revision": "Revision",
	"validation_status": "Validation",
	"min_z": "Min Z",
	"max_z": "Max Z",
}
SINGLE_TYPES = ["Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon"]
EXPRESSIONS = {
	"Text": ("map_get(json_to_map(\"attributes\"), '{0}')", "QString"),
	"Integer": ("to_int(map_get(json_to_map(\"attributes\"), '{0}'))", "LongLong"),
	"Float": ("to_real(map_get(json_to_map(\"attributes\"), '{0}'))", "Double"),
	"Boolean": ("map_get(json_to_map(\"attributes\"), '{0}')", "Bool"),
	"Date": ("to_date(map_get(json_to_map(\"attributes\"), '{0}'))", "QDate"),
	"Datetime": ("to_datetime(map_get(json_to_map(\"attributes\"), '{0}'))", "QDateTime"),
}


def main():
	request = json.loads(sys.stdin.read() or "{}")
	secret = (request.get("db") or {}).get("password") or ""

	def scrub(message):
		message = str(message)
		return message.replace(secret, "********") if secret else message

	os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
	os.environ["QGIS_CUSTOM_CONFIG_PATH"] = tempfile.mkdtemp(prefix="isp-qgis-publish-")

	from qgis.core import QgsApplication

	app = QgsApplication([], False)
	app.initQgis()
	try:
		report = publish(request)
	except Exception as e:
		report = {"ok": False, "message": scrub(e)}
	app.exitQgis()
	print(json.dumps(report))


def publish(request):
	from qgis.core import QgsCoordinateReferenceSystem, QgsProject

	dataset = request["dataset"]
	output = request["output"]
	project = QgsProject.instance()
	project.clear()
	project.setCrs(QgsCoordinateReferenceSystem(MAP_CRS))
	project.setTitle(dataset["title"])

	types = SINGLE_TYPES if dataset["geometry_type"] == "Geometry" else [dataset["geometry_type"]]
	layers = []
	for geometry_type in types:
		short_name = dataset["name"] if len(types) == 1 else f"{dataset['name']}_{geometry_type.lower()}"
		layer = make_layer(request["db"], dataset, geometry_type, short_name)
		if not layer.isValid():
			return {"ok": False, "message": f"QGIS could not open {FEATURE_TABLE} as a {geometry_type} layer."}
		layers.append(layer)

	root = project.layerTreeRoot()
	if len(layers) == 1:
		project.addMapLayer(layers[0])
	else:
		group = root.addGroup(dataset["title"])
		set_short_name(group, dataset["name"])
		for layer in layers:
			project.addMapLayer(layer, False)
			group.addLayer(layer)

	configure_services(project, dataset, layers)

	# QGIS also writes a backup (.qgs~) and an attachments zip next to the file: write in a private
	# temporary directory beside the output, move only the .qgs into place, then drop the rest.
	workdir = tempfile.mkdtemp(prefix=f".publish-{dataset['name']}-", dir=os.path.dirname(output))
	temporary = os.path.join(workdir, os.path.basename(output))
	try:
		if not project.write(temporary):
			return {"ok": False, "message": f"QGIS could not write the project: {project.error()}"}
		check = verify(temporary)
		if not check["ok"]:
			return check
		os.chmod(temporary, 0o640)
		os.replace(temporary, output)
	finally:
		shutil.rmtree(workdir, ignore_errors=True)

	return {
		"ok": True,
		"path": output,
		"layers": [layer_short_name(layer) for layer in layers],
		"wms_name": dataset["name"],
		"feature_count": check["feature_count"],
	}


def make_layer(db, dataset, geometry_type, short_name):
	from qgis.core import QgsCoordinateReferenceSystem, QgsVectorLayer

	parts = [db.get("name") or "", f"host={db.get('host') or 'localhost'}"]
	if db.get("port"):
		parts.append(f"port={db['port']}")
	parts += [f"user={db['user']}", f"password={db.get('password') or ''}", f"tables={FEATURE_TABLE}"]
	uri = "MySQL:" + ",".join(parts) + f"|layername={FEATURE_TABLE}|geometrytype={geometry_type}"

	title = dataset["title"] if dataset["geometry_type"] != "Geometry" else f"{dataset['title']} ({geometry_type})"
	layer = QgsVectorLayer(uri, title, "ogr")
	if not layer.isValid():
		return layer
	# passed to MariaDB as a WHERE clause: backticks, not double quotes (those are string literals there)
	layer.setSubsetString("`dataset` = '{0}'".format(dataset["name"].replace("'", "''")))
	layer.setCrs(QgsCoordinateReferenceSystem(dataset["crs"]))
	set_short_name(layer, short_name)
	layer.setDisplayExpression("coalesce(\"feature_label\", '#' || \"name\")")

	add_attribute_fields(layer, dataset.get("attributes") or [])
	hide_internal_columns(layer, dataset)
	style(layer, geometry_type, dataset.get("color"))
	return layer


def add_attribute_fields(layer, attributes):
	from qgis.core import QgsField
	from qgis.PyQt.QtCore import QMetaType

	types = getattr(QMetaType, "Type", QMetaType)
	for attribute in attributes:
		expression, type_name = EXPRESSIONS.get(attribute["data_type"], EXPRESSIONS["Text"])
		field = QgsField(attribute["field_name"], getattr(types, type_name))
		index = layer.addExpressionField(expression.format(attribute["field_name"].replace("'", "''")), field)
		if attribute.get("label"):
			layer.setFieldAlias(index, attribute["label"])


def hide_internal_columns(layer, dataset):
	from qgis.core import Qgis

	visible = set(VISIBLE_COLUMNS)
	if not dataset.get("has_z"):
		visible -= {"min_z", "max_z"}
	expression_fields = {a["field_name"] for a in dataset.get("attributes") or []}
	flags = Qgis.FieldConfigurationFlag
	for index, field in enumerate(layer.fields()):
		name = field.name()
		if name in COLUMN_ALIASES:
			layer.setFieldAlias(index, COLUMN_ALIASES[name])
		if name not in visible and name not in expression_fields:
			layer.setFieldConfigurationFlag(index, flags.HideFromWms, True)
			layer.setFieldConfigurationFlag(index, flags.HideFromWfs, True)


def style(layer, geometry_type, color):
	from qgis.core import QgsSingleSymbolRenderer, QgsSymbol, QgsUnitTypes
	from qgis.PyQt.QtGui import QColor

	base = QColor(color or "#2f80ed")
	symbol = QgsSymbol.defaultSymbol(layer.geometryType())
	if "Polygon" in geometry_type:
		fill = QColor(base)
		fill.setAlpha(70)
		symbol.setColor(fill)
		outline = symbol.symbolLayer(0)
		outline.setStrokeColor(base)
		outline.setStrokeWidth(0.5)
	elif "LineString" in geometry_type:
		symbol.setColor(base)
		symbol.setWidth(0.7)
	else:
		symbol.setColor(base)
		symbol.setSize(3.0)
		symbol.symbolLayer(0).setStrokeColor(QColor("#ffffff"))
	symbol.setOutputUnit(QgsUnitTypes.RenderMillimeters)
	layer.setRenderer(QgsSingleSymbolRenderer(symbol))


def set_short_name(node, short_name):
	if hasattr(node, "serverProperties"):
		node.serverProperties().setShortName(short_name)
	elif hasattr(node, "setShortName"):
		node.setShortName(short_name)
	else:  # layer tree group
		node.setCustomProperty("wmsShortName", short_name)


def layer_short_name(layer):
	return layer.serverProperties().shortName() if hasattr(layer, "serverProperties") else layer.shortName()


def configure_services(project, dataset, layers):
	from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsRectangle

	project.writeEntry("WMSServiceCapabilities", "/", True)
	project.writeEntry("WMSServiceTitle", "/", dataset["title"])
	project.writeEntry("WMSCrsList", "/", sorted({MAP_CRS, "EPSG:4326", dataset["crs"]}))
	project.writeEntry("WMSUseLayerIDs", "/", False)
	project.writeEntry("WMSAddWktGeometry", "/", False)
	project.writeEntry("WMSPrecision", "/", "4")
	project.writeEntry("WMSFeatureInfoUseAttributeFormSettings", "/", False)
	# read-only WFS for feature retrieval; no WFS-T layers
	project.writeEntry("WFSLayers", "/", [layer.id() for layer in layers])
	for layer in layers:
		project.writeEntry("WFSLayersPrecision", "/" + layer.id(), 4)

	extent = dataset.get("extent")
	if extent and all(v is not None for v in extent) and extent[0] <= extent[2] and extent[1] <= extent[3]:
		transform = QgsCoordinateTransform(
			QgsCoordinateReferenceSystem(dataset["crs"]), QgsCoordinateReferenceSystem(MAP_CRS), project
		)
		box = transform.transformBoundingBox(QgsRectangle(*extent))
		box.grow(max(box.width(), box.height(), 100) * 0.05)
		project.writeEntry(
			"WMSExtent",
			"/",
			[repr(v) for v in (box.xMinimum(), box.yMinimum(), box.xMaximum(), box.yMaximum())],
		)


def verify(path):
	"""Load the written project back, as QGIS Server will, and count the features."""
	from qgis.core import QgsProject

	project = QgsProject()
	if not project.read(path):
		return {"ok": False, "message": f"The written project does not load: {project.error()}"}
	count = 0
	for layer in project.mapLayers().values():
		if not layer.isValid():
			return {"ok": False, "message": f"Layer {layer.name()} is not valid when the project is reloaded."}
		count += max(layer.featureCount(), 0)
	return {"ok": True, "feature_count": count}


if __name__ == "__main__":
	main()
