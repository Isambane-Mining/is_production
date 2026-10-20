// The OpenLayers map of the GIS Workstation.
//
// Each dataset layer is a vector layer whose features are fetched for the current view only
// (get_layer_features: native spatial index, transformed to EPSG:3857 and simplified to the pixel
// size on the server), so a large dataset is never loaded into the browser in full.

import Map from "ol/Map.js";
import View from "ol/View.js";
import TileLayer from "ol/layer/Tile.js";
import VectorLayer from "ol/layer/Vector.js";
import OSM from "ol/source/OSM.js";
import VectorSource from "ol/source/Vector.js";
import GeoJSON from "ol/format/GeoJSON.js";
import { Circle, Fill, Stroke, Style } from "ol/style.js";
import { fromLonLat, toLonLat } from "ol/proj.js";
import { boundingExtent, buffer, createEmpty, extend, getCenter, isEmpty } from "ol/extent.js";
import { defaults as default_controls, ScaleLine } from "ol/control.js";
import { DragZoom } from "ol/interaction.js";
import { always } from "ol/events/condition.js";
import { call } from "./api.js";

const PALETTE = ["#2f80ed", "#eb5757", "#27ae60", "#f2994a", "#9b51e0", "#00a3a3", "#c0392b", "#7f8c8d"];
const SELECTED = "#ffb000";
const geojson = new GeoJSON();

export class GisMap {
	constructor(target, config, on) {
		this.on = on; // { pointer(coord), view({resolution, scale, zoom}), identify(hit|null), loading(name, state), error(msg) }
		this.layers = {}; // dataset name -> { layer, source, dataset, seq }
		this.selected = null; // { dataset, id }
		this.history = [];
		this.history_index = -1;
		this.navigating = false;
		this.tool = "identify";

		this.basemap = new TileLayer({ source: new OSM(), visible: config.basemap === "OpenStreetMap", zIndex: 0 });
		this.map = new Map({
			target,
			layers: [this.basemap],
			controls: default_controls({ attribution: true, rotate: false }).extend([new ScaleLine({ units: "metric" })]),
			view: new View({ center: fromLonLat(config.center), zoom: config.zoom, maxZoom: 24 }),
		});
		this.zoom_box = new DragZoom({ condition: always, out: false });
		this.zoom_box.setActive(false);
		this.map.addInteraction(this.zoom_box);
		this.zoom_box.on("boxend", () => this.set_tool("identify"));

		this.map.on("pointermove", (e) => this.on.pointer(e.coordinate));
		this.map.on("singleclick", (e) => this.tool === "identify" && this.identify(e.pixel));
		this.map.on("moveend", () => this.after_move());
	}

	// ---- layers

	add_layer(dataset, { visible = true, opacity = 1, color } = {}) {
		if (this.layers[dataset.name]) return;
		const source = new VectorSource();
		const entry = {
			dataset,
			source,
			seq: 0,
			color: color || dataset.default_color || PALETTE[Object.keys(this.layers).length % PALETTE.length],
		};
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
		delete this.layers[name];
		if (this.selected && this.selected.dataset === name) this.select(null);
	}

	set_visible(name, visible) {
		const entry = this.layers[name];
		if (!entry) return;
		entry.layer.setVisible(visible);
		if (visible) this.load(name);
	}

	set_opacity(name, opacity) {
		this.layers[name] && this.layers[name].layer.setOpacity(opacity);
	}

	set_color(name, color) {
		const entry = this.layers[name];
		if (!entry) return;
		entry.color = color;
		entry.layer.changed();
	}

	// names: top of the layer tree first, as in QGIS
	set_order(names) {
		names.forEach((name, i) => this.layers[name] && this.layers[name].layer.setZIndex(names.length - i + 1));
	}

	set_basemap(visible) {
		this.basemap.setVisible(visible);
	}

	features(name) {
		const entry = this.layers[name];
		return entry ? entry.source.getFeatures() : [];
	}

	// ---- loading

	after_move() {
		const view = this.map.getView();
		const resolution = view.getResolution();
		const latitude = toLonLat(view.getCenter())[1];
		// OGC standard pixel (0.28 mm); Web Mercator stretches distances by 1/cos(latitude)
		const scale = (resolution * Math.cos((latitude * Math.PI) / 180)) / 0.00028;
		this.on.view({ resolution, scale, zoom: view.getZoom() });

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
		Object.keys(this.layers).forEach((name) => this.load(name));
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
				resolution: view.getResolution(),
			});
			if (seq !== entry.seq || this.layers[name] !== entry) return; // a newer view or layer removed
			entry.source.clear(true);
			entry.source.addFeatures(geojson.readFeatures(collection));
			this.on.loading(name, collection.truncated ? "truncated" : "ready", entry.source.getFeatures().length);
		} catch (e) {
			if (seq !== entry.seq) return;
			this.on.loading(name, "error");
			this.on.error(`${entry.dataset.title}: ${e.message}`);
		}
	}

	// ---- styling

	style(entry, feature) {
		const selected = this.selected && this.selected.dataset === entry.dataset.name && this.selected.id === feature.getId();
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

	// ---- selection and identify

	identify(pixel) {
		let hit = null;
		this.map.forEachFeatureAtPixel(
			pixel,
			(feature, layer) => {
				hit = { dataset: layer.get("dataset"), feature };
				return true;
			},
			{ hitTolerance: 4, layerFilter: (layer) => !!layer.get("dataset") }
		);
		this.select(hit ? { dataset: hit.dataset, id: hit.feature.getId() } : null);
		this.on.identify(hit);
	}

	select(selection) {
		const previous = this.selected;
		this.selected = selection;
		for (const s of [previous, selection]) {
			if (s && this.layers[s.dataset]) this.layers[s.dataset].layer.changed();
		}
	}

	find(dataset, id) {
		const entry = this.layers[dataset];
		return entry ? entry.source.getFeatureById(id) : null;
	}

	// ---- navigation

	set_tool(tool) {
		this.tool = tool;
		this.zoom_box.setActive(tool === "zoom_box");
		this.on.tool && this.on.tool(tool);
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

function with_alpha(hex, alpha) {
	const m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
	if (!m) return hex;
	const n = parseInt(m[1], 16);
	return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

export { boundingExtent, getCenter, PALETTE };
