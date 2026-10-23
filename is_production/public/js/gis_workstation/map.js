// The OpenLayers map of the GIS Workstation.
//
// Each dataset layer has two parts:
// - a vector layer whose features are fetched for the current view only (get_layer_features: native
//   spatial index, transformed to EPSG:3857 and simplified to the pixel size on the server), so a large
//   dataset is never loaded into the browser in full. It feeds the attribute table and the selection;
// - for datasets published to QGIS, an image layer rendered by QGIS Server (WMS GetMap through the
//   Frappe gateway). With the "qgis" renderer the image shows the layer, Identify uses QGIS's
//   GetFeatureInfo, and the vector layer only draws the selection highlight. With the "browser"
//   renderer OpenLayers draws the vectors itself.

import Map from "ol/Map.js";
import View from "ol/View.js";
import ImageLayer from "ol/layer/Image.js";
import TileLayer from "ol/layer/Tile.js";
import VectorLayer from "ol/layer/Vector.js";
import ImageWMS from "ol/source/ImageWMS.js";
import XYZ from "ol/source/XYZ.js";
import VectorSource from "ol/source/Vector.js";
import GeoJSON from "ol/format/GeoJSON.js";
import { Circle, Fill, Stroke, Style } from "ol/style.js";
import { MultiPoint } from "ol/geom.js";
import { fromLonLat, toLonLat } from "ol/proj.js";
import { boundingExtent, buffer, createEmpty, extend, getCenter, isEmpty } from "ol/extent.js";
import { defaults as default_controls, ScaleLine } from "ol/control.js";
import { DragBox, DragZoom } from "ol/interaction.js";
import { always } from "ol/events/condition.js";
import { call } from "./api.js";
import { EditSession } from "./edit.js";

export const GATEWAY = "/api/method/is_production.geo_planning.services.gis_gateway_service.ows";
const PALETTE = ["#2f80ed", "#eb5757", "#27ae60", "#f2994a", "#9b51e0", "#00a3a3", "#c0392b", "#7f8c8d"];
const SELECTED = "#ffb000";
const geojson = new GeoJSON();

export class GisMap {
	constructor(target, config, on) {
		this.on = on; // { pointer(coord), view({resolution, scale, zoom, rotation}), identify(hit|null), select(dataset, ids, additive), loading(name, state), error(msg), tool(t) }
		this.layers = {}; // dataset name -> { layer, source, dataset, seq }
		this.selected = null; // { dataset, ids: Set }
		this.active = null; // the active layer (select tool works on it)
		this.history = [];
		this.history_index = -1;
		this.navigating = false;
		this.tool = "identify";
		this.identify_seq = 0;

		this.basemap = null; // a GIS Map Service layer (set_basemap)
		this.map = new Map({
			target,
			layers: [],
			controls: default_controls({ attribution: true, rotate: true }).extend([new ScaleLine({ units: "metric" })]),
			view: new View({ center: fromLonLat(config.center), zoom: config.zoom, maxZoom: 24 }),
		});
		// QGIS map tools: Zoom In / Zoom Out (click or drag a box), Select (click or drag a box)
		this.zoom_box = new DragZoom({ condition: always, out: false });
		this.zoom_box_out = new DragZoom({ condition: always, out: true });
		this.select_box = new DragBox({ condition: always });
		this.select_box.on("boxend", (e) => this.select_in_extent(this.select_box.getGeometry().getExtent(), e.mapBrowserEvent.originalEvent.shiftKey));
		for (const i of [this.zoom_box, this.zoom_box_out, this.select_box]) {
			i.setActive(false);
			this.map.addInteraction(i);
		}

		this.map.on("pointermove", (e) => this.on.pointer(e.coordinate));
		this.map.on("singleclick", (e) => {
			if (this.edit && this.edit.tool !== "none") return this.edit.click(e);
			if (this.tool === "identify") this.identify(e.pixel, e.coordinate);
			else if (this.tool === "zoom_in") this.zoom_at(e.coordinate, 1);
			else if (this.tool === "zoom_out") this.zoom_at(e.coordinate, -1);
			else if (this.tool === "select") this.select_at(e.coordinate, e.pixel, e.originalEvent.shiftKey || e.originalEvent.ctrlKey);
		});
		this.map.on("moveend", () => this.after_move());
	}

	// ---- layers

