// Phase 5 commands (Mining → Planning): publish an approved design to Geo Planning, the CAD sequence as a schedule
// selection, the planning view (blocks coloured by status / schedule period / % mined), survey reconciliation and
// revision comparison. The records live in Geo Planning (Mining Block, Schedule Selection) and in CAD Design
// Publication / CAD Survey Reconciliation; the workstation only starts the work and shows the results.

import { call } from "./api";
import { ramp } from "./surfaces";
import { entity_label } from "./surface_commands";

const STATUS_COLOURS = { Draft: "#8a8f98", Available: "#4aa8d8", Planned: "#a782e0", Scheduled: "#e5a53b", Mining: "#e5534b", Complete: "#5fbf6a", Excluded: "#555b63" };

function hex(c) {
	return "#" + c.map((v) => Math.round(Math.min(1, Math.max(0, v)) * 255).toString(16).padStart(2, "0")).join("");
}

export class PlanningCommands {
	// ctx: as MiningCommands, plus set_compare_overlay(content|null)
	constructor(ctx) {
		this.ctx = ctx;
		this.colours = null; // the planning view in force: {mode, legend}
	}

	get session() {
		return this.ctx.session;
	}

	async run(command, args) {
		const handler = {
			plan_publish: () => this.publish(),
			plan_selection: () => this.selection(),
			plan_view: () => this.planning_view(),
			plan_view_off: () => this.clear_view(),
			plan_reconcile: () => this.reconcile(),
			plan_compare: () => this.compare(args),
			plan_compare_off: () => this.ctx.set_compare_overlay(null),
		}[command];
		if (!handler) return false;
		await handler();
		return true;
	}

	model() {
		return this.ctx.model().name;
	}

	// fresh from the server: an approval a moment ago must count
	revisions() {
		return call("revision_history", { model: this.model() });
	}

	// -------------------------------------------------------------- publish

	async publish() {
		const approved = (await this.revisions()).filter((r) => r.revision_status === "Approved");
		if (!approved.length) throw new Error(__("Only an Approved revision can be published: submit the design for review and have it approved first."));
		const preview = await call("publish_preview", { model: this.model(), revision: approved[0].name });
		const v = preview.validation;
		const fields = [
			{ key: "revision", label: __("Approved revision"), type: "select", options: approved.map((r) => ({ value: r.name, label: `${r.revision_label} · ${r.name}` })), default: approved[0].name },
			{ key: "geo_project", label: __("Geo Project"), type: "select", options: preview.projects.map((p) => ({ value: p.name, label: p.project_name || p.name })), default: preview.geo_project || (preview.projects[0] || {}).name },
		];
		preview.materials.forEach((m, i) => {
			fields.push({ key: `density_${i}`, label: __("{0}: density (t/m³)", [m.material]), type: "number", default: /coal/i.test(m.material) ? 1.45 : 2.4, help: __("{0} blocks, {1} m³", [m.blocks, Math.round(m.volume).toLocaleString()]) });
			fields.push({ key: `status_${i}`, label: __("{0}: material", [m.material]), type: "select", options: ["Mineable", "Waste"], default: /coal|ore/i.test(m.material) ? "Mineable" : "Waste" });
		});
		fields.push({ key: "make_selection", label: __("Schedule"), type: "check", check_label: __("Also make a schedule selection in the CAD sequence"), default: true });
		const values = await this.ctx.ask({
			title: __("Publish Design to Geo Planning"),
			intro: v
				? __("{0} blocks of {1}, validated against {2}: {3} ({4}). Blocks not yet scheduled follow the design; scheduled, mining or complete blocks are kept and flagged when the design changed them.", [preview.blocks, preview.revision_label, v.standard || "—", __(v.status), v.name])
				: __("{0} has no design validation: validate the design before submitting it; it cannot be published yet.", [preview.revision_label]),
			fields,
			submit_label: __("Publish"),
			check: () => (v && v.status !== "Fail" ? null : __("The design needs a passing validation (Mining → Validate Design) in the approved revision.")),
		});
		if (!values) return;
		const materials = {};
		preview.materials.forEach((m, i) => (materials[m.material] = { density: values[`density_${i}`] || 0, status: values[`status_${i}`] }));
		const pub = await call("publish_design", { model: this.model(), revision: values.revision, geo_project: values.geo_project, materials, make_selection: values.make_selection ? 1 : 0 });
		this.ctx.show_result({ kind: "publication", title: __("Published {0}", [pub.name]), result: pub });
		this.ctx.message(__("{0}: {1} blocks published to {2} ({3} new, {4} updated, {5} flagged).", [pub.name, pub.blocks_published, pub.geo_pit_layout, pub.blocks_created, pub.blocks_updated, pub.blocks_flagged]), "result");
	}

