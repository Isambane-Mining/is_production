"""Connect one site to the QGIS Server of its host: ``bench --site <site> gis-setup``.

Nothing about the host is hard-coded. The command works out, for the bench and site it runs in:

- the bench's Unix user (whoever runs the command) and the site name;
- QGIS Server's group, from its systemd unit (or ``--qgis-group``);
- the site's database (from its site_config).

and then makes this site ready:

1. checks the host: PyQGIS imports under the given Python and QGIS Server answers at the given URL;
2. creates the shared project base folder (root, QGIS Server's group, 0751) if needed and this site's own folder
   ``<base>/<site>`` (bench user, QGIS Server's group, 2750), with ``sudo``; with
   ``--manage-server-plugins`` also QGIS Server's plugin folder for this bench;
3. creates (or re-keys) this site's read-only database account ``qgis_ro_<db name>`` with a random password and
   ``SELECT`` on its own ``tabGIS Feature`` only, as the MariaDB administrator (``--db-root-username`` /
   ``--db-root-password``, the bench's root_login / root_password config, ``sudo mariadb``, or a prompt);
4. fills in GIS Settings (and GIS Plugin Settings), which republishes the site's datasets, and runs the health check.

Running it again is safe: folders get their owner and mode back and the account gets a new password.
``--print-only`` shows the privileged commands and SQL instead of running them.
"""

import getpass
import os
import re
import secrets
import shlex
import subprocess

import frappe
from frappe import _

DEFAULT_GROUP = "www-data"


class SetupError(Exception):
	pass


# ---------------------------------------------------------------------------
# what the host looks like
# ---------------------------------------------------------------------------


def detect_qgis_group(service="qgis-server"):
	"""The group QGIS Server runs as, from its systemd unit; www-data (Debian/Ubuntu's default) if unknown."""
	try:
		group = subprocess.run(
			["systemctl", "show", service, "-p", "Group", "--value"], capture_output=True, text=True, timeout=10
		).stdout.strip()
	except (OSError, subprocess.SubprocessError):
		group = ""
	return group or DEFAULT_GROUP


def bench_user():
	return getpass.getuser()


def account_name(db_name):
	if not re.match(r"^[A-Za-z0-9_]+$", db_name or ""):
		raise SetupError(f"Unexpected database name {db_name!r}.")
	return f"qgis_ro_{db_name}"[:80]


def account_host():
	"""Where QGIS Server connects from: this host for a local database, anywhere otherwise."""
	return "localhost" if (frappe.conf.db_host or "localhost") in ("localhost", "127.0.0.1", "::1") else "%"


# ---------------------------------------------------------------------------
# folders (privileged)
# ---------------------------------------------------------------------------


def folder_commands(base, site, user, group, plugin_dir=None):
	commands = [
		["install", "-d", "-o", "root", "-g", group, "-m", "0751", base],
		["install", "-d", "-o", user, "-g", group, "-m", "2750", os.path.join(base, site)],
	]
	if plugin_dir:
		commands.append(["install", "-d", "-o", user, "-g", group, "-m", "2750", plugin_dir])
	return commands


def plugin_dir_owned_elsewhere(plugin_dir, user):
	"""Name of the user owning an existing QGIS Server plugin folder, if it is not this bench's user."""
	import pwd

	try:
		owner = pwd.getpwuid(os.stat(plugin_dir).st_uid).pw_name
	except (FileNotFoundError, KeyError):
		return None
	return owner if owner not in (user, "root") else None


def run_privileged(command, print_only, echo):
	echo("  $ sudo " + " ".join(shlex.quote(c) for c in command))
	if print_only:
		return
	result = subprocess.run(["sudo", *command])
	if result.returncode:
		raise SetupError(f"sudo {command[0]} failed (exit code {result.returncode}).")


# ---------------------------------------------------------------------------
# database account (privileged)
# ---------------------------------------------------------------------------


def account_sql(user, host, password, db_name):
	if not re.match(r"^[A-Za-z0-9_\-]+$", password):
		raise SetupError("Unexpected characters in the generated password.")
	who = f"'{user}'@'{host}'"
	return [
		f"CREATE USER IF NOT EXISTS {who} IDENTIFIED BY '{password}'",
		f"ALTER USER {who} IDENTIFIED BY '{password}'",
		f"GRANT SELECT ON `{db_name}`.`tabGIS Feature` TO {who}",
	]


