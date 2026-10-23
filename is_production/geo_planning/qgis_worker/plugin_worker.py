"""QGIS plugin package analysis and installation.

Runs under the system Python (``/usr/bin/python3 -I plugin_worker.py``), never inside the bench, and does
not import Frappe. Plugin code is **never imported or run here**: the analysis reads the zip and parses
the Python source with ``ast`` only.

- ``analyse`` {zip, max_bytes}: check the package is a safe QGIS plugin zip (one top-level package with
  ``__init__.py`` and ``metadata.txt``, no absolute paths, no ``..``, no links, size-capped), read its
  metadata, scan its source (imports, Qt widgets / ``iface`` use, processing provider, server entry
  point, compiled modules), check its Python dependencies and QGIS version range, and classify it:
  Native server-compatible, Processing-compatible, Browser-adapter compatible, Requires manual
  adaptation, or Unsupported.
- ``extract`` {zip, destination, plugin_id, max_bytes}: unpack the package (same checks) into
  ``destination/plugin_id`` through a temporary folder, replacing an older version.

This file imports QGIS (GPL-2.0-or-later) only to read the installed QGIS version.
"""

import ast
import configparser
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import zipfile

SYSTEM_MODULES = {"qgis", "PyQt5", "PyQt6", "sip", "osgeo", "processing"}
GUI_MODULES = ("qgis.gui", "qgis.PyQt.QtWidgets", "PyQt5.QtWidgets", "PyQt6.QtWidgets", "qgis.PyQt.QtWebKitWidgets", "qgis.utils")
COMPILED = (".so", ".pyd", ".dll", ".dylib")


def emit(data):
	print(json.dumps(data, default=str))


def main():
	request = json.loads(sys.stdin.read() or "{}")
	try:
		if request["command"] == "analyse":
			emit({"ok": True, **analyse(request)})
		elif request["command"] == "extract":
			emit({"ok": True, **extract(request)})
		else:
			emit({"ok": False, "message": "Unknown command."})
	except PackageError as e:
		emit({"ok": False, "message": str(e)})


class PackageError(Exception):
	pass


# ---------------------------------------------------------------------------
# package checks
# ---------------------------------------------------------------------------


def check_package(path, max_bytes):
	if not zipfile.is_zipfile(path):
		raise PackageError("The package is not a zip file.")
	with zipfile.ZipFile(path) as archive:
		members = [m for m in archive.infolist() if not m.filename.startswith("__MACOSX/")]
		total = 0
		tops = set()
		for m in members:
			name = m.filename
			if name.startswith("/") or "\\" in name or ".." in name.split("/"):
				raise PackageError(f"Unsafe path in the package: {name}")
			if stat.S_ISLNK(m.external_attr >> 16):
				raise PackageError(f"The package contains a link: {name}")
			total += m.file_size
			if total > max_bytes:
				raise PackageError("The package is larger than allowed (GIS Plugin Settings).")
			tops.add(name.split("/")[0])
		if len(tops) != 1:
			raise PackageError("A QGIS plugin zip must contain exactly one top-level folder (the plugin package).")
		top = tops.pop()
		names = {m.filename for m in members}
		if f"{top}/__init__.py" not in names or f"{top}/metadata.txt" not in names:
			raise PackageError(f"{top}/ must contain __init__.py and metadata.txt.")
		if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", top):
			raise PackageError(f"{top} is not a valid Python package name.")
		return top, members


def sha256(path):
	digest = hashlib.sha256()
	with open(path, "rb") as f:
		for block in iter(lambda: f.read(1 << 20), b""):
			digest.update(block)
	return digest.hexdigest()


# ---------------------------------------------------------------------------
# analyse
# ---------------------------------------------------------------------------


