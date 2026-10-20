"""QGIS Processing worker: describe the installed algorithms, or run one.

Runs under the system Python that has python3-qgis (``/usr/bin/python3 -I processing_worker.py``), never
inside the bench, and does not import Frappe. Reads one JSON request on stdin.

- ``describe``: every algorithm of every provider (native, GDAL, GRASS, …) with its parameter and output
  definitions, as one JSON report on stdout. Frappe caches it as the algorithm registry.
- ``run``: run one algorithm with parameters that already point at files in the job's workspace (Frappe
  exports the input datasets there first, and chooses the output paths). While it runs, it prints JSON
  lines ``{"progress": 0-100}`` and ``{"log": "..."}``; the last line is ``{"result": {...}}`` or
  ``{"error": "..."}``. SIGTERM cancels the algorithm (Frappe sends it when the user cancels).

This file imports QGIS (GPL-2.0-or-later); see the licence note in the app README.
"""

import json
import math
import os
import signal
import sys
import tempfile

# parameter types the web toolbox can fill in (others: the algorithm is listed but not runnable
# when such a parameter is required)
SUPPORTED_TYPES = {
	"source", "vector", "raster", "multilayer", "number", "distance", "scale", "duration", "enum", "boolean",
	"string", "expression", "field", "crs", "extent", "point", "band", "sink", "vectorDestination",
	"rasterDestination", "fileDestination", "datetime", "color", "range", "maplayer", "layer", "area",
}


def emit(**data):
	sys.stdout.write(json.dumps(data, default=str) + "\n")
	sys.stdout.flush()


def start_qgis():
	os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
	os.environ["QGIS_CUSTOM_CONFIG_PATH"] = tempfile.mkdtemp(prefix="isp-qgis-processing-")
	from qgis.core import QgsApplication

	app = QgsApplication([], False)
	app.initQgis()
	sys.path.append(os.path.join(QgsApplication.pkgDataPath(), "python", "plugins"))
	from processing.core.Processing import Processing

	Processing.initialize()
	return app


def load_plugins(plugins):
	"""Start enabled processing plugins (GIS Plugin) in this worker process only.

	Returns ({id: error}, {id: [provider ids it added]}).
	"""
	errors, providers = {}, {}
	if not plugins:
		return errors, providers
	import qgis.utils as qgis_utils
	from qgis.core import QgsApplication

	registry = QgsApplication.processingRegistry()

	for plugin in plugins:
		parent = plugin["parent"]
		if parent not in sys.path:
			sys.path.insert(0, parent)
		if parent not in qgis_utils.plugin_paths:
			qgis_utils.plugin_paths.insert(0, parent)
	qgis_utils.updateAvailablePlugins()
	for plugin in plugins:
		before = {p.id() for p in registry.providers()}
		try:
			if not qgis_utils.loadPlugin(plugin["id"]):
				raise RuntimeError("QGIS could not load it")
			if not qgis_utils.startProcessingPlugin(plugin["id"]):
				raise RuntimeError("it did not start a processing provider")
		except Exception as e:
			errors[plugin["id"]] = str(e)
		providers[plugin["id"]] = sorted(p.id() for p in registry.providers() if p.id() not in before)
	return errors, providers


def plain(value):
	"""A QGIS/Qt value as JSON."""
	if value is None:
		return None
	if isinstance(value, (bool, int, str)):
		return value
	if isinstance(value, float):
		return value if math.isfinite(value) else None
	if isinstance(value, (list, tuple)):
		return [plain(v) for v in value]
	if isinstance(value, dict):
		return {str(k): plain(v) for k, v in value.items()}
	for method in ("source", "authid", "toString"):
		if hasattr(value, method):
			try:
				return getattr(value, method)()
			except TypeError:
				pass
	return str(value)


def flag(obj, *names):
	"""True if a QGIS flag is set, across the old (class) and new (Qgis.*) enum locations."""
	from qgis.core import Qgis

	flags = obj.flags()
	for name in names:
		for holder in (getattr(Qgis, "ProcessingParameterFlag", None), getattr(Qgis, "ProcessingAlgorithmFlag", None), type(obj)):
			enum = getattr(holder, name, None) if holder is not None else None
			if enum is not None:
				try:
					if flags & enum:
						return True
				except TypeError:
					pass
	return False


def describe_parameter(p):
	info = {
		"name": p.name(),
		"description": p.description(),
		"type": p.type(),
		"optional": flag(p, "Optional", "FlagOptional"),
		"advanced": flag(p, "Advanced", "FlagAdvanced"),
		"hidden": flag(p, "Hidden", "FlagHidden"),
		"default": plain(p.defaultValue()),
		"help": p.help() if hasattr(p, "help") else "",
	}
	kind = p.type()
	try:
		if kind in ("number", "distance", "scale", "duration"):
			info["integer"] = int(p.dataType()) == 0 if hasattr(p, "dataType") else False
			info["minimum"] = plain(p.minimum())
			info["maximum"] = plain(p.maximum())
			if kind == "distance":
				info["parent"] = p.parentParameterName()
		elif kind == "enum":
			info["options"] = list(p.options())
			info["multiple"] = p.allowMultiple()
			info["static_strings"] = p.usesStaticStrings() if hasattr(p, "usesStaticStrings") else False
		elif kind == "field":
			info["parent"] = p.parentLayerParameterName()
			info["multiple"] = p.allowMultiple()
			info["field_type"] = int(p.dataType())
		elif kind in ("source", "vector", "multilayer"):
			info["data_types"] = [int(t) for t in p.dataTypes()] if hasattr(p, "dataTypes") else []
			if kind == "multilayer":
				info["layer_type"] = int(p.layerType())
		elif kind == "string":
			info["multiline"] = p.multiLine()
		elif kind == "expression":
			info["parent"] = p.parentLayerParameterName()
		elif kind == "band":
			info["parent"] = p.parentLayerParameterName()
		elif kind in ("sink", "vectorDestination"):
			info["data_type"] = int(p.dataType())
		elif kind in ("rasterDestination", "fileDestination"):
			info["extension"] = p.defaultFileExtension()
	except Exception as e:  # a provider-specific parameter class without one of these methods
		info["describe_error"] = str(e)
	return info


