// The portal page has no desk, so no Frappe translation function: a minimal one for {0}-style args.
// Imported first by the bundle entry, because modules (tools.js) translate labels when they load.
if (typeof window.__ !== "function") {
	window.__ = (text, args) => String(text).replace(/\{(\d+)\}/g, (m, i) => (args && args[i] !== undefined ? args[i] : m));
}