def analyse(request):
	path = request["zip"]
	top, members = check_package(path, int(request.get("max_bytes") or 50 * 1024 * 1024))
	with zipfile.ZipFile(path) as archive:
		metadata = read_metadata(archive.read(f"{top}/metadata.txt").decode("utf-8", "replace"))
		sources = {
			m.filename: archive.read(m.filename).decode("utf-8", "replace")
			for m in members
			if m.filename.endswith(".py")
		}
	compiled = sorted(m.filename for m in members if m.filename.lower().endswith(COMPILED))

	scan = scan_sources(top, sources)
	dependencies = []
	for module in sorted(scan["external_imports"]):
		found = importlib.util.find_spec(module) is not None  # locates; does not import
		dependencies.append({"dependency": module, "kind": "Python Module", "requirement": "", "satisfied": found,
			"detail": "available in the system Python" if found else "not installed for the system Python"})
	for plugin in [p.strip() for p in (metadata.get("plugin_dependencies") or "").split(",") if p.strip()]:
		dependencies.append({"dependency": plugin, "kind": "QGIS Plugin", "requirement": "", "satisfied": False, "detail": "install it as a GIS Plugin too"})

	qgis_version = installed_qgis_version()
	minimum, maximum = metadata.get("qgisminimumversion") or "", metadata.get("qgismaximumversion") or ""
	version_ok = qgis_version is None or (
		(not minimum or version_tuple(qgis_version) >= version_tuple(minimum))
		and (not maximum or version_tuple(qgis_version) <= version_tuple(maximum, upper=True))
	)
	dependencies.append({"dependency": "QGIS", "kind": "QGIS Version", "requirement": f"{minimum or '?'} – {maximum or '3.99'}",
		"satisfied": version_ok, "detail": f"installed {qgis_version}"})

	status, reasons = classify(metadata, scan, compiled, dependencies, version_ok)
	return {
		"plugin_id": top,
		"metadata": metadata,
		"checksum": sha256(path),
		"size": os.path.getsize(path),
		"files": len(members),
		"compiled": compiled,
		"scan": {k: sorted(v) if isinstance(v, set) else v for k, v in scan.items()},
		"dependencies": dependencies,
		"qgis_version": qgis_version,
		"compatibility": status,
		"reasons": reasons,
	}


def read_metadata(text):
	parser = configparser.ConfigParser(interpolation=None, strict=False)
	parser.read_string(text)
	if not parser.has_section("general"):
		raise PackageError("metadata.txt has no [general] section.")
	return {k.lower(): v.strip() for k, v in parser.items("general")}


def scan_sources(top, sources):
	scan = {
		"imports": set(),
		"external_imports": set(),
		"gui_imports": set(),
		"uses_iface": False,
		"processing_provider": False,
		"processing_algorithms": False,
		"init_processing": False,
		"class_factory": False,
		"server_class_factory": False,
		"syntax_errors": [],
	}
	own = {top}
	for path in sources:
		parts = path[: -len(".py")].split("/")
		own.update(parts[1:2])  # sub-modules imported absolutely by short name
	for path, source in sources.items():
		try:
			tree = ast.parse(source, filename=path)
		except SyntaxError as e:
			scan["syntax_errors"].append(f"{path}:{e.lineno}: {e.msg}")
			continue
		for node in ast.walk(tree):
			if isinstance(node, ast.Import):
				names = [a.name for a in node.names]
			elif isinstance(node, ast.ImportFrom):
				if node.level:  # relative import: the plugin's own code
					continue
				base = node.module or ""
				names = [base] + [f"{base}.{a.name}" for a in node.names]
			else:
				names = []
			for name in names:
				if not name:
					continue
				scan["imports"].add(name)
				root = name.split(".")[0]
				if any(name == g or name.startswith(g + ".") for g in GUI_MODULES):
					scan["gui_imports"].add(name)
				if root not in own and root not in SYSTEM_MODULES and root not in sys.stdlib_module_names:
					scan["external_imports"].add(root)
			if isinstance(node, ast.Name) and node.id == "iface" or isinstance(node, ast.Attribute) and node.attr == "iface":
				scan["uses_iface"] = True
			if isinstance(node, ast.ClassDef):
				bases = {getattr(b, "attr", getattr(b, "id", "")) for b in node.bases}
				if "QgsProcessingProvider" in bases:
					scan["processing_provider"] = True
				if bases & {"QgsProcessingAlgorithm", "QgsProcessingFeatureBasedAlgorithm"}:
					scan["processing_algorithms"] = True
			if isinstance(node, ast.FunctionDef):
				if node.name == "initProcessing":
					scan["init_processing"] = True
				elif node.name == "classFactory":
					scan["class_factory"] = True
				elif node.name == "serverClassFactory":
					scan["server_class_factory"] = True
	return scan


