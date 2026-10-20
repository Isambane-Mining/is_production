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
- Create the project directory. The app writes one `.qgs` per published dataset into it (**GIS Settings → QGIS
  Project Directory**). The files contain the read-only database account's password, so make the directory
  writable by the bench user, readable by QGIS Server's group only, and closed to everyone else:

  ```bash
  sudo install -d -o <bench user> -g www-data -m 2750 /var/lib/qgis-server/projects
  ```

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
(`attributes`). The published QGIS project adds a typed expression field for each attribute (see [Publishing and the QGIS gateway](#7-publishing-and-the-qgis-gateway)).

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
  goes up on every change to the geometry, attributes or label.
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
permission on GIS Dataset.

**Laid out like QGIS Desktop.** It has QGIS's menus, toolbar groups, docks, layer tree, status bar and keyboard
shortcuts. The icons are drawn with [Lucide](https://lucide.dev) (ISC licence), not QGIS's own GPL icon set, and the
About box says the workstation is not an official QGIS product.

- **Menu bar:**
  - **Project, Edit, View, Layer, Settings, Plugins, Processing, Help**, as in QGIS. View → *Panels* and
    *Toolbars* show and hide each panel and toolbar, and View → *Basemap* switches the basemap.
  - **Vector** and **Raster** menus with QGIS's entries (Geoprocessing Tools → Buffer, Clip, Dissolve…; Analysis →
    Slope, Hillshade, Contour…). Each entry opens its Processing algorithm. Entries are greyed out when the
    algorithm is not installed or cannot run from the web.
- **Toolbars**, each of which can be hidden:
  - **Project:** New, Open, Save, Export to QGIS.
  - **Data Source:** Import, Export.
  - **Map Navigation:** Pan, Zoom In/Out (click or drag a box), Zoom Full, Zoom to Selection, Zoom to Layer, Zoom
    Last/Next, Refresh.
  - **Attributes:** Identify, Select by area or click, Deselect all, Attribute Table.
  - **Digitizing:** Toggle Editing, Save Layer Edits, Add Feature, Vertex Tool, Move, Delete, Undo, Redo.
  - **Advanced Digitizing:** Rotate, Split, Merge, Duplicate, Add Part, Snapping, Tracing, Z for new vertices,
    Validate, Discard.
  - **Processing / Plugins:** Toolbox, History, Plugin Manager.
- **Docks:**
  - **Browser** over **Layers** on the left. Datasets are grouped by category, map-service overlays are listed,
    and you can drag a dataset onto the Layers panel or double-click it to add it.
  - **Processing Toolbox**, **Identify Results** (or **Edit Feature** while editing) and **Processing History** as
    tabs on the right.
  - **Attribute Table** under the map.

  Panels can be closed and resized.
- **Layer tree:**
  - visibility check boxes, and legends that expand (QGIS GetLegendGraphic);
  - a pencil on the layer being edited;
  - drag-and-drop ordering, with the basemap and its opacity at the bottom;
  - a right-click menu: Zoom to Layer/Selection, Open Attribute Table, Toggle Editing, Export (Save Features /
    Save Selected Features As…), Drawn By (QGIS Server / Browser), Opacity, Change Colour, Move to Top, Remove
    Layer, Properties.
- **Selection** works outside editing too. Select by click, Shift+click or box on the active layer, including
  layers drawn by QGIS. *Zoom to Selection*, *Save Selected Features As…* and the Processing *Selected features
  only* option all use it.
- **Status bar:** messages, the pointer *Coordinate* (type x, y and press Enter to go there), *Scale* (choose or
  type 1:N), *Rotation*, the *CRS* button, and the QGIS health check.
- **Shortcuts** as in QGIS, listed under Settings → *Keyboard Shortcuts*:
  - Ctrl+N/O/S, Ctrl+Shift+S
  - Ctrl+Shift+I (Identify), Ctrl+Shift+F (Zoom Full), Ctrl+J (Zoom to Selection)
  - F5 (Refresh), F6 (Attribute Table), Ctrl+Alt+T (Toolbox), Ctrl+Shift+A (Deselect)
  - Ctrl+. (Add Feature), Del, Ctrl+Z / Ctrl+Shift+Z, Ctrl+D (Remove Layer)
- **Loading per view:** layers load only the features in the current view, simplified to the pixel size on the
  server, with at most *Max Features per Request* per view.
- **Drawn by QGIS:** published datasets are drawn by QGIS Server through the gateway, and Identify uses
  GetFeatureInfo. Each layer can be switched to *Browser*.
- **Projects:** *Project → New / Open / Save / Save As* store the map as a **GIS Project** (layers, overlays,
  basemap, display CRS, extent). `/portal_gis?project=GISP-00001` opens one directly.

The page follows the user's Desk light/dark theme.

**Editing.** Users with write permission on GIS Feature (Production Manager, Production User) get **Edit Layer**
for the active layer. While a layer is being edited, it is drawn in the browser at full detail, so unsaved
edits show and no vertices are lost to simplification. The editing toolbar has:

- **Select:** click, Shift/Ctrl+click to add or remove, Ctrl+drag for a box. The attribute table's rows select
  too.
- **Add** (point, line or polygon by dataset type; *Geometry* datasets choose), **Add Part** (Multi and
  Geometry datasets), **Vertices** (drag a vertex, click an edge to add one, Alt+click to remove one), **Move**,
  **Rotate…**, **Duplicate**, **Split** (draw the cutting line), **Merge** (the oldest saved feature is kept with
  its attributes) and **Delete**.
- **Snap** to the vertices and edges of every visible layer, and **Trace** along existing features while
  drawing.
- **Attributes:** a form in the Feature panel, typed by the attribute schema. Select several features to set one
  field on all of them, or double-click an attribute table cell.
- **Undo/Redo** (Ctrl+Z / Ctrl+Y) for every change, including split, merge and attribute edits.
- **Validate** checks the edited geometries on the server without saving.
- **Save** sends every change in one request, saved as one transaction: if any change fails, nothing is saved,
  and each failure is listed in the Feature panel.
- **Conflict check:** each change carries the revision the browser loaded. A feature that someone else changed
  or deleted since then is refused as a conflict.

Changes go through the GIS Feature document (`gis_edit_service.save_edits`), so permissions, validation,
revisions and version history apply as in Desk. The browser warns before you leave a page with unsaved changes.

**Elevation while editing.** For datasets with *Has Z*:

- vertices that did not move keep their stored Z;
- new vertices are interpolated along their line or ring;
- a moved single point keeps its Z;
- anything else takes the **Z** entered in the editing toolbar. Without that Z, the save is refused rather than
  guessing an elevation.

### 7. Publishing and the QGIS gateway

**Publishing.** Each active spatial dataset gets its own QGIS project, `<QGIS Project Directory>/<dataset>.qgs`,
written by the PyQGIS worker `geo_planning/qgis_worker/publish.py`:

- One layer (a group of six layers, one per geometry type, for *Geometry* datasets) reads `tabGIS Feature`
  through the read-only account, filtered to the dataset, with the dataset's geometry type and CRS. The QGIS
  feature ID is the GIS Feature ID.
- A typed expression field for each attribute (Text, Integer, Float, Boolean, Date, Datetime), using the
  attribute labels as aliases.
- Only the feature ID, label, revision, validation status, Min/Max Z (datasets with Z) and the attributes are
  visible in WMS and WFS. The raw JSON and Frappe's own columns are hidden.
- A single-symbol style in the dataset's *Default Colour*. Fuller styling comes with the cartography phase.
- WMS, plus WFS for reading only (no WFS-T), with EPSG:3857, EPSG:4326 and the dataset's CRS.

The worker writes the project in a temporary directory, loads it back to check it, and moves it into place
with mode 0640.

Features are read live, so editing features does **not** need a republish. A dataset is republished in the
background when its title, geometry type, CRS, Z setting, colour or attribute schema changes, and every
dataset is republished when the QGIS account or the project directory changes in GIS Settings. The dataset
form shows the publish status and has a **Publish to QGIS** button. GIS Settings has **Publish All Datasets**.
Archived and deleted datasets are unpublished, and their project file is removed.

**Gateway.** Browsers never reach QGIS Server. Every map, legend and feature request goes to:

```
GET /api/method/is_production.geo_planning.services.gis_gateway_service.ows?dataset=<name>&SERVICE=WMS&REQUEST=GetMap&...
```

The gateway:

- checks the session's read permission on the dataset, including User Permissions;
- allows only WMS GetMap / GetFeatureInfo / GetLegendGraphic / GetCapabilities and WFS GetCapabilities /
  DescribeFeatureType / GetFeature;
- sets the project itself from the dataset, ignoring any `MAP` sent by the client. Each project contains only
  its own dataset, so a request cannot reach other data;
- caps image sizes (4096 px), WFS feature counts (*Max Features per Request*) and GetFeatureInfo counts.

### 8. Plugins

**GIS Plugin Settings** (Single) controls plugins:
- **Policy:** third-party plugins are switched **off** by default. Separate switches allow processing plugins
  and QGIS Server plugins. *Enforce Compatibility* is on, experimental plugins are off, and packages are limited
  in size.
- **Sources:** an *Allowed Download Sources* list (default `https://plugins.qgis.org/`) and the plugin
  repository URL.
- **Directories:** where server plugins go.
- **Diagnostics:** a summary, folders that have no plugin record, plugins with errors, and *QGIS Server Restart
  Required*.

**GIS Plugin** records each plugin, its package (a private file with a SHA-256 checksum), metadata, analysis,
dependencies, installation and history. Only **System Managers** change plugins. The web workers never import
plugin code.

A plugin goes through these steps:

1. **Register:** upload the plugin's `.zip`, set a *Source URL* from an allowed source and click *Download
   Package*, or use **GIS Plugin Settings → Browse QGIS Plugin Repository**. That lists plugins for the
   installed QGIS version from plugins.qgis.org, and *Register* downloads one.
2. **Analyse** (`qgis_worker/plugin_worker.py`, system Python): this never imports or runs the plugin.
   - It checks the zip is safe: one top-level package containing `__init__.py` and `metadata.txt`, no absolute
     paths, no `..`, no links, and a capped size.
   - It reads `metadata.txt` and parses the Python with `ast` to find imports, Qt widgets and `iface` use,
     processing providers, `initProcessing`, `serverClassFactory` and compiled modules.
   - It checks Python dependencies against the system Python and the QGIS version range against the installed
     QGIS.
   - The result is one of:
     *Native server-compatible*, *Processing-compatible*, *Browser-adapter compatible*, *Requires manual
     adaptation* or *Unsupported*.
3. **Install:** allowed only by policy, and only when the analysis matches the plugin's kind with every
   dependency met. The package checksum must still match the analysed one.
   - **Processing plugins** are unpacked into `sites/<site>/private/gis_plugins/processing/`.
   - **Server plugins** go to a private staging folder first.
   - **Desktop plugins** and **Frappe GIS plugins** are registered and classified only: a QGIS Desktop
     plugin's Qt interface cannot run in a browser (adapters are the *Desktop plugin compatibility* phase).
4. **Enable.**
   - A processing plugin is loaded by the processing worker only. Its algorithms appear in the Processing
     toolbox under the plugin's provider. A plugin that fails to load is marked *Error* with the message.
   - A server plugin is copied into QGIS Server's plugin directory (`QGIS_PLUGINPATH`). QGIS Server loads plugins
     when it starts, so run `sudo systemctl restart qgis-server` and then *Mark QGIS Server Restarted*. The bench
     user must be able to write that directory:

     ```bash
     sudo chown <bench user>:www-data /var/lib/qgis-server/plugins && sudo chmod 2750 /var/lib/qgis-server/plugins
     ```
5. **Disable / Uninstall:** removes the plugin from the worker's list or from QGIS Server's directory, then
   deletes its unpacked files.

**Plugin catalogue (Plugins → Manage and Install Plugins…).** The workstation has a plugin window laid out like
QGIS's, with tabs *All*, *Installed*, *Not Installed*, *Upgradeable*, *Invalid* and *Requests*. Search covers
plugins.qgis.org (for the installed QGIS version) together with the plugins registered here.

- **Every GIS user** sees the approved (enabled) plugins. They can switch an approved processing plugin **off or
  on for their own Processing Toolbox**; the choice is stored per user, and its algorithms are hidden from them
  and refused if they try to run one. They can also **request** a repository plugin with a reason. The request
  becomes a GIS Plugin record (*Requested*), and System Managers are notified in Frappe.
- **System Managers** act on requests (*Download and Analyse*, or *Decline* with a reason, which notifies the
  requester), register repository plugins directly, and install, *Enable for Everyone*, disable, **upgrade**
  (download the newer version, analyse, reinstall, re-enable) and uninstall.

The worker records which Processing providers each plugin adds, so a user's choices hide exactly that plugin's
algorithms.

The daily health check scans the plugin directories and reports plugin errors and a pending QGIS Server
restart.

### Map services (basemaps and overlays)

**GIS Map Service** records are the basemaps and overlay layers offered in the workstation (Mining Planning →
GIS → Map Services). The same imagery choices as the Mining Simulation are created on install:

| Service | Use | Notes |
|---|---|---|
| OpenStreetMap | Basemap (street) | OSM tile usage policy applies |
| Esri World Imagery | Basemap (**satellite**) | No key needed |
| Esri World Topographic, OpenTopoMap | Basemap (topographic) | |
| Google Satellite | Basemap (**satellite**) | **Disabled** until a Google Maps Platform key with the Map Tiles API is entered |
| Place Names and Boundaries (Esri) | Overlay | Labels to put over satellite imagery |

You can add more:
- **XYZ** tile URLs, e.g. Mapbox, MapTiler, Thunderforest or an in-house tile server;
- **WMS** layers;
- more **Google Map Tiles** types (satellite, roadmap, terrain, with optional label layers).

**Access to locked services.** A service can use an API key (in the URL as `{api_key}`, or in a named query
parameter), an HTTP header, or basic authentication. The secrets are stored as Password fields.

*Delivery* decides who fetches the tiles:
- **Direct:** the browser fetches them. An API key is visible to the browser, so restrict the key to this site's
  address in the provider's console, as Google and Mapbox recommend.
- **Through Frappe:** the server fetches them and adds the credentials, which never reach the browser. HTTP
  header and basic authentication always work this way. The proxy only reaches public internet addresses: a
  service that resolves to a private, loopback or link-local address is refused, so it cannot be pointed at
  QGIS Server or other internal systems. Tiles are cached by browsers for a day, not on the server.

In the workstation:
- the **Basemap** picker (bottom of the Layers panel, or *View*) chooses the basemap and its opacity;
- overlays are listed in the Browser and added like layers;
- projects save the basemap and overlays.

Exported QGIS projects include the services that need no credentials, as QGIS XYZ/WMS layers. Keyed services
are left out of packages and the job log says so.

Check each provider's terms of use. Most require the attribution, which the map shows.

### 9. Import and export

Imports and exports run as background jobs. Each one is recorded as a **GIS Import Export Job** (Mining
Planning → GIS → Import / Export Jobs) with the user, time, datasets, format, CRS, counts and a log. GDAL and
QGIS do the reading and writing in the worker `geo_planning/qgis_worker/gis_io.py` under the system Python.
In the workstation, use **Layer → Import Dataset… / Export…** and **Project → Export Project to QGIS… /
Import QGIS Project Package…**.

**Import** works in three steps:

1. **Upload and inspect.** Supported files are GeoPackage, GeoJSON, KML/KMZ, GML, GPX, CSV with coordinates and
   DXF, plus a `.zip` holding Shapefiles (`.shp/.shx/.dbf/.prj`) or a File Geodatabase. Every layer is listed
   with the geometry types actually found in it, its feature count, Z values, CRS (ESRI `.prj` files are matched
   to an EPSG code) and fields.
2. **Map each layer.** Choose *Create a new dataset* (name, title, geometry type and CRS are all suggested),
   *Append to a dataset* (fields go to attributes of the same name), or *Skip*. Fields can be renamed, retyped
   or left out. Their original names are kept on the dataset (*Source Field Name*), so exports restore them.
   - **CSV:** choose the X/Y/Z columns. For South African Lo data the **Y** column (westing) is x and the **X**
     column (southing) is y.
   - **Reprojection is never silent.** If the file's CRS differs from the dataset's, the import is refused until
     *Reproject* is ticked. A file without a CRS is taken to be in the dataset's CRS, and the log says so. The
     original CRS is kept on the dataset (*Source CRS*, *Import Metadata*).
3. **Run.** Features are inserted in chunks as GIS Features, so they are validated, typed and permission-checked
   like any other save. Curves are converted to straight segments. A feature that fails is skipped and listed in
   the log; the rest import. New datasets are published to QGIS automatically.

**Export.** The workstation can export the active layer, the selected features, all layers in the map, or a saved
GIS Project, in each dataset's own CRS or a chosen one:

| Format | Notes |
|---|---|
| GeoPackage | One file, one layer per dataset. The main format for QGIS Desktop. |
| GeoJSON, GML | One file per dataset (zipped when there are several). |
| Shapefile | Zipped. Field names are cut to 10 characters, and the job log lists the new names. |
| KML | Always WGS 84 (EPSG:4326). |
| CSV | Geometry as WKT, with a `.csvt` file giving the field types (zipped). |

- Exports contain only the features the user may read. Each feature carries `gis_feature_id` and `label`, then
  its attributes under their original source names, and keeps its Z values.
- The file is attached to the job as a private file, and only the job's owner and Production Managers can
  download it. Deleting the job deletes the file.

**QGIS project packages.** *Export Project to QGIS* writes a zip holding a GeoPackage of the project's datasets
and a `.qgz` project. The project has the layer order, visibility, opacity, colour, display CRS and saved extent,
with relative paths, so it opens in QGIS Desktop wherever it is unpacked. *Import QGIS Project Package* takes
such a zip back, including one edited in QGIS Desktop. Its file-based vector layers become datasets, and a
new **GIS Project** recreates the map. Layers whose data is not inside the zip are listed in the log as
skipped. Styling beyond a single colour is not carried yet; that comes with the cartography phase.

**Upload size.** Frappe limits uploads to 10 MB unless the site sets a larger limit (`max_file_size` in
`site_config.json`, in bytes, or *System Settings*). Raise it for real survey files. The web server's own body
limit (`client_max_body_size` in the bench nginx config) must allow the same size. Exports are written on the
server and are not affected by these limits.

### 9a. QGIS Processing

**Processing → Toolbox…** in the workstation runs the QGIS Processing algorithms installed on the host:
QGIS native, GDAL and GRASS (730 on QGIS 3.44, 581 of them usable from the web).

- **Registry.** `qgis_worker/processing_worker.py` lists every algorithm with its parameter and output
  definitions. The list is cached in `sites/<site>/private/gis_processing/registry.json` and rebuilt by
  **GIS Settings → Refresh Processing Algorithms**, or by the daily health check when the QGIS version changes.
  Some algorithms need what a browser cannot provide: a QGIS Desktop project, folder outputs, database
  connections, or mesh or file inputs. These are listed with the reason and cannot be run.
- **Form.** The toolbox builds each algorithm's form from its definition:
  - datasets are filtered by geometry type, with *Selected features only* for the active layer;
  - numbers are checked against the algorithm's minimum and maximum;
  - choice lists, booleans, text and expressions get matching inputs;
  - field lists come from the chosen dataset's attributes;
  - CRS, extent and point parameters can be filled from the current map view.
- **Running.** Each run is a **GIS Processing Job**, executed in the background by its own PyQGIS process:
  1. **Inputs:** the input datasets are written to GeoPackages in a private workspace. Only the features the
     user may read are included, with Z values kept and the dataset's attribute names, so field and expression
     parameters match.
  2. **Run:** the algorithm runs at lower CPU priority, with a timeout and an optional memory limit (GIS
     Settings → Processing). Progress and log lines stream into the job, and **Cancel** stops it.
  3. **Clean up:** the workspace is deleted afterwards.
- **Outputs.**
  - Vector outputs become **new** GIS Datasets (category *Processing Output*, source = the job). Processing
    never writes into an existing dataset: an output name that already exists is refused.
  - Raster outputs become *Raster* datasets, with their GeoTIFF as a private file.
  - Reports and other files are attached to the job.
  - Numbers and text (e.g. statistics) are stored in the job's results.

  New datasets are published to QGIS, and the toolbox can add them to the map.
- **History.** The toolbox's *History* tab and Mining Planning → GIS → Processing Jobs list past runs with their
  parameters, log and outputs.
- **Permissions.** Running a job needs create permission on GIS Processing Job (Production Manager, Production
  User). Algorithms that produce a dataset also need create permission on GIS Dataset, which only Production
  Manager has. Production Users can therefore run algorithms that only return values or files. Users see only
  their own jobs; Production Managers see all.

### 10. Security and permissions

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

### 11. Known limitations

- The app is not on par with every QGIS Desktop feature or plugin, and Qt plugin dialogs do not run in the browser.
- MariaDB's spatial functions are fewer than PostGIS's. Advanced analysis runs through QGIS Processing, not SQL.
- MariaDB geometries are 2D. Z values are kept in the feature's GeoJSON, plus Min/Max Z columns; QGIS layers read
  from MariaDB have 2D geometry.
- The Python packages `pyproj`, `rasterio` and `geopandas` in the bench `env/` bring their **own** copies of PROJ
  and GDAL, which can be different versions from the system ones that QGIS uses. CRS transforms done in the bench
  and in QGIS can therefore differ slightly. GIS transforms should always run through QGIS/PROJ in the worker.
- The GIS datasets are not linked to the Mining Simulation yet. That link is a later step.
- Editing works on the features loaded for the current view (*Max Features per Request*). Zoom in to edit
  a dense area completely.
- Coordinates are edited in Web Mercator in the browser and transformed back to the dataset's CRS with PROJ on
  the server. Unmoved vertices come back within a fraction of a millimetre.

### 12. Upgrades and backups

- **Before upgrading QGIS**, note the current version shown in GIS Settings. Run the upgrade on the lab site
  first, then run the dependency check and the GIS tests. Hold the packages on production
  (`sudo apt-mark hold qgis-server python3-qgis`) until the new version has been tested.
- After `bench migrate`, check that the spatial columns and indexes are still there (see [Datasets, features and the geometry schema](#5-datasets-features-and-the-geometry-schema)).
- **Backups:** the vector data is in MariaDB and is included in `bench backup`. Back these up together with the
  database, using `bench backup --with-files` plus the paths below:
  - raster files and uploaded datasets (Frappe private files)
  - plugin packages (private files attached to GIS Plugin). `sites/<site>/private/gis_plugins/` and
    `/var/lib/qgis-server/plugins` can be rebuilt by installing and enabling the plugins again
  - `sites/<site>/private/gis_processing/` holds the algorithm registry (rebuilt on demand) and running jobs'
    workspaces, and does not need backing up
  - `sites/<site>/private/gis_io/` holds the working files of running import and export jobs only, and does
    not need backing up
  - the project directory is **not** essential: GIS Settings → *Publish All Datasets* rebuilds every project file
  - `/etc/systemd/system/qgis-server.service` and `/etc/nginx/conf.d/qgis-server-local.conf`

### Development phases

| Phase | Deliverables |
|---|---|
| 1 — Foundation ✅ | GIS Settings, QGIS health checks, `/portal_gis` workstation shell, Mining Planning shortcut |
| 2 — Spatial storage ✅ | GIS Dataset, GIS Feature, MariaDB geometry adapter, Z range |
| 3 — Server integration ✅ | Publishing layers, QGIS rendering through the gateway, reading features |
| 4 — Editing ✅ | Vector tools, attribute editing, saving and validation |
| 5 — Exchange with QGIS Desktop ✅ | GeoPackage, GeoJSON, Shapefile, transfer of QGIS projects |
| 6 — Processing ✅ | Algorithm registry, PyQGIS jobs, saving outputs |
| 7 — Cartography and raster | Styling, labels, layouts, raster tools |
| 8 — Plugins ✅ | Plugin Settings, Plugin registry, server and processing plugins |
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
| [Lucide](https://lucide.dev) | Workstation toolbar and menu icons | ISC |
| [Vue](https://vuejs.org/) | GIS Workstation interface (from Frappe's build) | MIT |
| [OpenStreetMap](https://www.openstreetmap.org/copyright) | Optional basemap tiles | ODbL (data), attribution shown on the map |
| [three.js](https://threejs.org/) | Mining Simulation 3D view (vendored) | MIT |
| Python packages in `pyproject.toml` (shapely, geopandas, pyproj, rasterio, ...) | Geo Planning | See each package |

The copyright notices and license files of vendored libraries are kept next to the libraries under `public/`.

#### Licence

Copyright (C) 2025-2026 Isambane Mining (Pty) Ltd. All contributions were made by Isambane employees under copyright assignment.

This app is free software: you can redistribute it and/or modify it under the terms of the **GNU General Public
License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any
later version** (GPL-3.0-or-later). It is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See
`license.txt` for the full licence text.

**Why GPL-3.0-or-later.** It fits every GPL component the app depends on:

- **QGIS** (QGIS Server, PyQGIS) and **GRASS GIS** are GPL-2.0-or-later. The PyQGIS workers in
  `geo_planning/qgis_worker/` import QGIS, and QGIS plugins load into it; GPL-2.0-or-later code may be combined
  with GPL-3.0 code.
- **ERPNext**, a required app, is GPL-3.0.
- The permissively licensed parts are compatible with GPL-3.0: Frappe (MIT), OpenLayers (BSD-2-Clause), proj4js
  (MIT), Lucide (ISC), three.js (MIT), GDAL and PROJ (MIT/X). Keep their copyright notices and licence texts
  with them, as the table above lists.

**If you distribute the app** (give a copy to anyone outside Isambane, or ship it with QGIS or as a package):

- pass on the source code with it, or a written offer to provide it;
- keep the GPL licence text and every copyright and licence notice, and say what you changed;
- the QGIS packages themselves are distributed by QGIS.org / Debian under their own GPL terms. Installing them on
  a host, as described above, does not make this repository a QGIS distribution.

Running the app for Isambane's own users on Isambane's servers is not distribution. GPL-3.0 (unlike AGPL-3.0)
does not require offering the source to people who only use it over the network.