	add_layer(dataset, { visible = true, opacity = 1, color, renderer } = {}) {
		if (this.layers[dataset.name]) return;
		const source = new VectorSource();
		const entry = {
			dataset,
			source,
			seq: 0,
			color: color || dataset.default_color || PALETTE[Object.keys(this.layers).length % PALETTE.length],
			renderer: dataset.published && renderer !== "browser" ? "qgis" : "browser",
		};
		if (dataset.published) {
			const wms = new ImageWMS({
				url: GATEWAY,
				params: { dataset: dataset.name, LAYERS: wms_name(dataset), FORMAT: "image/png", TRANSPARENT: true, VERSION: "1.3.0" },
				ratio: 1.25,
				serverType: "qgis",
			});
			let reported = false;
			wms.on("imageloaderror", () => {
				this.on.loading(dataset.name, "error");
				if (!reported) this.on.error(`${dataset.title}: QGIS Server could not draw this layer.`);
				reported = true;
			});
			wms.on("imageloadend", () => (reported = false));
			entry.image = new ImageLayer({
				source: wms,
				visible: visible && entry.renderer === "qgis",
				opacity,
				properties: { dataset_image: dataset.name },
			});
			this.map.addLayer(entry.image);
		}
		entry.layer = new VectorLayer({
			source,
			visible,
			opacity,
			style: (feature) => this.style(entry, feature),
			properties: { dataset: dataset.name },
		});
		this.layers[dataset.name] = entry;
		this.map.addLayer(entry.layer);
		this.load(dataset.name);
		return entry.color;
	}

	remove_layer(name) {
		const entry = this.layers[name];
		if (!entry) return;
		this.map.removeLayer(entry.layer);
		if (entry.image) this.map.removeLayer(entry.image);
		delete this.layers[name];
		if (this.selected && this.selected.dataset === name) this.select(null);
	}

	set_visible(name, visible) {
		const entry = this.layers[name];
		if (!entry) return;
		entry.layer.setVisible(visible);
		if (entry.image) entry.image.setVisible(visible && entry.renderer === "qgis");
		if (visible) this.load(name);
	}

	set_renderer(name, renderer) {
		const entry = this.layers[name];
		if (!entry || entry.kind === "service" || (renderer === "qgis" && !entry.image)) return;
		entry.renderer = renderer;
		if (entry.image) entry.image.setVisible(entry.layer.getVisible() && renderer === "qgis");
		entry.layer.changed();
	}

	set_opacity(name, opacity) {
		const entry = this.layers[name];
		if (!entry) return;
		entry.layer.setOpacity(opacity);
		if (entry.image) entry.image.setOpacity(opacity);
	}

	legend_url(name) {
		const entry = this.layers[name];
		if (!entry || !entry.image) return null;
		const params = new URLSearchParams({
			dataset: name,
			SERVICE: "WMS",
			VERSION: "1.3.0",
			REQUEST: "GetLegendGraphic",
			LAYERS: wms_name(entry.dataset),
			FORMAT: "image/png",
			LAYERTITLE: "FALSE",
			RULELABEL: "FALSE",
			SYMBOLWIDTH: "5",
			SYMBOLHEIGHT: "4",
			BOXSPACE: "0.5",
			TRANSPARENT: "TRUE",
		});
		return `${GATEWAY}?${params}`;
	}

	set_color(name, color) {
		const entry = this.layers[name];
		if (!entry || entry.kind === "service") return;
		entry.color = color;
		entry.layer.changed();
	}

	// names: top of the layer tree first, as in QGIS
	set_order(names) {
		// QGIS image under its own vector layer (which carries the selection highlight)
		names.forEach((name, i) => {
			const entry = this.layers[name];
			if (!entry) return;
			const base = (names.length - i) * 2 + 1;
			entry.layer.setZIndex(base + 1);
			if (entry.image) entry.image.setZIndex(base);
		});
	}

	// service: a GIS Map Service config from list_map_services (or null for no basemap)
	set_basemap(service, opacity) {
		if (this.basemap) this.map.removeLayer(this.basemap);
		this.basemap = service ? service_layer(service) : null;
		if (this.basemap) {
			this.basemap.setZIndex(0);
			this.basemap.setOpacity(opacity ?? service.opacity ?? 1);
			this.map.addLayer(this.basemap);
		}
	}

	set_basemap_opacity(opacity) {
		if (this.basemap) this.basemap.setOpacity(opacity);
	}

	// An overlay map service in the layer list (key "service:<name>")
	add_service_layer(key, service, { visible = true, opacity } = {}) {
		if (this.layers[key]) return;
		const layer = service_layer(service);
		layer.setVisible(visible);
		layer.setOpacity(opacity ?? service.opacity ?? 1);
		this.layers[key] = { kind: "service", layer, dataset: { name: key, title: service.title, spatial: false }, service };
		this.map.addLayer(layer);
	}