def create_account(statements, root_login, root_password, print_only, echo):
	shown = [re.sub(r"IDENTIFIED BY '[^']*'", "IDENTIFIED BY '********'", s) for s in statements]
	for line in shown:
		echo(f"  SQL> {line};")
	if print_only:
		return
	root_login = root_login or frappe.conf.get("mariadb_root_login") or frappe.conf.get("root_login")
	root_password = root_password or frappe.conf.get("mariadb_root_password") or frappe.conf.get("root_password")
	if not root_password:
		# Debian's default: the MariaDB root account authenticates by the Unix socket for the root user
		result = subprocess.run(["sudo", "-n", "mariadb"], input=";\n".join(statements) + ";\n", capture_output=True, text=True)
		if result.returncode == 0:
			echo("  (as the MariaDB administrator through sudo mariadb)")
			return
	from frappe.database.mariadb.setup_db import get_root_connection

	frappe.flags.root_login = root_login or "root"
	frappe.flags.root_password = root_password  # None: Frappe asks for it, as bench new-site does
	connection = get_root_connection()
	for statement in statements:
		connection.sql(statement)
	connection.sql("FLUSH PRIVILEGES")


# ---------------------------------------------------------------------------
# the whole setup
# ---------------------------------------------------------------------------


def run_setup(
	qgis_url=None,
	qgis_python=None,
	project_dir=None,
	qgis_service="qgis-server",
	qgis_group=None,
	db_root_username=None,
	db_root_password=None,
	manage_server_plugins=False,
	plugin_dir=None,
	skip_account=False,
	skip_host_check=False,
	print_only=False,
	echo=print,
):
	from is_production.geo_planning.services import gis_health_service as health
	from is_production.geo_planning.services.gis_plugin_service import ensure_single_defaults

	ensure_single_defaults()
	settings = frappe.get_single("GIS Settings")
	site = frappe.local.site
	user = bench_user()
	qgis_url = qgis_url or settings.qgis_server_url
	qgis_python = qgis_python or settings.qgis_python
	base = (project_dir or settings.qgis_project_directory).rstrip("/")
	group = qgis_group or detect_qgis_group(qgis_service)
	echo(f"Site {site}, bench user {user}, QGIS Server group {group}.")

	echo("1. Host: QGIS Server and PyQGIS")
	pyqgis = health.run_worker(qgis_python, "health.py", {}).get("pyqgis") or {}
	server = health.check_qgis_server(qgis_url)
	echo(f"  PyQGIS ({qgis_python}): {pyqgis.get('version') or pyqgis.get('message') or 'not available'}")
	echo(f"  QGIS Server ({qgis_url}): {server.get('version') if server['ok'] else server.get('message')}")
	if not (pyqgis.get("ok") and server["ok"]) and not skip_host_check:
		raise SetupError(
			"QGIS is not ready on this host. Install QGIS Server and PyQGIS and start the qgis-server service first "
			"(README: Install QGIS, Configure QGIS Server), or pass --qgis-url / --qgis-python."
		)

	echo("2. Folders")
	plugins_target = None
	if manage_server_plugins:
		plugins_target = (plugin_dir or frappe.db.get_single_value("GIS Plugin Settings", "server_plugin_directory")).rstrip("/")
		owner = plugin_dir_owned_elsewhere(plugins_target, user)
		if owner:
			raise SetupError(
				f"{plugins_target} belongs to {owner}: another bench manages QGIS Server plugins on this host. "
				"Only one site may; leave out --manage-server-plugins."
			)
	for command in folder_commands(base, site, user, group, plugins_target):
		run_privileged(command, print_only, echo)

	password = None
	db_user = settings.qgis_db_user
	if not skip_account:
		echo("3. Read-only database account")
		db_name = frappe.conf.db_name
		db_user = account_name(db_name)
		password = secrets.token_urlsafe(24)
		create_account(account_sql(db_user, account_host(), password, db_name), db_root_username, db_root_password, print_only, echo)

	if print_only:
		echo("Print only: nothing was changed.")
		return {"status": None}

	echo("4. GIS Settings")
	settings.reload()
	settings.qgis_server_url = qgis_url
	settings.qgis_python = qgis_python
	settings.qgis_project_directory = base
	settings.qgis_server_group = group
	if password:
		settings.qgis_db_host = frappe.conf.db_host or "localhost"
		settings.qgis_db_port = frappe.conf.db_port or None
		settings.qgis_db_name = None  # this site's own database
		settings.qgis_db_user = db_user
		settings.qgis_db_password = password
	settings.save(ignore_permissions=True)  # republishes the datasets when the account changed
	if manage_server_plugins:
		frappe.db.set_single_value("GIS Plugin Settings", {"manage_server_plugins": 1, "server_plugin_directory": plugins_target})
	frappe.db.commit()

	echo("5. Health check")
	result = health.run_health_check()
	frappe.db.commit()
	for line in result["problems"] + result["warnings"]:
		echo(f"  - {line}")
	echo(f"Health: {result['status']}")
	return result