	async selection() {
		const ids = this.ctx.selection().filter((id) => {
			const e = this.session.entities.get(id);
			return e && e.metadata && e.metadata.mining_block;
		});
		const r = await call("selection_from_sequence", { model: this.model(), block_entity_ids: ids.length ? ids : null });
		this.ctx.message(__("Schedule selection {0}: {1} blocks in CAD sequence (Desk → Mining Schedule Selection).", [r.selection, r.blocks]), "result");
	}

	// -------------------------------------------------------------- planning view

	async planning_view(preset = null, quiet = false) {
		const data = await call("planning_overlay", { model: this.model() });
		if (!Object.keys(data.blocks).length) throw new Error(__("No block of this model is published to Geo Planning yet (Mining → Publish Design)."));
		const values =
			preset ||
			(await this.ctx.ask({
				title: __("Planning View"),
				intro: __("Colours the design's blocks by their Geo Planning record (view only: nothing is saved)."),
				fields: [
					{
						key: "mode",
						label: __("Colour by"),
						type: "select",
						options: [
							{ value: "status", label: __("Block status") },
							{ value: "period", label: __("Schedule period") },
							{ value: "mined", label: __("% mined (last survey)") },
							{ value: "sequence", label: __("CAD sequence") },
						],
						default: "status",
					},
					{ key: "scenario", label: __("Schedule scenario"), type: "select", options: data.scenarios.map((s) => ({ value: s.name, label: `${s.scenario_name} · ${__(s.schedule_status || "")}` })), default: (data.scenarios[0] || {}).name, show_if: (v) => v.mode === "period" },
				],
				submit_label: __("Show"),
				check: (v) => (v.mode === "period" && !v.scenario ? __("No schedule scenario uses this design's layout yet.") : null),
			}));
		if (!values) return;
		const full = values.mode === "period" ? await call("planning_overlay", { model: this.model(), scenario: values.scenario }) : data;
		const blocks = full.blocks;
		const colours = new Map();
		const legend = new Map();
		const rows = Object.entries(blocks);
		const max_seq = Math.max(1, ...rows.map(([, b]) => b.cad_sequence || 0));
		const periods = [...new Set(rows.map(([, b]) => b.period_no).filter((p) => p !== null && p !== undefined))].sort((a, b) => a - b);
		for (const [id, b] of rows) {
			let c, key;
			if (values.mode === "status") {
				key = b.block_status || "Draft";
				c = STATUS_COLOURS[key] || "#8a8f98";
				if (b.planning_status === "Review") (key = __("Review")), (c = "#ff3df2");
			} else if (values.mode === "period") {
				if (b.period_no === null || b.period_no === undefined) (key = __("Not scheduled")), (c = "#555b63");
				else (key = `${__("Period")} ${b.period_no}`), (c = hex(ramp(periods.length > 1 ? periods.indexOf(b.period_no) / (periods.length - 1) : 0)));
			} else if (values.mode === "mined") {
				const pct = b.percent_mined || 0;
				key = pct >= 98 ? "100 %" : pct > 2 ? `${Math.floor(pct / 25) * 25}–${Math.floor(pct / 25) * 25 + 25} %` : "0 %";
				c = hex(ramp(pct / 100));
			} else {
				key = `${__("Sequence")} 1 … ${max_seq}`;
				c = hex(ramp((b.cad_sequence || 0) / max_seq));
			}
			colours.set(id, c);
			const order = values.mode === "period" ? (b.period_no ?? 1e9) : values.mode === "mined" ? b.percent_mined || 0 : values.mode === "status" ? Object.keys(STATUS_COLOURS).indexOf(b.block_status) : 0;
			const g = legend.get(key) || { label: key, colour: c, count: 0, volume: 0, order };
			g.order = Math.min(g.order, order);
			g.count++;
			g.volume += b.design_volume || 0;
			legend.set(key, g);
		}
		this.ctx.viewport.colour_override = colours;
		this.ctx.viewport.sync(this.session);
		this.colours = { mode: values.mode };
		if (!quiet) this.ctx.show_result({ kind: "planning_view", title: __("Planning View"), result: { mode: values.mode, scenario: values.scenario, legend: [...legend.values()].sort((a, b) => a.order - b.order), publication: data.publication, blocks: rows.length } });
	}

