## Production

Isambane Mining Frappe App for Production Records

- [Mining Simulation](#mining-simulation)
- [GIS Workstation (QGIS)](#gis-workstation-qgis)
- [Acknowledgements and Licensing](#acknowledgements-and-licensing)

#### Mining Simulation

The Mining Simulation portal page (`/portal_mining_simulation`, full screen, login required) runs the mine planner's simulator on
**Mining Simulation Project** documents. Import a project file (`.isamiq.json`, or an older `.rollover.json`) from the
Mining Simulation Project list (*Import Simulation File*); *Export Simulation File* on a
project gives the same file back for the standalone simulator.

The page is built from the planner's standalone `simulator.html` without changing her code.
When she sends a new version, rebuild the page and test it:

```
bench --site <site> execute is_production.geo_planning.mining_simulation_sync.sync --kwargs "{'source': '/path/to/simulator.html'}"
```

---

## GIS Workstation (QGIS)

> **Status: in development.** This section describes the planned design and the host setup it needs.
> Parts that are not built yet are marked *(planned)*. The phases are listed under [Development phases](#development-phases).

### Introduction

The GIS Workstation is a browser alternative to QGIS Desktop, built into this app. It opens from the
**Mining Planning** workspace as a full-screen portal page (`/portal_gis`, login required), in the same way
as the Mining Simulation.

For now the GIS Workstation and the Mining Simulation are **separate projects** in the same app. When the GIS
works as expected, its datasets will be linked to the simulation viewer.

The main rule of the design:

- **Frappe owns** the app, the user interface, permissions, datasets and links to business records.
- **QGIS owns** map rendering and processing, through QGIS Server, PyQGIS, GDAL/OGR and PROJ.

The app does not rewrite algorithms that QGIS, GDAL or PROJ already provide.

The GIS Workstation is part of `is_production` and is built for Isambane's production and mine-planning work.
It is not designed as a general-purpose GIS app for other Frappe apps. Mining-specific features are built
directly into it.

### Architecture overview

```
 Browser (/portal_gis — OpenLayers)
        │  HTTPS, Frappe session
        ▼
 Frappe web worker ── GIS API (whitelisted methods; checks permissions on every call)
        │                      │                                 │
        │ edits                │ map / feature requests          │ processing, import, export
        ▼                      ▼                                 ▼
 Spatial storage adapter   QGIS gateway (authenticated     RQ job → PyQGIS worker
 (MariaDB geometry          proxy, localhost only)          (system /usr/bin/python3,
  columns + indexes)           │                             separate process)
        │                      ▼                                 │
        │               QGIS Server (FastCGI, 127.0.0.1) ◄───────┘
        │                      │ read-only DB user
        ▼                      ▼
 ┌─────────────────── Frappe MariaDB database ───────────────────┐
 │ GIS DocTypes + native GEOMETRY columns with SPATIAL indexes   │
 └───────────────────────────────────────────────────────────────┘
```

- **Edits** follow this path: browser → GIS API → spatial storage adapter → Frappe database. QGIS Server only reads.
- **QGIS Server** listens only on the local host. The browser never connects to it directly. Every map request
  goes through a Frappe endpoint that checks the user's permissions first.
- **PyQGIS cannot be imported in the bench virtual environment.** The bench runs Python 3.14, but the Debian
  `python3-qgis` bindings are built for the system Python (`/usr/bin/python3`, 3.13 on Debian 13). All PyQGIS
  work therefore runs in a separate worker process under the system Python. The Frappe web worker never
  imports `qgis`.
- **MariaDB stays the main data store.** PostgreSQL/PostGIS is not required. The storage provider can be swapped
  out, so PostGIS, GeoPackage, WFS and similar sources can be added later without changing the frontend.

### Requirements and supported versions

| Component | Version | Notes |
|---|---|---|
| OS | Debian 13 (trixie) / Ubuntu 24.04 LTS | Same host as the bench |
| Frappe / ERPNext | v16 | See `required_apps` in `hooks.py` |
| MariaDB | 10.6+ (11.x recommended) | Needs `GEOMETRY` types, `SPATIAL` indexes and `ST_*` functions |
| QGIS Server + PyQGIS | Current QGIS **LTR** from qgis.org | Record the installed version in GIS Settings; test before upgrading |
| GDAL/OGR | As shipped with the QGIS packages | The OGR **MySQL** driver must be present (see the checks below) |
| PROJ | As shipped with the QGIS packages | |
| GRASS GIS | Optional | Adds the GRASS processing provider |
| Web server | nginx (as configured by bench) | Also acts as the local FastCGI front for QGIS Server |

### 1. Install QGIS on Ubuntu / Debian

Use **one** package source for every QGIS, GDAL and PROJ package. If you mix the distribution's packages with
packages from qgis.org, the installation can break.

**Option A: qgis.org repository (recommended).** This repository has the current LTR and the newest release.
The official guide is https://qgis.org/resources/installation-guide/. Check that guide for the current repository
URLs and which distributions are supported before you copy the commands below.

```bash
sudo apt install gnupg software-properties-common wget
sudo mkdir -m755 -p /etc/apt/keyrings
sudo wget -O /etc/apt/keyrings/qgis-archive-keyring.gpg \
    https://download.qgis.org/downloads/qgis-archive-keyring.gpg

# Use the codename of your distribution (trixie, bookworm, noble, ...) — see `lsb_release -cs`.
# Use https://qgis.org/debian-ltr for the Long Term Release (recommended for servers),
# or https://qgis.org/debian for the latest release.
# On Ubuntu, use https://qgis.org/ubuntu-ltr or https://qgis.org/ubuntu instead.
sudo tee /etc/apt/sources.list.d/qgis.sources >/dev/null <<EOF
Types: deb deb-src
URIs: https://qgis.org/debian-ltr
Suites: $(lsb_release -cs)
Architectures: amd64
Components: main
Signed-By: /etc/apt/keyrings/qgis-archive-keyring.gpg
EOF
```

**Option B: distribution packages.** Debian and Ubuntu ship QGIS in their own archives. These versions are older,
but they get updates with the rest of the system. Skip the repository step above.

**Install the packages:**

```bash
sudo apt update
sudo apt install qgis-server python3-qgis \
    gdal-bin python3-gdal proj-bin spawn-fcgi
```

**Optional processing providers:**

```bash
sudo apt install grass qgis-plugin-grass   # GRASS GIS provider for QGIS Processing
```

**Never** `pip install` QGIS, PyQGIS or the system GDAL bindings into the bench `env/`. PyQGIS runs only under
the system Python.

**Check the installation:**

```bash
dpkg -s qgis-server | grep '^Version'
/usr/bin/python3 -c "from qgis.core import Qgis; print(Qgis.version())"
ogrinfo --version
ogrinfo --formats | grep -i mysql          # must list the MySQL driver used to read MariaDB
projinfo EPSG:2053 -o PROJ                 # Hartebeesthoek94 / Lo29 — confirms PROJ data is installed

# Call QGIS Server once in CGI mode (no project file needed for this check):
QT_QPA_PLATFORM=offscreen QUERY_STRING="SERVICE=WMS&REQUEST=GetCapabilities" \
    /usr/lib/cgi-bin/qgis_mapserv.fcgi | head -20
```

### 2. Configure QGIS Server

Run QGIS Server as its own service on a local socket. Put an nginx server block in front of it that listens
**only on 127.0.0.1**. Frappe talks to that local address. The public site never forwards to it.

**`/etc/systemd/system/qgis-server.service`**

```ini
[Unit]
Description=QGIS Server (FastCGI) for Frappe GIS
After=network.target

[Service]
User=www-data
Group=www-data
RuntimeDirectory=qgis-server
Environment=QT_QPA_PLATFORM=offscreen
Environment=QGIS_SERVER_LOG_STDERR=1
Environment=QGIS_SERVER_LOG_LEVEL=1
Environment=QGIS_SERVER_PARALLEL_RENDERING=1
Environment=QGIS_SERVER_MAX_THREADS=4
Environment=QGIS_SERVER_IGNORE_BAD_LAYERS=1
Environment=QGIS_PLUGINPATH=/var/lib/qgis-server/plugins
ExecStart=/usr/bin/spawn-fcgi -n -s /run/qgis-server/qgis.sock -M 0660 -U www-data -G www-data \
    -F 2 -- /usr/lib/cgi-bin/qgis_mapserv.fcgi
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

**`/etc/nginx/conf.d/qgis-server-local.conf`**

```nginx
server {
    listen 127.0.0.1:8090;          # local only — never expose this port
    server_name localhost;

    location /ows/ {
        fastcgi_pass unix:/run/qgis-server/qgis.sock;
        include fastcgi_params;
        fastcgi_param QGIS_PROJECT_FILE $arg_map;   # the project is chosen by the Frappe gateway
    }
}
```

```bash
sudo mkdir -p /var/lib/qgis-server/plugins
sudo systemctl daemon-reload
sudo systemctl enable --now qgis-server
sudo nginx -t && sudo systemctl reload nginx
curl -s "http://127.0.0.1:8090/ows/?SERVICE=WMS&REQUEST=GetCapabilities" | head -5
```

- Keep the firewall closed on port 8090. Do not add a `location` for QGIS Server to the bench-generated site config.
- If nginx runs as a different user, change the socket owner to match. The bench nginx config for this host
  shows which user it runs as.
- `.qgs`/`.qgz` project files written by the app go to a directory that both the bench user and `www-data` can read
  (**GIS Settings → QGIS Project Directory**, used from the layer-publishing phase on).

### 3. Database user for QGIS Server

QGIS Server reads features through the GDAL/OGR MySQL driver, using its **own read-only** MariaDB account.
QGIS never uses the site's own database user. It needs `SELECT` on **one table only**:

```sql
-- run as a MariaDB admin; replace <site_db> with the site's db_name from site_config.json
CREATE USER 'qgis_ro'@'localhost' IDENTIFIED BY '<strong password>';
GRANT SELECT ON `<site_db>`.`tabGIS Feature` TO 'qgis_ro'@'localhost';
GRANT SELECT ON `<site_db>`.`tabGIS Dataset` TO 'qgis_ro'@'localhost';
```

How QGIS reads a dataset: each dataset becomes one layer on `tabGIS Feature`. The layer is filtered with
`dataset = '<dataset_name>'`, and its geometry type and CRS are set on the layer. The feature ID is the table's
integer primary key (`name`), so it stays the same between requests. QGIS sees the attributes as one JSON column
(`attributes`). The QGIS project generator *(planned)* adds a typed expression field for each attribute.

This was chosen over per-dataset SQL views because the OGR MySQL driver finds no primary key on a view. It then
numbers features 0, 1, 2… on every read, which breaks identify, selection and editing.

To test the account (it should list the GIS features, and nothing else should be readable):

```bash
ogrinfo -ro -so "MySQL:<site_db>,host=127.0.0.1,user=qgis_ro,password=<password>,tables=tabGIS Feature" "tabGIS Feature"
```

Enter the account in **GIS Settings → QGIS Database Account**. The password is stored as a Password field and is
sent to the PyQGIS worker on stdin, never on a command line.

### 4. Install the app

```bash
bench get-app <is_production_repository_url>
bench --site <site_name> install-app is_production
bench --site <site_name> migrate
bench build --app is_production      # builds the GIS Workstation bundle (OpenLayers, proj4js, Vue)
bench restart
```

Then open **GIS Settings** (Mining Planning workspace → GIS card) and enter the QGIS Server endpoint
(`http://127.0.0.1:8090/ows/`), the system Python path (`/usr/bin/python3`) and the read-only database account.
Save, then click **Check Dependencies**.

### 5. Datasets, features and the geometry schema

- **GIS Dataset** (named by `dataset_name`, which is also the QGIS layer name) sets the geometry type, the CRS
  (an EPSG code; nothing is reprojected on save) and the attribute schema (**GIS Attribute Definition** rows:
  Text, Integer, Float, Boolean, Date, Datetime, with required, default and allowed values). Once a dataset has
  features, its type, CRS, Z setting and existing attributes are locked. New attributes can still be added.
- **GIS Feature** (integer ID) keeps its geometry twice. The `geometry` field holds GeoJSON in the dataset's CRS,
  with full precision and any Z values. The native `geom` column (`GEOMETRY`, `SPATIAL` index, dataset SRID) is a
  2D copy written in the same transaction, and it is what spatial queries and QGIS use. Attributes are checked
  against the schema. Invalid geometries are saved and marked *Invalid*, or refused if the dataset sets
  *Reject Invalid Geometry*. Single parts are stored as one-part multis in Multi* datasets. The revision number
  goes up on every change to the geometry or attributes.
- **Elevation:** in datasets with *Has Z*, every coordinate keeps its Z in the GeoJSON. Each feature also stores
  its **Min Z / Max Z**, and the dataset its Z extent, as normal columns. QGIS can therefore filter, label and
  style by elevation even though the `geom` column is 2D.
- **Permissions:** a User Permission on GIS Dataset also restricts that user's access to the dataset's features.

Frappe's schema sync does not know about the `geom` column. The `after_install` and `after_migrate` hooks
(`gis_storage_service.ensure_geometry_schema`) add the column and its index if either is missing, and they are
safe to run any number of times. To check by hand:

```sql
SHOW INDEX FROM `tabGIS Feature` WHERE Index_type = 'SPATIAL';
```

### 6. Health check

**GIS Settings → Check Dependencies** runs the check. It also runs once a day from the scheduler. The result is
stored on GIS Settings as *OK*, *Warning* or *Error*, with a list of problems and the full report. It checks:

- QGIS Server: whether it answers at the configured URL, its version (from its `Server` response header), and
  whether the URL is local
- PyQGIS, through the system Python in a separate process (`geo_planning/qgis_worker/health.py`): the QGIS,
  GDAL and PROJ versions, the OGR MySQL driver, and the processing providers (native, GDAL, GRASS, ...)
- the QGIS database account: it connects, can read `tabGIS Feature` with a stable FID column, QGIS can open the
  table as a layer, and the account **cannot** read other Frappe tables
- the geometry schema (the `geom` column and its spatial index), and the project directory if one is set
- plugin status *(planned, with the plugin phase)*

The workstation's status bar shows the last result.

### GIS Workstation

Open it from the **Mining Planning** workspace (*GIS Workstation* shortcut), or go to `/portal_gis`. It needs read
permission on GIS Dataset. What it has now:

- **Menu bar and toolbar**, with menu items for later phases shown but disabled.
- **Browser** (datasets by category) and **Layers** panel: add/remove layers, visibility, order, colour,
  opacity and zoom to layer. The layer list and panel layout are remembered per browser until GIS Projects
  arrive.
- **Map** (OpenLayers, with an optional OpenStreetMap basemap): Identify, zoom box, full extent, zoom to layer or
  selection, previous/next extent, and *Go to Coordinate*.
- **Loading per view:** layers load only the features in the current view. The server uses the spatial index,
  transforms to Web Mercator, and simplifies lines and polygons to the pixel size. A layer shows at most
  *Max Features per Request* features per view (GIS Settings); it says so when there are more.
- **Feature panel and attribute table:** the table lists the active layer's features in view, with sort and
  filter. The selection is kept in step with the map, and features open in Desk for editing.
- **Status bar:** pointer coordinates in the chosen CRS (the default is GIS Settings → *Default Display CRS*;
  Lo zones read as westing/southing, the same as QGIS), map scale, and QGIS health.

The page follows the user's Desk light/dark theme. Map rendering through QGIS Server and editing on the map come
in later phases.

### 7. Plugins *(planned)*

**GIS Plugin Settings** (Single) and **GIS Plugin** records manage the plugins. Only a **System Manager** can
install or enable a plugin.

- Plugin packages are private File attachments on the GIS Plugin record, saved with a checksum. Plugin code is
  never stored in text fields, and it is never run from a text field.
- **QGIS Server plugins** are deployed to the directory set in `QGIS_PLUGINPATH` (`/var/lib/qgis-server/plugins`).
  Restart `qgis-server` after a change.
- **Processing plugins** are loaded only by the PyQGIS worker. The Frappe web worker never imports plugin code.
- **QGIS Desktop plugins** are recorded and given a compatibility class: server-compatible, processing-compatible,
  browser-adapter compatible, needs manual adaptation, or unsupported. Running Qt user interfaces in the browser
  is a research goal. It is not a feature of this app.

### 8. Import and export *(planned)*

Imports and exports run as background jobs. Each job is logged in a **GIS Import Export Job** with the user, time,
datasets, format and status.

- **Import:** GeoPackage, Shapefile (`.shp/.shx/.dbf/.prj`), GeoJSON, KML/KMZ, GML, GPX, CSV with coordinates,
  DXF where supported, and GeoTIFF and other GDAL rasters. QGIS projects (`.qgs/.qgz`) are imported where they are
  compatible. The source CRS is always kept. Reprojection happens only if the user asks for it.
- **Export:** GeoPackage (the main format for QGIS Desktop), GeoJSON, Shapefile, KML, GML and CSV for vectors,
  and GeoTIFF/COG for rasters. The user chooses the target CRS and whether to export all features or only the
  selected ones.
- **Project package:** a GeoPackage plus a `.qgz` project with layer order, styles, labels and extent. The package
  can be imported back into the app.
- An export applies the same permissions on datasets and attributes as the workstation.

### 9. Security and permissions

- The GIS uses the app's existing roles. No GIS-specific roles are added. **Production Manager** has full access to
  datasets and features. **Production User** can read datasets and can create, edit and delete features.
  **System Manager** keeps full access and manages plugins.
- Every GIS API call and every request through the QGIS gateway checks Frappe permissions on the project, the
  dataset and, where it applies, the feature.
- Users cannot reach QGIS Server directly. Only the Frappe gateway calls it.
- QGIS Server uses a read-only database account, and all writes go through the Frappe GIS API.
- Processing outputs and exported files get the permissions of the dataset they came from.
- No processing job overwrites an existing dataset unless the user gives explicit permission. Every change to a
  dataset creates a new revision.

### 10. Known limitations

- The app is not on par with every QGIS Desktop feature or plugin, and Qt plugin dialogs do not run in the browser.
- MariaDB's spatial functions are fewer than PostGIS's. Advanced analysis runs through QGIS Processing, not SQL.
- MariaDB geometries are 2D. Z values are kept in the feature's GeoJSON, plus Min/Max Z columns; QGIS layers read
  from MariaDB have 2D geometry.
- The Python packages `pyproj`, `rasterio` and `geopandas` in the bench `env/` bring their **own** copies of PROJ
  and GDAL, which can be different versions from the system ones that QGIS uses. CRS transforms done in the bench
  and in QGIS can therefore differ slightly. GIS transforms should always run through QGIS/PROJ in the worker.
- The GIS datasets are not linked to the Mining Simulation yet. That link is a later step.

### 11. Upgrades and backups

- **Before upgrading QGIS**, note the current version shown in GIS Settings. Run the upgrade on the lab site
  first, then run the dependency check and the GIS tests. Hold the packages on production
  (`sudo apt-mark hold qgis-server python3-qgis`) until the new version has been tested.
- After `bench migrate`, check that the spatial columns and indexes are still there (see [Datasets, features and the geometry schema](#5-datasets-features-and-the-geometry-schema)).
- **Backups:** the vector data is in MariaDB and is included in `bench backup`. Back these up together with the
  database, using `bench backup --with-files` plus the paths below:
  - raster files and uploaded datasets (Frappe private files)
  - plugin packages (private files) and `/var/lib/qgis-server/plugins`
  - the directory that holds the generated `.qgs/.qgz` project files
  - `/etc/systemd/system/qgis-server.service` and `/etc/nginx/conf.d/qgis-server-local.conf`

### Development phases

| Phase | Deliverables |
|---|---|
| 1 — Foundation ✅ | GIS Settings, QGIS health checks, `/portal_gis` workstation shell, Mining Planning shortcut |
| 2 — Spatial storage ✅ | GIS Dataset, GIS Feature, MariaDB geometry adapter, Z range |
| 3 — Server integration | Publishing layers, QGIS rendering through the gateway, reading features |
| 4 — Editing | Vector tools, attribute editing, saving and validation |
| 5 — Exchange with QGIS Desktop | GeoPackage, GeoJSON, Shapefile, transfer of QGIS projects |
| 6 — Processing | Algorithm registry, PyQGIS jobs, saving outputs |
| 7 — Cartography and raster | Styling, labels, layouts, raster tools |
| 8 — Plugins | Plugin Settings, Plugin registry, server and processing plugins |
| 9 — Desktop plugin compatibility | Research and adapters for the desktop plugin interface |
| 10 — Hardening | Performance, security, tests, documentation |

---

## Acknowledgements and Licensing

#### QGIS

The GIS Workstation uses **QGIS Server** and **PyQGIS** for map rendering and processing.

**QGIS — Open Source Geographic Information System**
Developed by the QGIS contributors and community.

- Website: https://qgis.org/
- Source: https://github.com/qgis/QGIS
- License: GNU General Public License, version 2 or later (GPL-2.0-or-later), as it applies to each QGIS
  component. https://github.com/qgis/QGIS/blob/master/COPYING

This app is an independent product of Isambane Mining (Pty) Ltd. It is **not** an official QGIS product. It is
not endorsed by, and has no official link to, the QGIS project or QGIS.ORG.

#### Third-party components

| Component | Used for | License |
|---|---|---|
| [Frappe Framework](https://github.com/frappe/frappe) | App framework | MIT |
| [ERPNext](https://github.com/frappe/erpnext) | Required app | GPL-3.0 |
| [QGIS Server / PyQGIS](https://qgis.org/) | Rendering, processing (system package, not bundled) | GPL-2.0-or-later |
| [GDAL/OGR](https://gdal.org/) | Format I/O, MySQL/MariaDB reading (system package) | MIT/X style |
| [PROJ](https://proj.org/) | Coordinate transforms (system package) | MIT/X style |
| [GRASS GIS](https://grass.osgeo.org/) | Optional processing provider | GPL-2.0-or-later |
| [OpenLayers](https://openlayers.org/) | GIS Workstation map | BSD-2-Clause |
| [proj4js](https://github.com/proj4js/proj4js) | Coordinate readout in other CRSs | MIT |
| [Vue](https://vuejs.org/) | GIS Workstation interface (from Frappe's build) | MIT |
| [OpenStreetMap](https://www.openstreetmap.org/copyright) | Optional basemap tiles | ODbL (data), attribution shown on the map |
| [three.js](https://threejs.org/) | Mining Simulation 3D view (vendored) | MIT |
| Python packages in `pyproject.toml` (shapely, geopandas, pyproj, rasterio, ...) | Geo Planning | See each package |

The copyright notices and license files of vendored libraries are kept next to the libraries under `public/`.

#### License review

This app's own code is licensed under the MIT license (`license.txt`). QGIS, PyQGIS and GRASS are **GPL**. The app
installs them as separate system packages and talks to QGIS Server over FastCGI/HTTP. However, the PyQGIS
worker scripts import `qgis` directly.

**Have the licensing reviewed before you distribute this app outside Isambane, or ship it together with QGIS
or as a binary.** Listing QGIS in these acknowledgements does not, on its own, make the app compliant with the
GPL. One likely result of the review is that the PyQGIS worker code and any QGIS plugins we write must be
released under GPL-2.0-or-later.

#### License

mit
