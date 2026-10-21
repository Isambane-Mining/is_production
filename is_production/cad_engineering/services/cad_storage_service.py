"""Storage of CAD geometry files, behind a small provider interface.

Keys are provider-neutral strings; only the provider knows where they live, so a later object-store
provider can replace the local one without touching DocTypes, services or the workstation.

    models/<model>/blobs/<sha[:2]>/<sha>.<ext>   authoritative geometry (OCCT .brep, …), content-addressed
    models/<model>/revisions/<revision>.json.gz  frozen revision snapshots (write-once)
    mesh/<model>/<sha>.cadmesh                   derived display meshes (cache; safe to delete)

The local provider keeps models under CAD Settings → Model Storage Directory and meshes under Mesh Cache
Directory, both relative to the site folder, so every site on the host has its own files. Every key is
checked against a strict pattern and the resolved path must stay inside its root.
"""

import gzip
import hashlib
import os
import re
import shutil
import tempfile

import frappe
from frappe import _

MODEL_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,59}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
BLOB_EXTENSIONS = {"brep", "stl", "obj", "step", "json"}  # json: the server's record of a built file
KEY = re.compile(r"^(models|mesh)/[A-Za-z0-9][A-Za-z0-9_.\-]{0,59}/[A-Za-z0-9_.\-/]{1,200}$")


class LocalStorageProvider:
	"""Files under two roots inside the site folder."""

	def __init__(self, model_root, mesh_root):
		self.roots = {"models": model_root, "mesh": mesh_root}

	def path(self, key):
		if not KEY.match(key or "") or ".." in key.split("/"):
			frappe.throw(_("Invalid storage key {0}.").format(key))
		area, rest = key.split("/", 1)
		root = os.path.realpath(self.roots[area])
		path = os.path.realpath(os.path.join(root, rest))
		if not path.startswith(root + os.sep):
			frappe.throw(_("Invalid storage key {0}.").format(key))
		return path

	def exists(self, key):
		return os.path.isfile(self.path(key))

	def read(self, key):
		with open(self.path(key), "rb") as f:
			return f.read()

	def write(self, key, data, overwrite=True):
		path = self.path(key)
		if not overwrite and os.path.exists(path):
			frappe.throw(_("{0} already exists and cannot be replaced.").format(key))
		os.makedirs(os.path.dirname(path), mode=0o750, exist_ok=True)
		# write to a temporary file first, so a reader never sees half a file
		fd, temp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
		try:
			with os.fdopen(fd, "wb") as f:
				f.write(data)
			os.chmod(temp, 0o640)
			os.replace(temp, path)
		except BaseException:
			if os.path.exists(temp):
				os.remove(temp)
			raise

	def import_file(self, key, source):
		"""Copy a worker output into storage (also via a temporary file)."""
		with open(source, "rb") as f:
			self.write(key, f.read())

	def delete(self, key):
		path = self.path(key)
		if os.path.isfile(path):
			os.remove(path)

	def delete_prefix(self, area, model):
		root = os.path.realpath(self.roots[area])
		path = os.path.realpath(os.path.join(root, model))
		if path.startswith(root + os.sep) and os.path.isdir(path):
			shutil.rmtree(path)

	def local_path(self, key):
		"""A path the CAD worker can read (the local provider's own file)."""
		return self.path(key)


def site_directory(relative, default):
	relative = (relative or default).strip()
	if os.path.isabs(relative) or ".." in relative.split("/"):
		frappe.throw(_("Storage directories must be inside the site folder: {0}").format(relative))
	return os.path.abspath(frappe.get_site_path(relative))


def provider():
	settings = frappe.get_cached_doc("CAD Settings")
	return LocalStorageProvider(
		site_directory(settings.model_storage_directory, "private/cad/models"),
		site_directory(settings.mesh_cache_directory, "private/cad/mesh_cache"),
	)


def check_model_code(model):
	if not MODEL_CODE.match(model or ""):
		frappe.throw(_("Model Code may use letters, digits, '.', '_' and '-' only (at most 60, starting with a letter or digit)."))
	return model


def blob_key(model, digest, extension):
	check_model_code(model)
	if not SHA256.match(digest or "") or extension not in BLOB_EXTENSIONS:
		frappe.throw(_("Invalid geometry reference {0}.{1}").format(digest, extension))
	return f"models/{model}/blobs/{digest[:2]}/{digest}.{extension}"


def snapshot_key(model, revision):
	check_model_code(model)
	if not re.match(r"^[A-Za-z0-9_.\-]{1,80}$", revision or ""):
		frappe.throw(_("Invalid revision name {0}.").format(revision))
	return f"models/{model}/revisions/{revision}.json.gz"


def mesh_key(model, digest):
	check_model_code(model)
	if not SHA256.match(digest or ""):
		frappe.throw(_("Invalid geometry reference {0}.").format(digest))
	return f"mesh/{model}/{digest}.cadmesh"


def sha256_file(path):
	digest = hashlib.sha256()
	with open(path, "rb") as f:
		for chunk in iter(lambda: f.read(1 << 20), b""):
			digest.update(chunk)
	return digest.hexdigest()


def store_blob(model, path, extension, expected=None):
	"""Store a worker output under its SHA-256; the checksum is recomputed here, never trusted."""
	digest = sha256_file(path)
	if expected and expected != digest:
		frappe.throw(_("The geometry file changed between the worker and storage."))
	store = provider()
	key = blob_key(model, digest, extension)
	if not store.exists(key):
		store.import_file(key, path)
	return digest


def blob_exists(model, digest, extension="brep"):
	return provider().exists(blob_key(model, digest, extension))


def write_snapshot(model, revision, data):
	provider().write(snapshot_key(model, revision), gzip.compress(data, mtime=0), overwrite=False)
	return snapshot_key(model, revision)


def read_snapshot(key):
	return gzip.decompress(provider().read(key))


def model_storage_status():
	"""For the health check: both roots can be created and written by this bench's user."""
	try:
		store = provider()
	except Exception as e:
		return {"ok": False, "message": str(e)}
	result = {"ok": True, "roots": {}}
	for area, root in store.roots.items():
		try:
			os.makedirs(root, mode=0o750, exist_ok=True)
			probe = os.path.join(root, ".write-test")
			with open(probe, "w") as f:
				f.write("ok")
			os.remove(probe)
			usage = shutil.disk_usage(root)
			result["roots"][area] = {"path": root, "free_gb": round(usage.free / 1e9, 1)}
		except OSError as e:
			result["ok"] = False
			result["message"] = _("{0} is not writable: {1}").format(root, e)
	return result
