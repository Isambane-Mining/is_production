"""bench commands of is_production."""

import click
from frappe.commands import get_site, pass_context


@click.command(
	"gis-setup",
	help="Connect this site to the QGIS Server of its host: folders, read-only database account and GIS "
	"Settings, for whichever bench user and site this runs as. Safe to run again.",
)
@click.option("--qgis-url", help="QGIS Server endpoint (default: GIS Settings, http://127.0.0.1:8090/ows/).")
@click.option("--qgis-python", help="System Python with python3-qgis (default: GIS Settings, /usr/bin/python3).")
@click.option("--project-dir", help="Base folder for QGIS projects, shared by the host's sites (default: GIS Settings).")
@click.option("--qgis-service", default="qgis-server", show_default=True, help="systemd unit of QGIS Server (to find its group).")
@click.option("--qgis-group", help="Group QGIS Server runs as (default: from the systemd unit, else www-data).")
@click.option("--db-root-username", help="MariaDB administrator (default: root_login in config, else root).")
@click.option("--db-root-password", help="MariaDB administrator password (default: config, sudo mariadb, or a prompt).")
@click.option("--manage-server-plugins", is_flag=True, help="Make this the one site on the host that manages QGIS Server plugins.")
@click.option("--plugin-dir", help="QGIS Server plugin folder (QGIS_PLUGINPATH) with --manage-server-plugins.")
@click.option("--skip-account", is_flag=True, help="Keep the database account already in GIS Settings.")
@click.option("--skip-host-check", is_flag=True, help="Continue even if QGIS Server or PyQGIS does not answer.")
@click.option("--print-only", is_flag=True, help="Show the sudo commands and SQL without running them.")
@pass_context
def gis_setup(context, **options):
	import frappe

	from is_production.geo_planning.services.gis_setup_service import SetupError, run_setup

	site = get_site(context)
	frappe.init(site)
	frappe.connect()
	try:
		run_setup(echo=click.echo, **options)
	except SetupError as e:
		click.secho(str(e), fg="red")
		raise SystemExit(1)
	finally:
		frappe.destroy()


commands = [gis_setup]