	features(name) {
		const entry = this.layers[name];
		return entry && entry.kind !== "service" ? entry.source.getFeatures() : [];
	}

	// ---- loading

	after_move() {
		const view = this.map.getView();
		const resolution = view.getResolution();
		const latitude = toLonLat(view.getCenter())[1];
		// OGC standard pixel (0.28 mm); Web Mercator stretches distances by 1/cos(latitude)
		const scale = (resolution * Math.cos((latitude * Math.PI) / 180)) / 0.00028;
		this.on.view({ resolution, scale, zoom: view.getZoom(), rotation: Math.round(((view.getRotation() * 180) / Math.PI) * 10) / 10 });

		if (!this.navigating) {
			const state = { center: view.getCenter(), resolution };
			this.history = this.history.slice(0, this.history_index + 1);
			this.history.push(state);
			if (this.history.length > 50) this.history.shift();
			this.history_index = this.history.length - 1;
		}
		this.navigating = false;

		clearTimeout(this.reload_timer);
		this.reload_timer = setTimeout(() => Object.keys(this.layers).forEach((n) => this.load(n)), 200);
	}

	reload() {
		for (const [name, entry] of Object.entries(this.layers)) {
			if (entry.kind === "service") continue;
			this.load(name);
			if (entry.image) entry.image.getSource().updateParams({ _t: Date.now() });
		}
	}

	async load(name) {
		const entry = this.layers[name];
		if (!entry || !entry.layer.getVisible() || !entry.dataset.spatial) return;
		const view = this.map.getView();
		const size = this.map.getSize();
		if (!size) return;
		const extent = buffer(view.calculateExtent(size), view.getResolution() * 50);
		const seq = ++entry.seq;
		this.on.loading(name, "loading");
		try {
			const collection = await call("get_layer_features", {
				dataset: name,
				bbox: extent,
				// a layer being edited loads full detail: editing a simplified geometry would lose vertices
				resolution: entry.session ? 0 : view.getResolution(),
			});
			if (seq !== entry.seq || this.layers[name] !== entry) return; // a newer view or layer removed
			let features = geojson.readFeatures(collection);
			if (entry.session) features = entry.session.keep_in(features); // unsaved edits win
			entry.source.clear(true);
			entry.source.addFeatures(features);
			if (entry.session) entry.session.restore_into_source();
			this.on.loading(name, collection.truncated ? "truncated" : "ready", entry.source.getFeatures().length);
		} catch (e) {
			if (seq !== entry.seq) return;
			this.on.loading(name, "error");
			this.on.error(`${entry.dataset.title}: ${e.message}`);
		}
	}

	// ---- styling

	style(entry, feature) {
		if (entry.session) return this.edit_style(entry, feature);
		const selected = this.is_selected(entry.dataset.name, feature.getId());
		if (entry.renderer === "qgis" && !selected) return null; // QGIS draws it
		const color = selected ? SELECTED : entry.color;
		const invalid = feature.get("validation_status") === "Invalid";
		const stroke = new Stroke({ color, width: selected ? 3 : 1.6, lineDash: invalid ? [6, 4] : undefined });
		const type = feature.getGeometry() && feature.getGeometry().getType();
		if (type === "Point" || type === "MultiPoint") {
			return new Style({
				image: new Circle({
					radius: selected ? 7 : 5,
					fill: new Fill({ color }),
					stroke: new Stroke({ color: "#ffffff", width: 1.5 }),
				}),
			});
		}
		if (type === "Polygon" || type === "MultiPolygon") {
			return new Style({ stroke, fill: new Fill({ color: with_alpha(color, selected ? 0.35 : 0.22) }) });
		}
		return new Style({ stroke: new Stroke({ color, width: selected ? 3.5 : 2, lineDash: invalid ? [6, 4] : undefined }) });
	}

	// Editing: selected features in the highlight colour with their vertices; new features dashed.
	edit_style(entry, feature) {
		const selected = entry.session.is_selected(feature);
		const base = this.style({ ...entry, session: null, renderer: "browser" }, feature);
		const styles = [base];
		if (selected) {
			const color = SELECTED;
			const type = feature.getGeometry().getType();
			if (/Point/.test(type)) {
				styles[0] = new Style({ image: new Circle({ radius: 7, fill: new Fill({ color }), stroke: new Stroke({ color: "#ffffff", width: 2 }) }) });
			} else {
				styles[0] = new Style({
					stroke: new Stroke({ color, width: 3, lineDash: feature.getId() < 0 ? [8, 5] : undefined }),
					fill: /Polygon/.test(type) ? new Fill({ color: with_alpha(color, 0.25) }) : undefined,
				});
				styles.push(
					new Style({
						image: new Circle({ radius: 4, fill: new Fill({ color: "#ffffff" }), stroke: new Stroke({ color, width: 2 }) }),
						geometry: (f) => new MultiPoint(vertices(f.getGeometry())),
					})
				);
			}
		} else if (feature.getId() < 0) {
			styles[0] = this.style({ ...entry, session: null, renderer: "browser", color: "#27ae60" }, feature);
		}
		return styles;
	}

