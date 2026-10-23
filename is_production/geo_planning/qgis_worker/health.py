"""PyQGIS health check for the GIS Workstation.

Runs under the system Python that has python3-qgis (``/usr/bin/python3 -I health.py``), never
inside the bench: the bench's Python cannot import the QGIS bindings. It does not import Frappe.

Reads a JSON request on stdin::

    {"db": {"host": ..., "port": ..., "name": ..., "user": ..., "password": ...}}   (db optional)

and prints one JSON report on stdout. The password is never echoed back; any message that might
contain the connection string is scrubbed first.

This file imports QGIS (GPL-2.0-or-later); see the licence note in the app README.
"""

import json
import os
import sys
import tempfile

FEATURE_TABLE = "tabGIS Feature"


def main():
	request = json.loads(sys.stdin.read() or "{}")
	db = request.get("db") or {}
	secret = db.get("password") or ""

	def scrub(message):
		message = str(message)
		return message.replace(secret, "********") if secret else message

	report = {"python": sys.version.split()[0]}
	os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
	# a throwaway QGIS profile, so the check never writes into a user's QGIS settings
	os.environ["QGIS_CUSTOM_CONFIG_PATH"] = tempfile.mkdtemp(prefix="isp-qgis-health-")

	try:
		from qgis.core import Qgis, QgsApplication, QgsProjUtils, QgsVectorLayer
	except Exception as e:
		report["pyqgis"] = {"ok": False, "message": f"Cannot import qgis.core: {e}"}
		print(json.dumps(report))
		return

	app = QgsApplication([], False)
	app.initQgis()
	report["pyqgis"] = {"ok": True, "version": Qgis.version()}
	report["proj"] = {"version": f"{QgsProjUtils.projVersionMajor()}.{QgsProjUtils.projVersionMinor()}"}

	try:
		from osgeo import gdal, ogr

		gdal.UseExceptions()
		report["gdal"] = {"version": gdal.__version__, "mysql_driver": ogr.GetDriverByName("MySQL") is not None}
	except Exception as e:
		report["gdal"] = {"version": None, "mysql_driver": False, "message": str(e)}

	report["processing"] = processing_providers(QgsApplication)

	if db.get("user"):
		report["database"] = check_database(db, scrub, QgsVectorLayer)

	app.exitQgis()
	print(json.dumps(report))


def processing_providers(QgsApplication):
	try:
		sys.path.append(os.path.join(QgsApplication.pkgDataPath(), "python", "plugins"))
		from processing.core.Processing import Processing

		Processing.initialize()
		providers = [
			{"id": p.id(), "name": p.name(), "algorithms": len(p.algorithms())}
			for p in QgsApplication.processingRegistry().providers()
		]
		return {"ok": True, "providers": providers}
	except Exception as e:
		return {"ok": False, "providers": [], "message": str(e)}


def check_database(db, scrub, QgsVectorLayer):
	from osgeo import gdal

	parts = [db.get("name") or "", f"host={db.get('host') or 'localhost'}"]
	if db.get("port"):
		parts.append(f"port={db['port']}")
	parts += [f"user={db['user']}", f"password={db.get('password') or ''}", f"tables={FEATURE_TABLE}"]
	connection = "MySQL:" + ",".join(parts)
	result = {"ok": False}

	try:
		ds = gdal.OpenEx(connection, gdal.OF_VECTOR | gdal.OF_READONLY)
		layer = ds.GetLayerByName(FEATURE_TABLE)
		if layer is None:
			result["message"] = f"{FEATURE_TABLE} is not readable by {db['user']}."
			return result
		result.update(
			{
				"ok": True,
				"feature_count": layer.GetFeatureCount(),
				"fid_column": layer.GetFIDColumn(),
				"geometry_column": layer.GetGeometryColumn(),
			}
		)
		result["limited"] = not can_read_other_tables(ds)
	except Exception as e:
		result["message"] = scrub(e)
		return result

	# QGIS itself (the OGR provider) must open the same source as a layer
	layer = QgsVectorLayer(f"{connection}|layername={FEATURE_TABLE}", "health", "ogr")
	result["qgis_layer_valid"] = layer.isValid()
	if not layer.isValid():
		result["ok"] = False
		result["message"] = "GDAL can read the table, but QGIS could not open it as a layer."
	return result


def can_read_other_tables(ds):
	"""True if the account can read a Frappe table other than tabGIS Feature (it should not)."""
	from osgeo import gdal

	try:
		with gdal.quiet_errors():
			sql = ds.ExecuteSQL("SELECT name FROM `tabUser` LIMIT 1")
		if sql is None:
			return False
		ds.ReleaseResultSet(sql)
		return True
	except Exception:
		return False


if __name__ == "__main__":
	main()
