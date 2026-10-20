// Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
// For license information, please see license.txt

const MINING_SIM_API = "is_production.geo_planning.services.mining_simulation_service.";
const MINING_SIM_DESK_JS = "/assets/is_production/mining_simulation/desk_import.js";

const PRODUCTION_EQUIPMENT = ["ADT", "Rigid", "Excavator", "Dozer"];

frappe.ui.form.on("Mining Simulation Project", {
	setup(frm) {
		frm.set_query("asset_category", "fleet", () => ({ filters: { name: ["in", PRODUCTION_EQUIPMENT] } }));
		frm.set_query("item", "fleet", (doc, cdt, cdn) => {
			const row = locals[cdt][cdn];
			return {
				filters: {
					asset_category: row.asset_category || ["in", PRODUCTION_EQUIPMENT],
					is_fixed_asset: 1,
					disabled: 0,
				},
			};
		});
	},

	onload_post_render(frm) {
		mining_sim_team_options(frm);
		mining_sim_equipment_summary(frm);
	},

	team_count(frm) {
		mining_sim_team_notice(frm);
	},

	refresh(frm) {
		mining_sim_team_options(frm);
		mining_sim_equipment_summary(frm);
		mining_sim_team_notice(frm);
		if (frm.is_new()) return;

		frm
			.add_custom_button(__("Open in Simulator"), () => {
				window.open("/portal_mining_simulation?project=" + encodeURIComponent(frm.doc.name), "_blank");
			})
			.addClass("btn-primary");

		frm.add_custom_button(
			__("Export Simulation File"),
			() => {
				window.open(
					"/api/method/" + MINING_SIM_API + "export_simulation_file?name=" + encodeURIComponent(frm.doc.name),
				);
			},
			__("Simulation File"),
		);

		if (frm.perm[0] && frm.perm[0].write) {
			frm.add_custom_button(
				__("Replace from Simulation File"),
				() =>
					frappe.require(MINING_SIM_DESK_JS, () =>
						is_production.mining_simulation.pick_file((file_url, info) => {
							frappe.confirm(
								__(
									"Replace everything in {0} with the project in this file ({1}, {2} blocks)? This cannot be undone.",
									[frm.doc.project_name.bold(), info.project_name, info.blocks],
								),
								() =>
									is_production.mining_simulation
										.run_import(file_url, "replace", frm.doc.name)
										.then(() => frm.reload_doc()),
							);
						}),
					),
				__("Simulation File"),
			);
		}
	},
});

frappe.ui.form.on("Mining Simulation Team", {
	team_name: mining_sim_teams_changed,
	teams_remove: mining_sim_teams_changed,
	teams_add: mining_sim_teams_changed,
});

frappe.ui.form.on("Mining Simulation Equipment", {
	asset_category(frm, cdt, cdn) {
		frappe.model.set_value(cdt, cdn, "item", null);
	},
	item: mining_sim_equipment_summary,
	qty: mining_sim_equipment_summary,
	team: mining_sim_equipment_summary,
	fleet_remove: mining_sim_equipment_summary,
});

function mining_sim_teams_changed(frm) {
	mining_sim_team_options(frm);
	mining_sim_team_notice(frm);
	mining_sim_equipment_summary(frm);
}

// the Team column offers the teams of this project
function mining_sim_team_options(frm) {
	const names = (frm.doc.teams || []).map((t) => t.team_name).filter(Boolean);
	frm.fields_dict.fleet.grid.update_docfield_property("team", "options", names);
}

// the simulator's team count (may be a fraction) and the teams equipment is allocated to
function mining_sim_team_notice(frm) {
	const count = flt(frm.doc.team_count),
		rows = (frm.doc.teams || []).length;
	frm.dashboard.clear_headline();
	if (!frm.is_new() && rows && Math.ceil(count) !== rows) {
		frm.dashboard.set_headline_alert(
			__("The simulator works with {0} teams; {1} teams are defined for equipment.", [count, rows]),
			"orange",
		);
	}
	frm.remove_custom_button(__("Create Teams from Count"));
	if (!frm.is_new() && count > rows && frm.perm[0] && frm.perm[0].write) {
		frm.add_custom_button(__("Create Teams from Count"), () => {
			const have = new Set((frm.doc.teams || []).map((t) => t.team_name));
			for (let n = 1; (frm.doc.teams || []).length < Math.ceil(count); n++) {
				const name = __("Team {0}", [n]);
				if (!have.has(name)) frm.add_child("teams", { team_name: name });
			}
			frm.refresh_field("teams");
			mining_sim_teams_changed(frm);
			frm.dirty();
		});
	}
}

// what each project needs: units per Item, by team
function mining_sim_equipment_summary(frm) {
	const wrapper = frm.fields_dict.equipment_summary && frm.fields_dict.equipment_summary.$wrapper;
	if (!wrapper) return;
	const rows = (frm.doc.fleet || []).filter((r) => r.item);
	if (!rows.length) {
		wrapper.html("");
		return;
	}
	const teams = [...new Set(rows.map((r) => r.team || __("Unallocated")))];
	const by = {};
	for (const r of rows) {
		const key = r.asset_category + "\u0000" + r.item;
		by[key] = by[key] || {
			category: r.asset_category,
			item: r.item,
			name: r.equipment_name || r.item,
			teams: {},
			total: 0,
		};
		const t = r.team || __("Unallocated");
		by[key].teams[t] = (by[key].teams[t] || 0) + flt(r.qty);
		by[key].total += flt(r.qty);
	}
	const esc = frappe.utils.escape_html;
	const lines = Object.values(by).sort((a, b) => (a.category + a.item).localeCompare(b.category + b.item));
	wrapper.html(`
		<div class="form-group">
			<label class="control-label">${__("Equipment Required")}</label>
			<table class="table table-bordered table-sm" style="font-size: var(--text-sm)">
				<thead><tr><th>${__("Asset Category")}</th><th>${__("Item")}</th>
					${teams.map((t) => `<th class="text-right">${esc(t)}</th>`).join("")}
					<th class="text-right">${__("Total")}</th></tr></thead>
				<tbody>${lines
					.map(
						(l) => `<tr><td>${esc(l.category || "")}</td><td>${esc(l.name)}</td>
						${teams.map((t) => `<td class="text-right">${l.teams[t] || ""}</td>`).join("")}
						<td class="text-right"><b>${l.total}</b></td></tr>`,
					)
					.join("")}</tbody>
			</table>
		</div>`);
}