	start_edit(name, schema, on) {
		this.stop_edit();
		this.edit = new EditSession(this, name, schema, on);
		this.load(name); // full detail
		return this.edit;
	}

	stop_edit() {
		if (!this.edit) return;
		const name = this.edit.name;
		this.edit.destroy();
		this.edit = null;
		this.load(name);
	}

	// ---- selection and identify

	// Top visible layer first: QGIS-rendered layers are asked through GetFeatureInfo, browser-drawn
	// ones are hit-tested here. hit = { dataset, id, feature (OpenLayers, if loaded), properties }
	async identify(pixel, coordinate) {
		const entries = Object.values(this.layers)
			.filter((e) => e.kind !== "service" && e.layer.getVisible() && e.dataset.spatial)
			.sort((a, b) => b.layer.getZIndex() - a.layer.getZIndex());
		const click = ++this.identify_seq;
		for (const entry of entries) {
			let hit = null;
			if (entry.renderer === "qgis") {
				hit = await this.feature_info(entry, coordinate);
			} else {
				this.map.forEachFeatureAtPixel(
					pixel,
					(feature) => {
						hit = { dataset: entry.dataset.name, id: feature.getId(), feature, properties: null };
						return true;
					},
					{ hitTolerance: 4, layerFilter: (layer) => layer === entry.layer }
				);
			}
			if (click !== this.identify_seq) return; // a newer click
			if (hit) {
				this.select({ dataset: hit.dataset, id: hit.id });
				this.on.identify(hit);
				return;
			}
		}
		this.select(null);
		this.on.identify(null);
	}

	async feature_info(entry, coordinate) {
		const view = this.map.getView();
		const url = entry.image.getSource().getFeatureInfoUrl(coordinate, view.getResolution(), "EPSG:3857", {
			INFO_FORMAT: "application/json",
			QUERY_LAYERS: wms_name(entry.dataset),
			FEATURE_COUNT: 1,
			FI_POINT_TOLERANCE: 10,
			FI_LINE_TOLERANCE: 8,
			FI_POLYGON_TOLERANCE: 4,
			WITH_GEOMETRY: "FALSE",
		});
		try {
			const response = await fetch(url, { credentials: "same-origin" });
			if (!response.ok) throw new Error(`HTTP ${response.status}`);
			const feature = ((await response.json()).features || [])[0];
			if (!feature) return null;
			// QGIS feature ids are "<layer>.<fid>"; the fid is the GIS Feature name
			const id = Number(String(feature.id).split(".").pop());
			return { dataset: entry.dataset.name, id, feature: entry.source.getFeatureById(id), properties: feature.properties || {} };
		} catch (e) {
			this.on.error(`${entry.dataset.title}: Identify failed (${e.message}).`);
			return null;
		}
	}

	// selection: { dataset, id } or { dataset, ids } or null
	select(selection) {
		const previous = this.selected;
		this.selected = selection ? { dataset: selection.dataset, ids: new Set(selection.ids || (selection.id != null ? [selection.id] : [])) } : null;
		for (const s of [previous, this.selected]) {
			if (s && this.layers[s.dataset]) this.layers[s.dataset].layer.changed();
		}
	}

	is_selected(dataset, id) {
		return !!(this.selected && this.selected.dataset === dataset && this.selected.ids.has(id));
	}

	// Select tool: the active layer's feature under the click (also on QGIS-drawn layers, whose
	// browser features are invisible but loaded)
	select_at(coordinate, pixel, additive) {
		const entry = this.layers[this.active];
		if (!entry || entry.kind === "service") return;
		let hit = null;
		this.map.forEachFeatureAtPixel(pixel, (f) => ((hit = f), true), { hitTolerance: 4, layerFilter: (l) => l === entry.layer });
		if (!hit) {
			const tolerance = this.map.getView().getResolution() * 6;
			hit = entry.source.getFeaturesAtCoordinate(coordinate)[0] || null;
			const near = hit ? null : entry.source.getClosestFeatureToCoordinate(coordinate);
			if (near) {
				const [x, y] = near.getGeometry().getClosestPoint(coordinate);
				if (Math.hypot(x - coordinate[0], y - coordinate[1]) <= tolerance) hit = near;
			}
		}
		this.on.select(this.active, hit ? [hit.getId()] : [], additive);
	}

