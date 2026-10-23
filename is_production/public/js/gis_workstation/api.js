// Calls to the GIS Workstation's whitelisted methods, with the page's CSRF token.

const BASE = "is_production.geo_planning.services.gis_workstation_service.";

export async function call(method, args = {}) {
	const response = await fetch(`/api/method/${method.includes(".") ? method : BASE + method}`, {
		method: "POST",
		headers: {
			"Content-Type": "application/json",
			Accept: "application/json",
			"X-Frappe-CSRF-Token": (window.FRAPPE_GIS || {}).csrfToken || "",
		},
		body: JSON.stringify(args),
	});
	let data = {};
	try {
		data = await response.json();
	} catch (e) {
		/* not JSON */
	}
	if (!response.ok || data.exc_type) {
		throw new Error(server_message(data) || `${response.status} ${response.statusText}`);
	}
	return data.message;
}

function server_message(data) {
	try {
		const messages = JSON.parse(data._server_messages || "[]").map((m) => JSON.parse(m).message);
		if (messages.length) return strip_html(messages.join(" "));
	} catch (e) {
		/* fall through */
	}
	return data.exception ? String(data.exception).split(":").slice(1).join(":").trim() : "";
}

export function strip_html(text) {
	const div = document.createElement("div");
	div.innerHTML = text;
	return div.textContent || "";
}
