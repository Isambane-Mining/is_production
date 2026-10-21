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
- Create the project folders. The app writes one `.qgs` per published dataset into **a folder of its own site**:
  `<QGIS Project Directory>/<site name>/`. The files contain that site's read-only database password, so each
  site's folder belongs to its bench's user and QGIS Server's group, and is closed to everyone else. The shared
  base folder belongs to root: benches can pass through it but cannot list it.

  ```bash
  sudo install -d -o root -g www-data -m 0751 /var/lib/qgis-server/projects            # once per host
  sudo install -d -o <bench user> -g www-data -m 2750 /var/lib/qgis-server/projects/<site name>   # once per site
  ```

  GIS Settings → *Check Dependencies* shows the exact command for a site whose folder is missing.

#### Several benches and sites on one host

One QGIS Server serves every bench and site on the host. Each Frappe bench may run as its own Unix user (e.g.
one bench per developer plus production).

| Shared | How sites stay apart |
|---|---|
| QGIS Server | The gateway always names the project file, and a site's projects contain only its own datasets |
| Project folder | One subfolder per site (above): names cannot collide, and no bench can read another's files |
| Database account | Give each site **its own** read-only account (e.g. `qgis_ro_<db name>`) with `SELECT` on its own `tabGIS Feature` only |
| Server plugin folder | QGIS Server loads its plugins for every site, so exactly **one** site turns on GIS Plugin Settings → *Manage QGIS Server Plugins*; the others never touch that folder |

