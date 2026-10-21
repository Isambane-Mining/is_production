// CAD Engineering Workstation (/portal_cad): entry point of the full-screen portal page.
// Page config comes from portal_cad.py as window.FRAPPE_CAD.

import "./cad_workstation/i18n";
import { createApp } from "vue";
import App from "./cad_workstation/App.vue";

const mount = document.getElementById("cad-app");
if (mount) {
	const app = createApp(App, { config: window.FRAPPE_CAD.config });
	app.config.globalProperties.__ = window.__;
	app.mount(mount);
}