	select_in_extent(extent, additive) {
		const entry = this.layers[this.active];
		if (!entry || entry.kind === "service") return;
		const ids = [];
		entry.source.forEachFeatureIntersectingExtent(extent, (f) => {
			ids.push(f.getId());
		});
		this.on.select(this.active, ids, additive);
	}

	find(dataset, id) {
		const entry = this.layers[dataset];
		return entry && entry.kind !== "service" ? entry.source.getFeatureById(id) : null;
	}

	// ---- navigation

	set_tool(tool) {
		this.tool = tool;
		this.zoom_box.setActive(tool === "zoom_in" || tool === "zoom_box");
		this.zoom_box_out.setActive(tool === "zoom_out");
		this.select_box.setActive(tool === "select");
		this.on.tool && this.on.tool(tool);
	}

	zoom_at(coordinate, delta) {
		const view = this.map.getView();
		view.animate({ center: coordinate, zoom: view.getZoom() + delta, duration: 200 });
	}

	set_scale(scale) {
		const view = this.map.getView();
		const latitude = toLonLat(view.getCenter())[1];
		view.setResolution((scale * 0.00028) / Math.cos((latitude * Math.PI) / 180));
	}

	set_rotation(degrees) {
		this.map.getView().animate({ rotation: (degrees * Math.PI) / 180, duration: 200 });
	}

	zoom_by(delta) {
		const view = this.map.getView();
		view.animate({ zoom: view.getZoom() + delta, duration: 200 });
	}

	fit(extent, max_zoom = 18) {
		if (!extent || isEmpty(extent)) return false;
		this.map.getView().fit(extent, { padding: [40, 40, 40, 40], maxZoom: max_zoom, duration: 300 });
		return true;
	}

	zoom_to_layer(name) {
		const entry = this.layers[name];
		return entry ? this.fit(entry.dataset.extent_3857) : false;
	}

	zoom_to_feature(dataset, id) {
		const feature = this.find(dataset, id);
		if (!feature || !feature.getGeometry()) return false;
		return this.fit(feature.getGeometry().getExtent(), 19);
	}

	zoom_full(fallback) {
		const extent = createEmpty();
		for (const entry of Object.values(this.layers)) {
			if (entry.layer.getVisible() && entry.dataset.extent_3857) extend(extent, entry.dataset.extent_3857);
		}
		if (!this.fit(extent)) {
			this.map.getView().animate({ center: fromLonLat(fallback.center), zoom: fallback.zoom, duration: 300 });
		}
	}

	can_go(step) {
		const i = this.history_index + step;
		return i >= 0 && i < this.history.length;
	}

	go(step) {
		if (!this.can_go(step)) return;
		this.history_index += step;
		const state = this.history[this.history_index];
		this.navigating = true;
		this.map.getView().animate({ center: state.center, resolution: state.resolution, duration: 200 });
	}

	go_to(coordinate_3857, resolution) {
		this.map.getView().animate({ center: coordinate_3857, resolution: resolution || this.map.getView().getResolution(), duration: 300 });
	}

	update_size() {
		this.map.updateSize();
	}
}

function service_layer(service) {
	const attributions = service.attribution ? [service.attribution] : undefined;
	if (service.kind === "wms") {
		return new ImageLayer({
			source: new ImageWMS({ url: service.url, params: service.params, ratio: 1.25, attributions, crossOrigin: null }),
		});
	}
	return new TileLayer({
		source: new XYZ({
			url: service.url,
			attributions,
			minZoom: service.min_zoom || 0,
			maxZoom: service.max_zoom || 19, // deeper zooms enlarge the last tiles
		}),
	});
}

function vertices(geometry) {
	const flat = geometry.getFlatCoordinates();
	const stride = geometry.getStride();
	const points = [];
	for (let i = 0; i < flat.length; i += stride) points.push([flat[i], flat[i + 1]]);
	return points;
}

function wms_name(dataset) {
	return dataset.qgis_layer_name || dataset.name;
}

function with_alpha(hex, alpha) {
	const m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
	if (!m) return hex;
	const n = parseInt(m[1], 16);
	return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

export { boundingExtent, getCenter, PALETTE };
