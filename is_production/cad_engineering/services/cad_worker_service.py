"""Running the CAD worker (``cad_worker/geometry_worker.py``) from the bench.

The worker is a separate process under the isolated CAD worker Python (CAD Settings → CAD Worker Python,
by default ``/opt/cad-worker/venv/bin/python``) started with ``-I`` and a minimal environment. It gets a
bounded JSON request on stdin — never database credentials — and works only inside a private job
workspace under the site folder. The worker lowers its own CPU priority and applies the memory and CPU
limits from CAD Settings before it touches any geometry; the bench enforces the wall-clock timeout.
"""

import json
import os
import queue
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

import frappe
from frappe import _
from frappe.utils import cint

WORKER_DIR = Path(__file__).resolve().parent.parent / "cad_worker"
WORKER = "geometry_worker.py"  # Open CASCADE
DXF_WORKER = "dxf_worker.py"  # ezdxf + pyproj
SCRIPTS = {WORKER, DXF_WORKER}
HEALTH_TIMEOUT = 120


def settings():
	return frappe.get_cached_doc("CAD Settings")


def environment():
	return {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": os.environ.get("HOME", "/tmp"), "LANG": "C.UTF-8", "OMP_NUM_THREADS": "2"}


def limits(config=None):
	config = config or settings()
	minutes = cint(config.max_processing_minutes) or 30
	return {
		"nice": min(max(cint(config.worker_nice), 0), 19),
		"memory_limit_mb": max(cint(config.worker_memory_limit_mb), 0),
		"cpu_seconds": minutes * 60,
	}, minutes * 60


def job_workspace(name):
	"""A fresh private folder for one worker run (removed afterwards by the caller)."""
	path = os.path.abspath(frappe.get_site_path("private", "cad", "jobs", name))
	shutil.rmtree(path, ignore_errors=True)
	os.makedirs(os.path.join(path, "outputs"), mode=0o750)
	return path


def check_python(python):
	if not python or not os.path.isfile(python):
		return _("CAD worker Python {0} does not exist. Install it as described in the README (CAD Engineering Workstation).").format(python)
	return None


def script_path(script):
	if script not in SCRIPTS:
		frappe.throw(_("Unknown CAD worker {0}.").format(script))
	return str(WORKER_DIR / script)


def run(request, timeout=HEALTH_TIMEOUT, python=None, script=WORKER):
	"""Run one command and wait; returns the worker's result or {"error": ...}."""
	config = settings()
	python = python or config.worker_python
	problem = check_python(python)
	if problem:
		return {"error": problem}
	worker_limits, _max = limits(config)
	try:
		completed = subprocess.run(
			[python, "-I", script_path(script)],
			input=json.dumps({**worker_limits, **request}),
			capture_output=True,
			text=True,
			timeout=timeout,
			env=environment(),
			cwd=request.get("workspace") or "/tmp",
		)
	except subprocess.TimeoutExpired:
		return {"error": _("The CAD worker did not finish within {0} seconds.").format(timeout)}
	result, error = None, None
	for line in completed.stdout.splitlines():
		try:
			message = json.loads(line)
		except ValueError:
			continue
		result = message.get("result", result)
		error = message.get("error", error)
	if error:
		return {"error": error}
	if result is None:
		return {"error": (completed.stderr or completed.stdout or "").strip()[-2000:] or _("The CAD worker gave no result.")}
	return result


def run_streaming(request, on_progress=None, should_cancel=None, log=None, script=WORKER):
	"""Run one command for a job: progress and log lines stream back, Cancel sends SIGTERM, the
	wall-clock limit kills the worker. Returns (result, error)."""
	config = settings()
	problem = check_python(config.worker_python)
	if problem:
		return None, problem
	worker_limits, timeout = limits(config)
	log = log if log is not None else []
	process = subprocess.Popen(
		[config.worker_python, "-I", script_path(script)],
		stdin=subprocess.PIPE,
		stdout=subprocess.PIPE,
		stderr=subprocess.PIPE,
		text=True,
		env=environment(),
		cwd=request.get("workspace") or "/tmp",
	)
	process.stdin.write(json.dumps({**worker_limits, **request}))
	process.stdin.close()

	lines = queue.Queue()
	threading.Thread(target=lambda: [lines.put(line) for line in process.stdout] + [lines.put(None)], daemon=True).start()
	stderr = []
	threading.Thread(target=lambda: [stderr.append(line) for line in process.stderr], daemon=True).start()

	started = time.monotonic()
	last_update = last_check = 0
	result = error = cancelled_at = None
	finished = False
	while not finished:
		try:
			line = lines.get(timeout=1)
		except queue.Empty:
			line = ""
		if line is None:
			finished = True
		elif line:
			try:
				message = json.loads(line)
			except ValueError:
				log.append(line.rstrip())
				message = {}
			if "log" in message:
				log.append(str(message["log"]))
			if "result" in message:
				result = message["result"]
			if "error" in message:
				error = message["error"]
			if "progress" in message and on_progress and time.monotonic() - last_update > 2:
				last_update = time.monotonic()
				on_progress(message["progress"])
		now = time.monotonic()
		if should_cancel and cancelled_at is None and now - last_check > 2:
			last_check = now
			if should_cancel():
				cancelled_at = now
				process.send_signal(signal.SIGTERM)
				log.append(_("Cancel requested."))
		if cancelled_at and now - cancelled_at > 15 and process.poll() is None:
			process.kill()
		if now - started > timeout and process.poll() is None:
			process.kill()
			error = _("Stopped after {0} minutes (CAD Settings → Maximum Processing Time).").format(timeout // 60)
	process.wait()
	tail = "".join(stderr[-40:]).strip()
	if (error or process.returncode) and tail:
		log.append(tail[-4000:])
	if cancelled_at:
		return None, "Cancelled."
	if result is None and not error:
		error = _("The CAD worker stopped unexpectedly (exit code {0}).").format(process.returncode)
	return result, error