def classify(metadata, scan, compiled, dependencies, version_ok):
	reasons = []
	unsupported = []
	if scan["syntax_errors"]:
		unsupported.append("its Python does not parse: " + "; ".join(scan["syntax_errors"][:3]))
	if not version_ok:
		unsupported.append("it does not support the installed QGIS version")
	missing = [d["dependency"] for d in dependencies if d["kind"] == "Python Module" and not d["satisfied"]]
	if missing:
		unsupported.append("missing Python modules: " + ", ".join(missing))
	if unsupported:
		return "Unsupported", unsupported

	is_server = metadata.get("server", "").lower() in ("true", "yes", "1") or scan["server_class_factory"]
	declared = metadata.get("hasprocessingprovider", "").lower() in ("yes", "true", "1")
	# a provider class, or the metadata flag: its algorithms can run headless. Without initProcessing() the
	# provider is registered in initGui(), which the processing worker runs against the iface stand-in.
	has_processing = declared or scan["processing_provider"]
	via_init_gui = has_processing and not scan["init_processing"]
	gui = bool(scan["gui_imports"]) or scan["uses_iface"]
	if compiled:
		reasons.append("contains compiled modules (" + ", ".join(compiled[:3]) + "), which must match this system's Python and QGIS build")
	if is_server:
		reasons.append("has a QGIS Server entry point (serverClassFactory / server=True)")
		return ("Requires manual adaptation" if compiled else "Native server-compatible"), reasons
	if has_processing:
		reasons.append(
			"adds a QGIS Processing provider"
			+ (" from initGui(): it is started with the desktop iface stand-in" if via_init_gui else "")
			+ (" (its dialogs are not used here; its algorithms run in the Processing toolbox)" if gui else "")
		)
		return ("Requires manual adaptation" if compiled else "Processing-compatible"), reasons
	if gui:
		reasons.append("is a desktop plugin: it uses the QGIS Desktop interface (" + ", ".join(sorted(scan["gui_imports"]))[:200] + ("; iface" if scan["uses_iface"] else "") + ")")
		return "Requires manual adaptation", reasons
	reasons.append("uses no desktop interface, so a web adapter could drive it")
	return "Browser-adapter compatible", reasons


def installed_qgis_version():
	try:
		from qgis.core import Qgis

		return Qgis.version().split("-")[0]
	except Exception:
		return None


def version_tuple(text, upper=False):
	parts = [int(p) for p in re.findall(r"\d+", text)[:3]]
	while len(parts) < 3:
		parts.append(99 if upper else 0)
	return tuple(parts)


# ---------------------------------------------------------------------------
# extract
# ---------------------------------------------------------------------------


def extract(request):
	path = request["zip"]
	top, members = check_package(path, int(request.get("max_bytes") or 50 * 1024 * 1024))
	if top != request["plugin_id"]:
		raise PackageError(f"The package contains {top}, not {request['plugin_id']}.")
	destination = os.path.realpath(request["destination"])
	os.makedirs(destination, exist_ok=True)
	work = tempfile.mkdtemp(prefix=f".install-{top}-", dir=destination)
	try:
		with zipfile.ZipFile(path) as archive:
			for m in members:
				target = os.path.realpath(os.path.join(work, m.filename))
				if not target.startswith(os.path.realpath(work) + os.sep):
					raise PackageError(f"Unsafe path in the package: {m.filename}")
				if m.is_dir():
					os.makedirs(target, exist_ok=True)
					continue
				os.makedirs(os.path.dirname(target), exist_ok=True)
				with archive.open(m) as src, open(target, "wb") as dst:
					shutil.copyfileobj(src, dst)
				os.chmod(target, 0o640)
		for root, dirs, _files in os.walk(os.path.join(work, top)):
			for d in dirs:
				os.chmod(os.path.join(root, d), 0o750)
		os.chmod(os.path.join(work, top), 0o750)
		final = os.path.join(destination, top)
		if os.path.exists(final):
			shutil.rmtree(final)
		os.replace(os.path.join(work, top), final)
	finally:
		shutil.rmtree(work, ignore_errors=True)
	return {"path": final}


if __name__ == "__main__":
	main()
