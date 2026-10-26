frappe.listview_settings["CAD Model"] = {
	add_fields: ["approval_status", "model_status"],
	get_indicator(doc) {
		if (doc.model_status === "Archived") return [__("Archived"), "gray", "model_status,=,Archived"];
		const colors = { Approved: "green", "In Review": "blue", Rejected: "red", "Not Submitted": "orange" };
		return [__(doc.approval_status), colors[doc.approval_status] || "gray", `approval_status,=,${doc.approval_status}`];
	},
	onload(listview) {
		listview.page.add_inner_button(__("Open CAD Workstation"), () => window.open("/portal_cad", "_blank"));
	},
};