Processing plugins, the algorithm registry, import/export work files and map services are per site (in the
site's private files or database).

### 3. Database user for QGIS Server

QGIS Server reads features through the GDAL/OGR MySQL driver, using its **own read-only** MariaDB account.
QGIS never uses the site's own database user. It needs `SELECT` on **one table only**:

```sql
-- run as a MariaDB admin; replace <site_db> with the site's db_name from site_config.json
CREATE USER 'qgis_ro_<site_db>'@'localhost' IDENTIFIED BY '<strong password>';
GRANT SELECT ON `<site_db>`.`tabGIS Feature` TO 'qgis_ro_<site_db>'@'localhost';
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

One account per site keeps sites apart on a shared host. Enter it in **GIS Settings → QGIS Database Account**. The password is stored as a Password field and is
sent to the PyQGIS worker on stdin, never on a command line.

### 4. Install the app

```bash
bench get-app <is_production_repository_url>
bench --site <site_name> install-app is_production
bench --site <site_name> migrate
bench build --app is_production      # builds the GIS Workstation bundle (OpenLayers, proj4js, Vue)
bench restart
```

Then connect the site to the host's QGIS Server. Run this **as the bench's own user**, once per site:

```bash
bench --site <site_name> gis-setup              # add --print-only first to see what it will do
```

Nothing in the app is tied to a particular server, user or site. The command works out the bench's Unix user,
the site and its database, and QGIS Server's group (from the `qgis-server` systemd unit). It then:

1. checks that QGIS Server answers and that PyQGIS imports under the system Python;
2. creates, with `sudo`, the shared project base folder if needed and this site's own folder `<base>/<site>`
   (bench user, QGIS Server's group, mode 2750);
3. creates or re-keys this site's read-only database account `qgis_ro_<db name>`. It gets a random password and
   `SELECT` on this site's `tabGIS Feature` only. This step runs as the MariaDB administrator, using
   `--db-root-username/--db-root-password`, the bench's `root_login`/`root_password` config, `sudo mariadb`, or a
   prompt;
4. fills in GIS Settings (which republishes the site's datasets) and runs the health check.

It is safe to run again. Options:
- `--qgis-url` and `--qgis-python` for a QGIS Server or Python elsewhere than the defaults;
- `--project-dir` and `--qgis-group` to override the project base folder and QGIS Server's group;
- `--skip-account` to keep an existing database account;
- `--manage-server-plugins` for the one site that manages QGIS Server plugins.

To set it up by hand instead, see sections 2–3 and enter the values in **GIS Settings**, then click **Check
Dependencies**.

**Per host** (once, as an administrator): install QGIS (section 1) and set up the `qgis-server` service and its
local nginx front (section 2). **Per bench and site:** `bench --site <site> gis-setup`.

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
shortcuts. The toolbar and menu icons are **QGIS's own default-theme icons** (by Robert Szczepanek and Anita Graser,
CC-BY-SA 3.0), shipped unmodified as separate files in `public/qgis_icons/` with their licence and attribution.
Small interface-chrome icons (close, expand, visibility) come from [Lucide](https://lucide.dev) (ISC). The About
box says the workstation is not an official QGIS product.

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
   - A server plugin is copied into QGIS Server's plugin directory (`QGIS_PLUGINPATH`). Only the one site with
     *Manage QGIS Server Plugins* turned on can do this, because the folder is shared by every site on the host.
     QGIS Server loads plugins when it starts, so run `sudo systemctl restart qgis-server` and then *Mark QGIS
     Server Restarted*. The managing site's bench user must be able to write that directory:

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

## CAD Engineering Workstation (Open CASCADE)

### 1. Introduction and purpose

The CAD Engineering Workstation (`/portal_cad`, module **CAD Engineering**) is a browser-based 2D/3D CAD
environment for mine design, built into `is_production` next to the GIS Workstation and the Mining Simulation.
Frappe owns users, permissions, records, revisions and workflow; **Open CASCADE Technology (OCCT)** does the
precision geometry in an isolated worker; **three.js** draws it in the browser. QGIS stays responsible for GIS.
The aim is an open-source alternative for selected workflows of commercial mining CAD tools (for example Deswik
CAD or MicroStation); those names describe the kind of work only, and no code, assets or algorithms of them are
used.

Open it from **Mining Planning → CAD Workstation**, from a CAD Model (**Open in CAD Workstation**), or at
`/portal_cad?model=<Model Code>`.

### 2. Architecture

```text
Browser (/portal_cad: Vue + three.js)        draws relative to the model's local origin
        │  whitelisted API, CSRF, Frappe permissions on every call
        ▼
cad_engineering/services/                   Frappe (bench Python)
  cad_workstation_service   API             cad_model_service     edits, conflict check, model stats
  cad_revision_service      revisions       cad_storage_service   geometry files (provider interface)
  cad_job_service           CAD Processing Job (RQ "long" queue)
        │  JSON request on stdin, private job folder, no database credentials
        ▼
cad_engineering/cad_worker/geometry_worker.py   isolated Python + OCP (OCCT): build, validate, measure, mesh
```

**Geometry storage.** Three kinds of data are kept apart:

| What | Where | Authoritative? |
|---|---|---|
| Identity, layer, bounding box, measures, exact parametric geometry of points/curves/text (JSON doubles) | `CAD Entity` rows | yes |
| Solids (and later meshes/surfaces): OCCT B-Rep files, content-addressed by SHA-256, plus the server's record of how each was built | `<site>/private/cad/models/<model>/blobs/` | yes |
| Frozen revision snapshots (gzip JSON, write-once, SHA-256 over the canonical content) | `<site>/private/cad/models/<model>/revisions/` | yes (immutable) |
| Display meshes (float32 relative to their own origin) | `<site>/private/cad/mesh_cache/` | **no** — a cache, safe to delete |

A display mesh is never used for a calculation; lengths, areas, volumes and topology checks come from OCCT on
the authoritative geometry. Coordinates keep full double precision in the database (`decimal(21,9)`) and in
OCCT; the browser subtracts the model's local origin before anything becomes float32, so survey coordinates
such as `X = -19 500, Y = 2 860 500` draw to sub-millimetre precision.

**Revisions.** A model has one *Working* revision (its live entities and layers). *Submit for Review* freezes a
snapshot; from then on that revision can never change. A Production Manager approves or rejects it. The next edit
opens a new Working revision whose parent is the last one. *Restore as New Revision* copies an old snapshot back
into the working state (the snapshot's checksum is verified first). Models with approved revisions are archived,
not deleted.

### 3. Software dependencies

| Component | Version tested | Where |
|---|---|---|
| Python for the worker | 3.13 (Debian 13 system `python3.13` + `python3-venv`) | system |
| [cadquery-ocp](https://pypi.org/project/cadquery-ocp/) (OCP bindings, bundles OCCT) | **7.9.3.1.1** → OCCT 7.9.3 | worker venv |
| vtk (dependency of cadquery-ocp) | 9.6.2 | worker venv |
| ezdxf (DXF import/export) | 1.4.5 | worker venv |
| pyproj (reprojection during DXF import) | 3.8 (PROJ 9.8) | worker venv |
| numpy / scipy (surfaces, TIN, Phase 3) | 2.5 / 1.18 | worker venv (optional) |
| three.js | 0.184 (`package.json`) | browser bundle |

`cadquery-ocp` has manylinux wheels for CPython 3.11–3.14 on x86-64 and aarch64; versions 7.9.x and 8.0.x both
work with the worker. Keep to an explicitly pinned version and re-run the health check after upgrading.

### 4. Supported operating systems

Tested on Debian 13 (trixie) x86-64. Any Linux with glibc ≥ 2.28 that runs CPython 3.11–3.14 can install the
wheels (Ubuntu 22.04+, Debian 12+, RHEL 9+). The worker needs no display server and no GPU.

### 5. Open CASCADE installation

OCCT comes inside the `cadquery-ocp` wheel; nothing is installed system-wide and the Debian `libocct-*`
packages are **not** needed. Do not install OCCT into the bench's virtual environment.

### 6. Python / CAD binding requirements

The worker runs under a separate Python, never the bench's (`env/`): the bench uses its own Python and native
geometry libraries in the web process could crash it. The worker imports only OCP, NumPy/SciPy and ezdxf; it
does not import Frappe.

### 7. CAD worker installation

Once per host (shared by every bench and site on it, like QGIS):

```bash
sudo apt install python3 python3-venv          # Debian 13: Python 3.13
sudo install -d -o root -g root -m 0755 /opt/cad-worker
sudo python3 -m venv /opt/cad-worker/venv
sudo /opt/cad-worker/venv/bin/pip install --upgrade pip
sudo /opt/cad-worker/venv/bin/pip install "cadquery-ocp==7.9.3.1.1" "ezdxf==1.4.5" "pyproj>=3.7,<4" "numpy>=2.2,<3" "scipy>=1.15,<2"

# check: prints the OCCT self-test result (a boolean cut at survey coordinates)
echo '{"command": "health"}' | /opt/cad-worker/venv/bin/python -I \
    apps/is_production/is_production/cad_engineering/cad_worker/geometry_worker.py
```

The venv is owned by root and read-only to the bench users. About 1.3 GB on disk.

### 8. Service configuration (systemd)

No extra systemd unit is needed. The worker is an **isolated process started per job** by the bench's existing
RQ `long` workers (managed by the bench's supervisor or systemd units), so it inherits nothing from the web
process, ends with the job, and cannot leak state between sites. Restart the bench workers after deploying new
`cad_engineering` code: `sudo supervisorctl restart '<bench>-workers:'` (and `<bench>-web:` for Python changes
in the web process). A long-running worker service on a separate processing host can be added later behind the
same JSON request format.

### 9. Worker isolation

Each run gets `python -I` (no user site, no `PYTHON*` variables), a minimal environment (`PATH`, `HOME`, `LANG`),
a fresh private folder `<site>/private/cad/jobs/<job>/` (removed afterwards) as its working directory, and only
copies of the files it needs. It refuses any path outside that folder, never receives database credentials, and
applies CPU priority (`nice`), an address-space limit (`RLIMIT_AS`) and a CPU-time limit (`RLIMIT_CPU`) before
it touches geometry. The bench kills it at the wall-clock limit or on **Cancel**. The bench recomputes the
SHA-256 of every file the worker returns before storing it. Imported files are parsed only as data; macros or
scripts in CAD files are never executed.

### 10. CAD Settings configuration

**CAD Settings** (Production Manager / System Manager):

| Field | Default | Meaning |
|---|---|---|
| Enable CAD Workstation / CAD Worker Enabled | on | switch the page or the worker off |
| CAD Worker Python | `/opt/cad-worker/venv/bin/python` | must not be the bench's Python |
| Maximum Processing Time | 30 min | wall-clock and CPU limit per job |
| Maximum Concurrent Jobs | 2 | per site; further jobs wait |
| Worker Memory Limit | 4096 MB | `RLIMIT_AS`; 0 = none |
| Worker CPU Priority | 10 | `nice` |
| Model Storage / Mesh Cache Directory | `private/cad/models`, `private/cad/mesh_cache` | relative to the site folder |
| Maximum Import Size | 200 MB | (Phase 2 imports) |
| Default units, CRS, precision, export format | Metres, Degrees, EPSG:2053, 3 / 4 decimals, DXF | new models and display |

### 11. Storage directory permissions

The directories are created by the bench user on first use with mode `0750` (files `0640`) inside
`<site>/private/`, so each site's models are separate and not web-readable. The storage service only accepts
keys of the form `models|mesh/<model code>/...` and refuses anything that resolves outside its root.

### 12. Model storage and backup

`bench backup --with-files` includes `private/cad/models/` (authoritative geometry and snapshots) because it is
inside the site's private files. `private/cad/mesh_cache/` can be excluded or deleted at any time; meshes are
rebuilt on demand (**Tessellate** jobs). Restore the database and `private/cad/models/` together: entity rows
refer to B-Rep files by checksum, and revision records refer to their snapshot files.

### 13. Supported CAD formats

| Format | Direction | Status |
|---|---|---|
| Native: entity JSON + OCCT B-Rep (`.brep`) + snapshot JSON | read/write | Phase 1 ✅ |
| DXF R12–R2018 (ASCII and binary) | import/export (ezdxf) | Phase 2 ✅ |
| GeoJSON (via GIS datasets) | import/export | Phase 5 (GIS ↔ CAD) |
| CSV / XYZ, LandXML, STL, OBJ, STEP | import/export | Phase 3 |
| LAS/LAZ (PDAL), IFC, DWG/DGN | conditional | later; only after interoperability testing |

No export is described as lossless unless round-trip tests prove it for the entities concerned.

#### DXF exchange (Phase 2)

**Import** (File → Import DXF, or `IMPORT`) is two steps. The uploaded file is read by the isolated worker and a
**validation report** is shown — DXF version, units (`$INSUNITS`), layers, blocks, counts per type, unsupported,
exploded and invalid entities (with handles), exact duplicates, extents and elevation range, repaired file errors —
before anything is written. On **Import**, the choices are applied: drawing units (a similarity: arcs stay exact),
axis mapping (*Negate X and Y* / *Swap X and Y* for Lo data drawn with other sign conventions), and an optional
source CRS (reprojection with PROJ; curved entities are then densified and blocks exploded — reported). Everything
goes into the model's Working revision in one transaction; a model in review refuses imports; approved revisions are
never touched. The original file stays attached to the **CAD Import Export** record, and the model's
*Transformation History* records the import.

| DXF | Becomes |
|---|---|
| LINE, POINT, CIRCLE, ARC, ELLIPSE, SPLINE (control points, knots, weights; fit points kept) | the same, exact |
| LWPOLYLINE / 2D POLYLINE (bulges = arc segments) · 3D POLYLINE | Polyline |
| XLINE / RAY | Construction Line |
| TEXT (alignment kept) · MTEXT (formatting codes removed) · visible ATTRIBs | Text · Multiline Text · Text |
| DIMENSION linear/aligned/radius/diameter/angular-3-point | Dimension (others exploded into lines and text) |
| HATCH (polyline and line/arc edges exact; ellipse/spline edges densified) · SOLID/TRACE | Hatch |
| INSERT (+ the BLOCKs it uses, nested) | Block Reference + CAD Block |
| LEADER, MLEADER, MLINE, ACAD_TABLE, … | exploded into simple entities |
| 3DSOLID, BODY, REGION, MESH, 3DFACE, IMAGE, OLE2FRAME, VIEWPORT, paper space | not imported (listed) |

Object coordinate systems are resolved exactly: arcs, circles and polylines drawn with extrusion `0,0,-1` (mirrored
in AutoCAD) are stored as the same curves about +Z. Mirrored and rotated block references keep their exact transform.

**Export** (File → Export DXF) writes the saved working state or any frozen revision (historical revisions included)
as DXF R12–R2018 in the model's coordinates and units, with layers (true colour, line type, line weight, off/locked),
blocks, every 2D entity type, dimensions (rendered by ezdxf) and hatches. Solids are written as MESH approximations of
their display meshes, which the record states. Round-trip tests (export → import) reproduce every entity's exact
geometry (to 1e-6) and every block definition.

#### Drawing tools (Phase 2)

Draw: point, line, polyline (with tangent arc segments, `A`/`L`), rectangle, polygon, circle, arc (centre/3-point),
ellipse, spline (interpolated through fit points, stored as a NURBS), construction line, text, multiline text, aligned/
linear/angular/radius/diameter dimensions, hatch (SOLID, ANSI31, ANSI37, LINES, GRID, DOTS) of selected closed objects,
blocks (create from a selection, insert with scale and rotation). Modify: move, copy, rotate, scale, mirror, stretch
(crossing window), rectangular and polar arrays, offset, trim, extend, break, join, explode, fillet, chamfer, erase.
Solids follow move/rotate/scale/mirror/array exactly (OCCT transforms). Offsets of polylines are made by OCCT
(`BRepOffsetAPI_MakeOffset`); trims, extends, fillets and joins use exact line/arc intersections in the browser and
are re-checked and measured by OCCT after Save. Snaps: endpoint, midpoint, centre, quadrant, intersection,
perpendicular, tangent, nearest, node, insertion (each switchable, adjustable aperture), grid snap, ortho, polar
tracking. Selection: click, Shift+click, window (drag left→right), crossing (right→left). External references
(other models, live or a pinned revision) are drawn faded, read-only and snappable. A drawing template copies
layers and blocks into new models; the annotation scale sets text and dimension sizes.

**Plan orientation.** Models in a westing/southing CRS (South African Lo, e.g. EPSG:2053) are shown with north up:
the plan view is turned 180° (CAD Model → Plan Orientation; *Auto from CRS* by default). Coordinates are never
changed, and text is drawn upright in the turned view.

### 14. Performance configuration

Entities are loaded in pages of 5 000; the viewport draws them in a few large buffers per colour and line style
(not one object each), text as instanced glyphs (one shared triangle set per character), hides text smaller than two
pixels, and keeps a cached image of the drawing so pointer moves only redraw the cursor, snaps and previews. Snapping
uses a grid index of snap points and of curve segments. Solids are drawn from cached display meshes fetched as binary.
Measured on the lab with a 24 MB DXF of 51 500 entities (1 500 contours with 360 000 vertices, 20 000 spot heights and
labels, 8 000 lines, 2 000 block references): inspection 17 s, import 44 s, OCCT validation of all 51 500 entities
116 s (background jobs); opening in the browser about 20 s; about 400 MB of browser memory. Raise *Maximum
Concurrent Jobs* only if the host has spare cores: each OCCT worker is single-threaded and starts in about 1 s.
View-driven loading for still larger models and surfaces arrive with Phase 3.

### 15. Dependency health checks

**CAD Settings → Check Dependencies** (and daily) reports: worker Python, OCP/OCCT version, an OCCT boolean
self-test at survey coordinates (volume must match to 1e-6), job-folder access, model and mesh storage
writability and free space, optional libraries (ezdxf, NumPy, SciPy — warnings only) and whether the browser
bundle is built. Missing optional features are warnings; anything needed for Phase 1 is an error.

### 16. Troubleshooting

| Symptom | Fix |
|---|---|
| *CAD worker Python … does not exist* | install the worker (step 7) or correct CAD Settings → CAD Worker Python |
| *Cannot import the OCP binding* | `pip install` failed or the Python version has no wheel; reinstall in the venv |
| Jobs stay *Queued* | the bench's `long` workers are not running, or *Maximum Concurrent Jobs* are busy |
| *The CAD Workstation assets are not built* | `bench build --app is_production` |
| *Edit Conflict* on save | someone saved the model after you opened it: reload, redo the change |
| *Model Locked* | a revision is In Review: approve or reject it first |
| A solid shows as a box outline | its display mesh is missing; it is rebuilt automatically (Tessellate job) |
| Python changes do not take effect | restart the bench's web and worker processes |

### 17. Licensing and attribution

See *Acknowledgements and Licensing* below: Open CASCADE Technology (LGPL-2.1 with the OCCT exception),
cadquery-ocp (Apache-2.0), VTK (BSD-3-Clause), ezdxf (MIT), NumPy/SciPy (BSD-3-Clause), three.js (MIT), Vue (MIT),
Lucide (ISC), pyproj (MIT), Droid Sans (Apache-2.0). OCCT and the other worker libraries are installed on the host
from PyPI and are not part of this repository.

**Tests.** `bench --site <site> run-tests --skip-before-tests --module is_production.cad_engineering.doctype.cad_model.test_cad_model`
(models, edits, revisions, permissions, OCCT measures) and `…cad_import_export.test_cad_import_export` (geometry,
blocks, offsets, DXF import report/commit, units/axes, exact DXF round trip); browser geometry:
`node is_production/cad_engineering/tests/js/run.js`. The DXF fixture is made by `tests/make_sample_dxf.py`.

### 18. Development phases

| Phase | Scope | Status |
|---|---|---|
| 1 — Foundation | module, CAD Settings + health check, CAD Model / Layer / Entity / Design Revision / Processing Job, isolated OCCT worker, storage adapter, revisions with frozen snapshots, `/portal_cad` (menus, panels, 2D/3D viewport, local origin, point/line/polyline/rectangle/circle/arc/text, move/copy/erase, box/cylinder/extrude via OCCT, object/grid snap, ortho, absolute/relative/polar input, undo/redo, save/reopen), permissions, tests | ✅ |
| 2 — 2D CAD | full draw/modify set, all snaps, polar tracking, window/crossing selection, dimensions, hatches, blocks, external references, drawing templates, annotation scale, plan orientation, DXF import (validation report) and export, CAD Import Export, CAD Block | ✅ |
| 3 — 3D and surfaces | CAD Surface, TIN with breaklines, contours, sections/profiles, volumes, booleans, STL/OBJ/STEP/LandXML, view-driven loading | next |
| 4 — Mining design | CAD Design Standard (versioned), pit/bench/berm, haul roads, mining blocks, drill layout (geometry only) | |
| 5 — Integration | Geo Project / Mining Block / schedule links, design vs survey, revision comparison | |
| 6 — Advanced | parametric and advanced modelling, optimisation, point clouds, more formats | |

**Permissions.** There are no CAD-specific roles. *Production Manager* administers settings, creates and edits
models and **approves or rejects revisions**; *Production User* creates and edits models and submits revisions
but cannot approve or delete; *System Manager* has full access. Layers, entities, revisions and jobs follow their
model's record permissions, so User Permissions on Company, Branch, Location, Geo Project or CAD Model limit what
a user sees in the workstation and in list views. Every API method checks permissions on the server. When a
reviewer approves a revision, the Employee linked to their user is recorded with the approval.

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

#### Open CASCADE Technology

The CAD Engineering Workstation uses **Open CASCADE Technology** (https://dev.opencascade.org/), © OPEN CASCADE
SAS, through the OCP bindings of the CadQuery project. This app is not affiliated with or endorsed by OPEN
CASCADE SAS, the CadQuery project or three.js.

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
| QGIS default-theme icons (Robert Szczepanek, Anita Graser) | Workstation toolbar and menu icons, unmodified, in `public/qgis_icons/` with `LICENSE-CC-BY-SA-3.0.txt` | CC-BY-SA 3.0 |
| [Lucide](https://lucide.dev) | Workstation interface-chrome icons (close, expand, visibility) | ISC |
| [Vue](https://vuejs.org/) | GIS Workstation interface (from Frappe's build) | MIT |
| [OpenStreetMap](https://www.openstreetmap.org/copyright) | Optional basemap tiles | ODbL (data), attribution shown on the map |
| [three.js](https://threejs.org/) | Mining Simulation 3D view (vendored); CAD Workstation viewport (npm, bundled) | MIT |
| [Open CASCADE Technology](https://dev.opencascade.org/) | CAD worker geometry kernel (installed on the host inside cadquery-ocp, not bundled) | LGPL-2.1 with the Open CASCADE exception |
| [cadquery-ocp](https://github.com/CadQuery/OCP) | Python bindings for OCCT in the CAD worker (host venv) | Apache-2.0 |
| [VTK](https://vtk.org/) | Dependency of cadquery-ocp (host venv) | BSD-3-Clause |
| [ezdxf](https://github.com/mozman/ezdxf) | DXF import/export in the CAD worker (host venv) | MIT |
| [NumPy](https://numpy.org/) / [SciPy](https://scipy.org/) | Numerical and surface work in the CAD worker (host venv) | BSD-3-Clause |
| [pyproj](https://pyproj4.github.io/pyproj/) | Reprojection during DXF import (host venv) | MIT |
| Droid Sans (The Android Open Source Project) | CAD Workstation text, unmodified, in `public/cad_fonts/` with its NOTICE | Apache-2.0 |
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
  (MIT), Lucide (ISC), three.js (MIT), GDAL and PROJ (MIT/X), cadquery-ocp (Apache-2.0, compatible with GPL-3.0
  but not GPL-2.0), VTK, NumPy and SciPy (BSD-3-Clause), ezdxf (MIT).
- **Open CASCADE Technology** is LGPL-2.1 with an additional exception; LGPL-2.1 code may be used by GPL-3.0
  code. The CAD worker (`cad_engineering/cad_worker/`) runs as a separate process and only imports the OCCT
  bindings installed on the host; this repository neither contains nor modifies OCCT. Anyone who redistributes
  OCCT itself must follow its licence (source of OCCT, licence text, the right to relink). Keep their copyright notices and licence texts
  with them, as the table above lists.
- The **QGIS icons** in `public/qgis_icons/` are not GPL: they are artwork under **CC-BY-SA 3.0**, which cannot be
  merged into GPL code. They are therefore kept as separate, unmodified files, loaded by the workstation as
  images, with their licence text and attribution in that folder. The app's licence does not apply to them.
  Changed icons stay CC-BY-SA 3.0. The author asks to be told when the icons are used in other software.

**If you distribute the app** (give a copy to anyone outside Isambane, or ship it with QGIS or as a package):

- pass on the source code with it, or a written offer to provide it;
- keep the GPL licence text and every copyright and licence notice, and say what you changed;
- the QGIS packages themselves are distributed by QGIS.org / Debian under their own GPL terms. Installing them on
  a host, as described above, does not make this repository a QGIS distribution.

Running the app for Isambane's own users on Isambane's servers is not distribution. GPL-3.0 (unlike AGPL-3.0)
does not require offering the source to people who only use it over the network.