def describe(request):
	from qgis.core import QgsApplication, Qgis

	algorithms = []
	for provider in QgsApplication.processingRegistry().providers():
		for alg in provider.algorithms():
			params = [describe_parameter(p) for p in alg.parameterDefinitions()]
			outputs = [{"name": o.name(), "description": o.description(), "type": o.type()} for o in alg.outputDefinitions()]
			blocking = [p for p in params if not p["optional"] and p["type"] not in SUPPORTED_TYPES]
			hidden = flag(alg, "HideFromToolbox", "FlagHideFromToolbox")
			reasons = []
			if blocking:
				reasons.append("needs " + ", ".join(sorted({p["type"] for p in blocking})) + " input")
			if flag(alg, "NotAvailableInStandaloneTool", "FlagNotAvailableInStandaloneTool"):
				reasons.append("only runs inside QGIS Desktop")
			if flag(alg, "RequiresProject", "FlagRequiresProject"):
				reasons.append("needs a QGIS Desktop project")
			algorithms.append(
				{
					"id": alg.id(),
					"name": alg.displayName(),
					"group": alg.group() or "",
					"provider": provider.id(),
					"provider_name": provider.name(),
					"description": alg.shortDescription() or "",
					"help": (alg.shortHelpString() or "")[:4000],
					"tags": list(alg.tags()),
					"deprecated": flag(alg, "Deprecated", "FlagDeprecated"),
					"hidden": hidden,
					"supported": not reasons,
					"unsupported_reason": "; ".join(reasons),
					"parameters": params,
					"outputs": outputs,
				}
			)
	return {"qgis_version": Qgis.version(), "algorithms": algorithms, "plugin_errors": request.get("_plugin_errors", {}),
		"plugin_providers": request.get("_plugin_providers", {})}


class Feedback:
	"""Build a QgsProcessingFeedback that streams progress and log lines as JSON."""

	def __new__(cls):
		from qgis.core import QgsProcessingFeedback

		class _Feedback(QgsProcessingFeedback):
			def __init__(self):
				super().__init__()
				self._last = -1
				self.progressChanged.connect(self._progress)

			def _progress(self, value):
				if int(value) != self._last:
					self._last = int(value)
					emit(progress=self._last)

			def pushInfo(self, info):
				emit(log=info)

			def pushWarning(self, warning):
				emit(log="Warning: " + warning)

			def reportError(self, error, fatalError=False):
				emit(log="Error: " + error)

			def pushCommandInfo(self, info):
				emit(log=info)

			def pushConsoleInfo(self, info):
				emit(log=info)

		return _Feedback()


def run(request):
	from qgis.core import (
		QgsApplication,
		QgsCoordinateReferenceSystem,
		QgsProcessingContext,
		QgsProject,
		QgsRasterLayer,
		QgsVectorLayer,
	)

	alg = QgsApplication.processingRegistry().createAlgorithmById(request["algorithm"])
	if alg is None:
		emit(error=f"Algorithm {request['algorithm']} is not installed.")
		return
	project = QgsProject.instance()
	if request.get("crs"):
		project.setCrs(QgsCoordinateReferenceSystem(request["crs"]))
	context = QgsProcessingContext()
	context.setProject(project)
	feedback = Feedback()
	signal.signal(signal.SIGTERM, lambda *_: feedback.cancel())

	parameters = request["parameters"]
	ok, message = alg.checkParameterValues(parameters, context)
	if not ok:
		emit(error=message)
		return

	import processing

	try:
		results = processing.run(alg, parameters, context=context, feedback=feedback)
	except Exception as e:
		emit(error="Cancelled." if feedback.isCanceled() else str(e))
		return
	if feedback.isCanceled():
		emit(error="Cancelled.")
		return

	outputs = {}
	for name, value in results.items():
		if isinstance(value, (QgsVectorLayer, QgsRasterLayer)):
			value = value.source()
		outputs[name] = plain(value)
	emit(result=outputs)


def raster_info(request):
	from osgeo import gdal, osr

	gdal.UseExceptions()
	ds = gdal.Open(request["path"])
	srs = osr.SpatialReference(wkt=ds.GetProjection()) if ds.GetProjection() else None
	code = None
	if srs is not None:
		srs.AutoIdentifyEPSG()
		code = srs.GetAuthorityCode(None)
	return {"epsg": f"EPSG:{code}" if code else None, "width": ds.RasterXSize, "height": ds.RasterYSize, "bands": ds.RasterCount}


def main():
	request = json.loads(sys.stdin.read() or "{}")
	command = request.get("command")
	if command == "raster_info":
		emit(**raster_info(request))
		return
	if request.get("nice"):
		os.nice(int(request["nice"]))
	if request.get("memory_limit_mb"):
		import resource

		limit = int(request["memory_limit_mb"]) * 1024 * 1024
		resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
	app = start_qgis()
	try:
		from qgis.core import QgsApplication

		request["_plugin_errors"], request["_plugin_providers"] = load_plugins(request.get("plugins"))
		if command == "describe":
			emit(**describe(request))
		elif command == "run":
			run(request)
		else:
			emit(error=f"Unknown command {command}.")
	finally:
		app.exitQgis()


if __name__ == "__main__":
	main()