	clear_view() {
		this.ctx.viewport.colour_override = null;
		this.ctx.viewport.sync(this.session);
		this.colours = null;
	}

	// -------------------------------------------------------------- survey reconciliation

	async reconcile() {
		const data = await call("planning_overlay", { model: this.model() });
		if (!data.publication) throw new Error(__("Publish the design to Geo Planning first (Mining → Publish Design)."));
		const surfaces = [...this.session.entities.values()].filter((e) => e.entity_type === "Surface" && e.geometry_ref);
		const survey = [...surfaces].reverse().find((e) => /as-built|survey/i.test(`${e.geometry.surface_type} ${e.geometry.name}`) && !/existing/i.test(e.geometry.name)) || surfaces[surfaces.length - 1];
		const values = await this.ctx.ask({
			title: __("Reconcile Survey"),
			intro: __("Measures a survey surface against the published design ({0}), block by block within each bench: planned, mined, remaining in the design, mined beyond the design, and the schedule to the survey date.", [data.publication]),
			fields: [
				{ key: "survey", label: __("Survey surface"), type: "select", options: surfaces.map((e) => ({ value: e.geometry_ref, label: entity_label(e) })), default: survey && survey.geometry_ref },
				{ key: "survey_date", label: __("Survey date"), default: new Date().toISOString().slice(0, 10), required: true },
				{ key: "scenario", label: __("Compare with schedule"), type: "select", options: [{ value: "", label: __("None") }, ...data.scenarios.map((s) => ({ value: s.name, label: s.scenario_name }))], default: "" },
				{ key: "update_blocks", label: __("Mining Blocks"), type: "check", check_label: __("Record % mined and set Mining / Complete"), default: true },
			],
			submit_label: __("Reconcile"),
			check: (v) => (/^\d{4}-\d{2}-\d{2}$/.test(v.survey_date) ? null : __("Give the survey date as YYYY-MM-DD.")),
		});
		if (!values) return;
		const unsaved = surfaces.find((e) => e.geometry_ref === values.survey && (this.session.dirty.has(e.entity_id) || !this.session.server_ids.has(e.entity_id)));
		if (unsaved) throw new Error(__("Save first (Ctrl+S): the survey surface is not saved yet."));
		const done = await this.ctx.run_job("mining_job", { model: this.model(), operation: "reconcile", params: { ...values, scenario: values.scenario || null } });
		if (!done) return;
		this.ctx.message(done.status_message, "result");
		await this.planning_view({ mode: "mined" }, true); // the blocks coloured by % mined
		this.ctx.show_result({ kind: "reconciliation", title: __("Survey {0}", [done.results.survey_date]), result: done.results });
	}

	// -------------------------------------------------------------- revision comparison

	async compare(preset) {
		const revisions = (await this.revisions()).filter((r) => r.geometry_checksum);
		if (!revisions.length) throw new Error(__("There is no frozen revision to compare with yet."));
		const options = [{ value: "working", label: __("Working state (saved)") }, ...revisions.map((r) => ({ value: r.name, label: `${r.revision_label} · ${__(r.revision_status)}` }))];
		const values =
			preset ||
			(await this.ctx.ask({
				title: __("Compare Revisions"),
				fields: [
					{ key: "older", label: __("Older"), type: "select", options: options.slice(1), default: (revisions[1] || revisions[0]).name },
					{ key: "newer", label: __("Newer"), type: "select", options, default: revisions.length > 1 ? revisions[0].name : "working" },
					{ key: "overlay", label: __("View"), type: "check", check_label: __("Draw the older revision faded over the model"), default: true },
				],
				submit_label: __("Compare"),
				check: (v) => (v.older === v.newer ? __("Choose two different states.") : null),
			}));
		if (!values) return;
		const diff = await call("compare_revisions", { model: this.model(), older: values.older, newer: values.newer === "working" ? null : values.newer });
		const label = (name) => (name === "working" ? __("working state") : (revisions.find((r) => r.name === name) || {}).revision_label || name);
		if (values.overlay) {
			const content = await call("get_revision_content", { model: this.model(), revision: values.older });
			this.ctx.set_compare_overlay({ name: `${this.model()} ${label(values.older)}`, offset: [0, 0, 0], content });
		}
		this.ctx.show_result({ kind: "revision_diff", title: __("{0} → {1}", [label(values.older), label(values.newer)]), result: diff });
	}
}
