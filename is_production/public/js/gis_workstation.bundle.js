// GIS Workstation (/portal_gis): entry point of the full-screen portal page.
// Page config comes from portal_gis.py as window.FRAPPE_GIS.

import { createApp } from "vue";
import App from "./gis_workstation/App.vue";

// The portal page has no desk, so no Frappe translation function: a minimal one for {0}-style args.
if (typeof window.__ !== "function") {
	window.__ = (text, args) => String(text).replace(/\{(\d+)\}/g, (m, i) => (args && args[i] !== undefined ? args[i] : m));
}

const mount = document.getElementById("gis-app");
if (mount) {
	createApp(App, { config: window.FRAPPE_GIS.config }).mount(mount);
}
