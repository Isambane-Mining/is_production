// Calls to the CAD Workstation's whitelisted methods, with the page's CSRF token.

const BASE = "is_production.cad_engineering.services.cad_workstation_service.";

export async function call(method, args = {}) {
	const response = await fetch(`/api/method/${method.includes(".") ? method : BASE + method}`, {
		method: "POST",
		headers: {
			"Content-Type": "application/json",
			Accept: "application/json",
			"X-Frappe-CSRF-Token": (window.FRAPPE_CAD || {}).csrfToken || "",
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
		const error = new Error(server_message(data) || `${response.status} ${response.statusText}`);
		error.exc_type = data.exc_type;
		throw error;
	}
	return data.message;
}

// Display mesh of a solid: binary (see cad_worker/geometry_worker.py tessellate). null while it is missing.
export async function get_mesh(model, geometry_ref) {
	const query = new URLSearchParams({ model, geometry_ref });
	const response = await fetch(`/api/method/${BASE}get_mesh?${query}`, { credentials: "same-origin" });
	if (response.status === 404) return null;
	if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
	return parse_mesh(await response.arrayBuffer());
}

// "ISPCADM1", uint32 vertices, uint32 indices, uint32 edge vertices, float64 origin[3], then
// float32 positions, uint32 indices, float32 edge positions (all relative to origin).
export function parse_mesh(buffer) {
	const view = new DataView(buffer);
	const magic = new TextDecoder().decode(new Uint8Array(buffer, 0, 8));
	if (magic !== "ISPCADM1") throw new Error(__("Not a CAD display mesh."));
	const vertices = view.getUint32(8, true);
	const indices = view.getUint32(12, true);
	const edges = view.getUint32(16, true);
	const origin = [view.getFloat64(20, true), view.getFloat64(28, true), view.getFloat64(36, true)];
	let offset = 44;
	const positions = new Float32Array(buffer.slice(offset, offset + vertices * 12));
	offset += vertices * 12;
	const index = new Uint32Array(buffer.slice(offset, offset + indices * 4));
	offset += indices * 4;
	const edge_positions = new Float32Array(buffer.slice(offset, offset + edges * 12));
	return { origin, positions, index, edges: edge_positions };
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

// Wait for a CAD Processing Job; on_progress gets each poll's payload.
export async function wait_for_job(name, on_progress) {
	for (;;) {
		const job = await call("get_job", { name });
		if (on_progress) on_progress(job);
		if (["Completed", "Failed", "Cancelled"].includes(job.status)) return job;
		await new Promise((resolve) => setTimeout(resolve, 1000));
	}
}
