<template>
	<div class="cad-analysis">
		<div class="cad-row cad-analysis-head">
			<b class="cad-grow">{{ data.title }}</b>
			<button class="cad-icon-btn" :title="__('Close')" @click="$emit('close')"><Icon name="close" /></button>
		</div>
		<div class="cad-analysis-body">
			<!-- surface checks -->
			<template v-if="data.kind === 'checks'">
				<div class="cad-report">
					<div><span>{{ __("Triangles / vertices") }}</span><b class="mono">{{ n(stats.triangle_count) }} / {{ n(stats.vertex_count) }}</b></div>
					<div><span>{{ __("Plan / surface area") }}</span><b class="mono">{{ f(stats.plan_area) }} / {{ f(stats.surface_area) }}</b></div>
					<div><span>{{ __("Elevations") }}</span><b class="mono">{{ f(stats.min_elevation) }} … {{ f(stats.max_elevation) }}</b></div>
					<template v-if="stats.closed !== undefined">
						<div><span>{{ __("Closed") }}</span><b>{{ stats.closed ? __("Yes") : __("No ({0} open edges)", [stats.open_edges]) }}</b></div>
					</template>
					<template v-for="row in check_rows" :key="row.label">
						<div><span>{{ row.label }}</span><b :class="row.value ? 'is-warning' : 'is-ok'">{{ row.value }}</b></div>
					</template>
				</div>
				<template v-if="spikes.length">
					<h4 class="cad-subhead">{{ __("Possible spikes (largest first)") }}</h4>
					<table class="cad-table small">
						<tr><th>X</th><th>Y</th><th>Z</th><th>{{ __("Departure") }}</th><th></th></tr>
						<tr v-for="s in spikes" :key="s.vertex">
							<td class="mono">{{ f(s.x) }}</td>
							<td class="mono">{{ f(s.y) }}</td>
							<td class="mono">{{ f(s.z) }}</td>
							<td class="mono">{{ f(s.departure) }}</td>
							<td><button class="small" @click="$emit('zoom', [s.x, s.y, s.z])">{{ __("Zoom") }}</button></td>
						</tr>
					</table>
					<p class="muted small">{{ __("Surface → Repair with “Remove spikes” takes them out.") }}</p>
				</template>
			</template>

			<!-- long section -->
			<template v-else-if="data.kind === 'profile'">
				<Chart :series="profile_series" :x_label="__('Chainage')" :y_label="__('Elevation')" />
				<p class="muted small">{{ __("Length {0}. Gaps are where a surface does not cover the alignment.", [f(data.result.length)]) }}</p>
			</template>

			<!-- cross-sections -->
			<template v-else-if="data.kind === 'sections'">
				<div class="cad-row">
					<button :disabled="station <= 0" @click="station--">‹</button>
					<input v-model.number="station" type="range" min="0" :max="sections.length - 1" class="cad-grow" />
					<button :disabled="station >= sections.length - 1" @click="station++">›</button>
					<span class="mono">{{ __("Ch {0}", [f(current_section.chainage)]) }}</span>
				</div>
				<Chart :series="section_series" :x_label="__('Offset (left − / right +)')" :y_label="__('Elevation')" :equal="true" />
				<p class="muted small">{{ __("{0} sections at {1} … {2}; centre {3}.", [sections.length, f(sections[0].chainage), f(sections[sections.length - 1].chainage), fmt_xy(current_section.centre)]) }}</p>
			</template>

			<!-- volume -->
			<template v-else-if="data.kind === 'volume'">
				<div class="cad-report">
					<div><span>{{ __("Cut") }}</span><b class="mono">{{ f(r.cut) }} {{ units3 }}</b></div>
					<div><span>{{ __("Fill") }}</span><b class="mono">{{ f(r.fill) }} {{ units3 }}</b></div>
					<div><span>{{ __("Net (fill − cut)") }}</span><b class="mono">{{ f(r.net) }} {{ units3 }}</b></div>
					<div><span>{{ __("Cut / fill area") }}</span><b class="mono">{{ f(r.cut_area) }} / {{ f(r.fill_area) }}</b></div>
					<div><span>{{ __("Plan area measured") }}</span><b class="mono">{{ f(r.plan_area) }}</b></div>
					<div><span>{{ __("Deepest cut / highest fill") }}</span><b class="mono">{{ f(r.max_depth) }} / {{ f(r.max_height) }}</b></div>
					<div><span>{{ __("Composite triangles") }}</span><b class="mono">{{ n(r.composite_triangles) }}</b></div>
					<div><span>{{ __("Checks") }}</span><b :class="r.valid ? 'is-ok' : 'is-error'">{{ r.valid ? __("All passed") : __("Failed") }}</b></div>
				</div>
				<ul class="small cad-warnings">
					<li v-for="(v, k) in bool_checks" :key="k" :class="v ? 'is-ok' : 'is-error'">{{ v ? "✓" : "✗" }} {{ check_label(k) }}</li>
				</ul>
				<p class="small">{{ r.method }}</p>
				<p v-if="r.record" class="small">
					{{ __("Recorded as") }} <a :href="`/desk/cad-volume-calculation/${encodeURIComponent(r.record)}`" target="_blank">{{ r.record }}</a>
				</p>
			</template>

			<!-- recorded volumes -->
			<template v-else-if="data.kind === 'volume_history'">
				<table v-if="data.rows.length" class="cad-table small">
					<tr><th>{{ __("Record") }}</th><th>{{ __("What") }}</th><th>{{ __("Cut") }}</th><th>{{ __("Fill") }}</th><th>{{ __("Net") }}</th><th></th></tr>
					<tr v-for="row in data.rows" :key="row.name">
						<td><a :href="`/desk/cad-volume-calculation/${encodeURIComponent(row.name)}`" target="_blank">{{ row.name }}</a></td>
						<td>{{ row.title }}</td>
						<td class="mono">{{ f(row.cut_volume) }}</td>
						<td class="mono">{{ f(row.fill_volume) }}</td>
						<td class="mono">{{ f(row.net_volume) }} {{ row.units }}</td>
						<td :class="row.valid ? 'is-ok' : 'is-error'">{{ row.valid ? "✓" : "✗" }}</td>
					</tr>
				</table>
				<p v-else class="muted">{{ __("No volumes have been calculated for this model.") }}</p>
			</template>

			<!-- slope / elevation -->
			<template v-else-if="data.kind === 'analyse'">
				<div class="cad-report">
					<div><span>{{ __("Mean slope (area weighted)") }}</span><b class="mono">{{ r.slope.mean.toFixed(2) }}°</b></div>
					<div><span>{{ __("Steepest triangle") }}</span><b class="mono">{{ r.slope.max.toFixed(2) }}°</b></div>
					<div><span>{{ __("Mean aspect") }}</span><b class="mono">{{ r.aspect.mean.toFixed(1) }}°</b></div>
					<div><span>{{ __("Plan area") }}</span><b class="mono">{{ f(r.stats.plan_area) }}</b></div>
				</div>
				<div class="cad-two">
					<div>
						<h4 class="cad-subhead">{{ __("Slope classes (plan area)") }}</h4>
						<Bars :rows="r.slope.classes.map((c) => ({ label: `${c.from}–${c.to}°`, value: c.plan_area }))" :format="f" />
					</div>
					<div>
						<h4 class="cad-subhead">{{ __("Elevation bands (plan area)") }}</h4>
						<Bars :rows="r.elevation.bands.map((b) => ({ label: `${f(b.from)}–${f(b.to)}`, value: b.plan_area }))" :format="f" />
					</div>
				</div>
			</template>

			<!-- open pit -->
			<template v-else-if="data.kind === 'pit_report'">
				<div class="cad-report">
					<div><span>{{ __("Floor / top") }}</span><b class="mono">{{ f(r.summary.floor_elevation) }} / {{ f(r.summary.top_elevation) }}</b></div>
					<div><span>{{ __("Depth / benches") }}</span><b class="mono">{{ f(r.summary.depth) }} m / {{ r.summary.benches }}</b></div>
					<div><span>{{ __("Overall slope (steepest)") }}</span><b class="mono">{{ r.summary.overall_slope_angle.toFixed(2) }}°</b></div>
					<div><span>{{ __("Floor / footprint area") }}</span><b class="mono">{{ f(r.summary.floor_area) }} / {{ f(r.summary.footprint_area) }}</b></div>
					<div v-if="r.volume"><span>{{ __("Void below the ground") }}</span><b class="mono">{{ f(r.volume.cut) }} m³</b></div>
					<div><span>{{ __("Standard") }}</span><b>{{ r.standard || __("none") }}</b></div>
				</div>
				<table class="cad-table small">
					<tr><th>{{ __("Bench") }}</th><th>{{ __("Toe") }}</th><th>{{ __("Crest") }}</th><th>{{ __("Face width") }}</th><th>{{ __("Face angle") }}</th><th>{{ __("Berm") }}</th></tr>
					<tr v-for="b in r.benches" :key="b.bench">
						<td>{{ b.bench }}</td>
						<td class="mono">{{ f(b.toe) }}</td>
						<td class="mono">{{ f(b.crest) }}</td>
						<td class="mono">{{ f(b.face_width) }}</td>
						<td class="mono">{{ b.face_angle.toFixed(2) }}°</td>
						<td class="mono">{{ b.berm_width === undefined ? "—" : f(b.berm_width) }}</td>
					</tr>
				</table>
				<template v-if="r.ramp">
					<h4 class="cad-subhead">{{ __("In-pit ramp") }}</h4>
					<div class="cad-report">
						<div><span>{{ __("Length / width") }}</span><b class="mono">{{ f(r.ramp.length) }} m / {{ f(r.ramp.width) }} m</b></div>
						<div><span>{{ __("From / to") }}</span><b class="mono">{{ f(r.ramp.start[2]) }} → {{ f(r.ramp.end[2]) }}</b></div>
						<div><span>{{ __("Steepest grade") }}</span><b class="mono">{{ r.ramp.max_grade.toFixed(2) }} % ({{ __("design") }} {{ r.ramp.gradient }} %)</b></div>
						<div><span>{{ __("More cut for the ramp") }}</span><b class="mono">{{ f(r.ramp.extra_cut) }} m³</b></div>
						<div><span>{{ __("Left in place under it") }}</span><b class="mono">{{ f(r.ramp.left_in_place) }} m³</b></div>
					</div>
					<p class="muted small">{{ r.post_mining ? __("The ramp is in the post-mining surface; the pit surface and the rings are the benches without it.") : __("The ramp is in the pit surface; the rings are the benches without it.") }}</p>
				</template>
				<p class="muted small">
					{{ r.daylight.length ? __("Daylight line: where the pit meets the ground.") : __("The pit does not meet the ground (or no ground was chosen).") }}
					{{ r.post_mining ? __("The post-mining surface joins the pit and the ground along it.") : "" }}
					{{ __("Save, then Mining → Validate Design.") }}
				</p>
			</template>

			<!-- geology -->
			<template v-else-if="data.kind === 'geology_report'">
				<div class="cad-report">
					<div><span>{{ __("Record") }}</span><b><a :href="`/desk/cad-geology-model/${encodeURIComponent(r.geology)}`" target="_blank">{{ r.geology }}</a></b></div>
					<div><span>{{ __("Source") }}</span><b>{{ r.geo_project }} · {{ __(r.source) }}</b></div>
					<div><span>{{ __("Axes") }}</span><b>{{ __(r.axis) }}</b></div>
					<div><span>{{ __("Checks") }}</span><b :class="r.checks_status === 'Pass' ? '' : r.checks_status === 'Fail' ? 'is-error' : 'is-warning'">{{ __(r.checks_status) }}</b></div>
				</div>
				<table class="cad-table small">
					<tr><th>{{ __("Seam") }}</th><th>{{ __("Area m²") }}</th><th>{{ __("Thickness") }}</th><th>{{ __("Volume m³") }}</th><th>{{ __("Density") }}</th><th>{{ __("Tonnes") }}</th><th>{{ __("Qualities") }}</th></tr>
					<tr v-for="s in r.seams" :key="s.seam">
						<td>{{ s.seam_label || s.seam }}</td>
						<td class="mono">{{ f(s.plan_area) }}</td>
						<td class="mono">{{ f(s.mean_thickness) }}</td>
						<td class="mono">{{ f(s.volume) }}</td>
						<td class="mono">{{ s.mean_density ? s.mean_density.toFixed(3) : "—" }}</td>
						<td class="mono">{{ s.tonnes ? f(s.tonnes) : "—" }}</td>
						<td class="small">{{ Object.entries(JSON.parse(s.qualities || "{}")).filter(([k]) => k !== "Relative Density").map(([k, v]) => `${k} ${Number(v).toFixed(2)}`).join(" · ") }}</td>
					</tr>
				</table>
				<h4 class="cad-subhead">{{ __("Checks") }}</h4>
				<ul class="small cad-warnings">
					<li v-for="(c, i) in r.checks" :key="i" :class="c.status === 'pass' ? '' : c.status === 'fail' ? 'is-error' : 'is-warning'">{{ c.status === "pass" ? "✓" : "!" }} {{ c.message }}</li>
				</ul>
				<p class="muted small">{{ r.method }} {{ __("Qualities are tonnage-weighted (thickness × density). Next: Mining → Block Geology.") }}</p>
			</template>

			<template v-else-if="data.kind === 'block_geology_report'">
				<div class="cad-report">
					<div><span>{{ __("Coal") }}</span><b class="mono">{{ f(r.totals.coal_tonnes) }} t · {{ f(r.totals.coal_volume) }} m³</b></div>
					<div><span>{{ __("Waste") }}</span><b class="mono">{{ f(r.totals.waste_volume) }} m³</b></div>
					<div><span>{{ __("Strip ratio") }}</span><b class="mono">{{ r.totals.strip_ratio ? r.totals.strip_ratio.toFixed(2) + " bcm/t" : "—" }}</b></div>
					<div><span>{{ __("Rule") }}</span><b>{{ __("coal from {0} % of a block", [r.rule.min_coal]) }}</b></div>
				</div>
				<table class="cad-table small">
					<tr><th>{{ __("Seam") }}</th><th>{{ __("Volume m³") }}</th><th>{{ __("Tonnes") }}</th><th>{{ __("Qualities (tonnage-weighted)") }}</th></tr>
					<tr v-for="s in r.seams" :key="s.seam">
						<td>{{ s.label || s.seam }}</td>
						<td class="mono">{{ f(s.volume) }}</td>
						<td class="mono">{{ f(s.tonnes) }}</td>
						<td class="small">{{ Object.entries(s.qualities).map(([k, v]) => `${k} ${Number(v).toFixed(2)}`).join(" · ") }}</td>
					</tr>
				</table>
				<h4 class="cad-subhead">{{ __("By bench") }}</h4>
				<table class="cad-table small">
					<tr><th>{{ __("Bench") }}</th><th>{{ __("Blocks") }}</th><th>{{ __("Coal blocks") }}</th><th>{{ __("Coal t") }}</th><th>{{ __("Waste m³") }}</th><th>{{ __("Strip ratio") }}</th></tr>
					<tr v-for="b in geology_benches" :key="b.bench">
						<td class="mono">{{ f(b.bench) }}</td>
						<td class="mono">{{ b.blocks }}</td>
						<td class="mono">{{ b.coal_blocks }}</td>
						<td class="mono">{{ f(b.tonnes) }}</td>
						<td class="mono">{{ f(b.waste) }}</td>
						<td class="mono">{{ b.tonnes ? (b.waste / b.tonnes).toFixed(2) : "—" }}</td>
					</tr>
				</table>
				<p class="muted small">{{ __("Each block's geology (seams, tonnes, qualities, strip ratio) is in its Properties; materials are set from the rule. Save to keep them.") }}</p>
			</template>

			<!-- point clouds -->
			<template v-else-if="data.kind === 'cloud_report'">
				<table class="cad-table small">
					<tr><th>{{ __("Step") }}</th><th>{{ __("Points before") }}</th><th>{{ __("After") }}</th><th></th></tr>
					<tr v-for="(s, i) in r.report" :key="i">
						<td>{{ __(s.op) }}</td>
						<td class="mono">{{ n(s.before) }}</td>
						<td class="mono">{{ n(s.after) }}</td>
						<td class="small">{{ s.changed !== undefined ? __("{0} reclassified", [n(s.changed)]) : s.ground !== undefined ? __("{0} ground, {1} low noise", [n(s.ground), n(s.low_noise)]) : "" }}</td>
					</tr>
				</table>
				<div class="cad-report">
					<div><span>{{ __("Classes") }}</span><b class="small">{{ cloud_classes(r.stats.classes) }}</b></div>
					<div><span>{{ __("Elevations") }}</span><b class="mono">{{ f(r.stats.min_elevation) }} … {{ f(r.stats.max_elevation) }}</b></div>
				</div>
				<p class="muted small">{{ __("A new version of the cloud; Undo returns to the previous one. Save to keep it.") }}</p>
			</template>

			<template v-else-if="data.kind === 'cloud_compare'">
				<div class="cad-report">
					<div><span>{{ __("Compared / off the surface") }}</span><b class="mono">{{ n(r.comparison.compared) }} / {{ n(r.comparison.off_surface) }}</b></div>
					<div><span>{{ __("Within ±{0} m", [r.comparison.tolerance]) }}</span><b class="mono">{{ r.comparison.within !== undefined ? r.comparison.within.toFixed(1) + " %" : "—" }}</b></div>
					<div><span>{{ __("Above / below") }}</span><b class="mono">{{ r.comparison.above !== undefined ? `${r.comparison.above.toFixed(1)} % / ${r.comparison.below.toFixed(1)} %` : "—" }}</b></div>
					<div><span>{{ __("Mean / RMSE") }}</span><b class="mono">{{ f(r.comparison.mean) }} / {{ f(r.comparison.rmse) }}</b></div>
					<div><span>{{ __("5 % / median / 95 %") }}</span><b class="mono">{{ f(r.comparison.p05) }} / {{ f(r.comparison.p50) }} / {{ f(r.comparison.p95) }}</b></div>
					<div><span>{{ __("Min / max") }}</span><b class="mono">{{ f(r.comparison.min) }} / {{ f(r.comparison.max) }}</b></div>
				</div>
				<template v-if="r.comparison.histogram">
					<h4 class="cad-subhead">{{ __("Deviation (m)") }}</h4>
					<Bars :rows="deviation_rows" :format="n" />
				</template>
				<p class="muted small">{{ __("+ above the surface, − below. The cloud is coloured by its deviation (blue below, red above); Point Cloud → Point Size sets the colour range.") }}</p>
			</template>

			<!-- pit-shell optimisation -->
			<template v-else-if="data.kind === 'optimisation_report'">
				<Chart plain :series="shell_series" :x_label="__('Revenue factor')" :y_label="__('Value at RF 1 (millions)')" />
				<div class="cad-report">
					<div><span>{{ __("Record") }}</span><b><a :href="`/desk/cad-pit-optimisation/${encodeURIComponent(r.optimisation)}`" target="_blank">{{ r.optimisation }}</a></b></div>
					<div><span>{{ __("Block model") }}</span><b class="mono">{{ r.grid.nx }} × {{ r.grid.ny }} × {{ r.grid.benches }} · {{ f(r.grid.dx) }} m · {{ f(r.grid.bench) }} m</b></div>
					<div><span>{{ __("Drawn shell") }}</span><b class="mono">RF {{ r.selected_rf }} · {{ f(r.selected.tonnes) }} t · SR {{ r.selected.strip_ratio ? r.selected.strip_ratio.toFixed(2) : "—" }}</b></div>
					<div><span>{{ __("Value (RF 1)") }}</span><b class="mono">{{ f(r.selected.value) }}</b></div>
				</div>
				<table class="cad-table small">
					<tr><th>RF</th><th>{{ __("Blocks") }}</th><th>{{ __("Coal t") }}</th><th>{{ __("Waste bcm") }}</th><th>{{ __("Strip ratio") }}</th><th>{{ __("Value at RF 1") }}</th></tr>
					<tr v-for="s in r.shells" :key="s.revenue_factor" :class="s.revenue_factor === r.selected_rf ? 'is-selected' : ''">
						<td class="mono">{{ s.revenue_factor.toFixed(2) }}</td>
						<td class="mono">{{ s.blocks }}</td>
						<td class="mono">{{ f(s.tonnes) }}</td>
						<td class="mono">{{ f(s.waste_volume) }}</td>
						<td class="mono">{{ s.strip_ratio ? s.strip_ratio.toFixed(2) : "—" }}</td>
						<td class="mono" :class="s.value < 0 ? 'is-error' : ''">{{ f(s.value) }}</td>
					</tr>
				</table>
				<p class="muted small">{{ r.method }} {{ __("Slope reach per bench: {0} m. Double-click the shell (or Mining → Edit Design) to draw another revenue factor.", [f(r.reach)]) }}</p>
			</template>

			<!-- pit stages -->
			<template v-else-if="data.kind === 'stages_report'">
				<Bars :rows="r.table.map((t) => ({ label: t.stage, value: t.increment }))" :format="(v) => f(v) + ' m³'" />
				<table class="cad-table small">
					<tr><th>{{ __("Stage") }}</th><th>{{ __("Floor") }}</th><th>{{ __("Benches") }}</th><th>{{ __("Volume") }}</th><th>{{ __("Increment") }}</th><th>{{ __("Pushback") }}</th><th>{{ __("Nested") }}</th></tr>
					<tr v-for="t in r.table" :key="t.stage">
						<td>{{ t.stage }}</td>
						<td class="mono">{{ f(t.floor_elevation) }}</td>
						<td class="mono">{{ t.benches }}</td>
						<td class="mono">{{ f(t.volume) }}</td>
						<td class="mono">{{ f(t.increment) }}</td>
						<td class="mono" :class="t.pushback_ok === false ? 'is-error' : ''">{{ t.pushback_width === null ? "—" : f(t.pushback_width) + " m" }}</td>
						<td :class="t.contains_previous ? '' : 'is-error'">{{ t.contains_previous ? "✓" : __("no: {0} m³ above", [f(t.undercut)]) }}</td>
					</tr>
				</table>
				<p class="muted small">{{ __("Increment: from the stage before's post-mining surface down to this one's. Pushback: the narrowest distance between consecutive daylight lines (minimum {0} m). Each stage is its own pit design (Mining → Edit Design regenerates the whole run).", [f(r.min_pushback_width)]) }}</p>
			</template>

			<!-- haul road -->
			<template v-else-if="data.kind === 'road_report'">
				<Chart :series="road_series" :x_label="__('Chainage')" :y_label="__('Elevation')" />
				<div class="cad-report">
					<div><span>{{ __("Length") }}</span><b class="mono">{{ f(r.length) }} m</b></div>
					<div><span>{{ __("Width") }}</span><b class="mono">{{ f(r.options.width) }} m</b></div>
					<div><span>{{ __("Steepest grade") }}</span><b class="mono" :class="grade_bad ? 'is-error' : ''">{{ r.max_grade.toFixed(2) }} %{{ std && std.maximum_gradient ? ` (≤ ${std.maximum_gradient} %)` : "" }}</b></div>
					<div><span>{{ __("Tightest curve") }}</span><b class="mono" :class="radius_bad ? 'is-error' : ''">{{ r.min_radius ? f(r.min_radius) + " m" : __("straight") }}{{ std && std.minimum_turning_radius ? ` (≥ ${std.minimum_turning_radius} m)` : "" }}</b></div>
					<template v-if="r.volume">
						<div><span>{{ __("Cut / fill") }}</span><b class="mono">{{ f(r.volume.cut) }} / {{ f(r.volume.fill) }} m³</b></div>
						<div><span>{{ __("Recorded as") }}</span><b><a :href="`/desk/cad-volume-calculation/${encodeURIComponent(r.volume.record)}`" target="_blank">{{ r.volume.record }}</a></b></div>
					</template>
				</div>
				<template v-if="r.vertical_curves && r.vertical_curves.length">
					<h4 class="cad-subhead">{{ __("Vertical curves") }}</h4>
					<table class="cad-table small">
						<tr><th>{{ __("PVI chainage") }}</th><th>{{ __("Elevation") }}</th><th>{{ __("Grades in → out") }}</th><th>{{ __("Length") }}</th><th>K</th><th></th></tr>
						<tr v-for="(c, i) in r.vertical_curves" :key="i">
							<td class="mono">{{ f(c.chainage) }}</td>
							<td class="mono">{{ f(c.elevation) }}</td>
							<td class="mono">{{ c.grade_in.toFixed(2) }} → {{ c.grade_out.toFixed(2) }} %</td>
							<td class="mono" :class="c.shortened ? 'is-warning' : ''">{{ f(c.length) }}</td>
							<td class="mono" :class="std && std.minimum_k_value && c.k < std.minimum_k_value ? 'is-error' : ''">{{ c.k.toFixed(1) }}</td>
							<td>{{ c.kind === "crest" ? __("crest") : __("sag") }}</td>
						</tr>
					</table>
				</template>
				<ul v-if="r.notes.length" class="small is-warning cad-warnings">
					<li v-for="(t, i) in r.notes" :key="i">{{ t }}</li>
				</ul>
				<p class="muted small">{{ __("Grades over the limit: {0} of {1} stations.", [over_grade, r.stations.length]) }}</p>
			</template>

			<!-- mining blocks -->
			<template v-else-if="data.kind === 'blocks_report'">
				<div class="cad-report">
					<div><span>{{ __("Blocks") }}</span><b class="mono">{{ n(data.rows.length) }}</b></div>
					<div><span>{{ __("In-situ volume") }}</span><b class="mono">{{ f(block_total) }} m³</b></div>
				</div>
				<div class="cad-two">
					<table class="cad-table small">
						<tr><th>{{ __("Bench (bottom)") }}</th><th>{{ __("Blocks") }}</th><th>{{ __("Volume m³") }}</th></tr>
						<tr v-for="g in by_bench" :key="g.key"><td class="mono">{{ f(g.key) }}</td><td>{{ g.count }}</td><td class="mono">{{ f(g.volume) }}</td></tr>
					</table>
					<table class="cad-table small">
						<tr><th>{{ __("Material") }}</th><th>{{ __("Blocks") }}</th><th>{{ __("Volume m³") }}</th></tr>
						<tr v-for="g in by_material" :key="g.key"><td>{{ g.key || __("(not set)") }}</td><td>{{ g.count }}</td><td class="mono">{{ f(g.volume) }}</td></tr>
					</table>
				</div>
				<table class="cad-table small">
					<tr><th>#</th><th>{{ __("Block") }}</th><th>{{ __("Bench") }}</th><th>{{ __("Material") }}</th><th>{{ __("Volume m³") }}</th><th></th></tr>
					<tr v-for="b in data.rows.slice(0, 400)" :key="b.entity_id">
						<td>{{ b.sequence }}</td>
						<td class="mono">{{ b.block_id }}</td>
						<td class="mono">{{ f(b.bench_bottom) }}</td>
						<td>{{ b.material || "—" }}</td>
						<td class="mono">{{ f(b.volume) }}</td>
						<td><button class="small" @click="$emit('select', [b.entity_id])">{{ __("Show") }}</button></td>
					</tr>
				</table>
				<p v-if="data.rows.length > 400" class="muted small">{{ __("The first 400 of {0}; the CSV has them all.", [data.rows.length]) }}</p>
				<div class="cad-row"><span class="cad-grow"></span><button @click="download_blocks">{{ __("Download CSV") }}</button></div>
			</template>

			<!-- drill pattern -->
			<template v-else-if="data.kind === 'drill_report'">
				<div class="cad-report">
					<div><span>{{ __("Holes / rows") }}</span><b class="mono">{{ r.summary.holes }} / {{ r.summary.rows }}</b></div>
					<div><span>{{ __("Burden × spacing") }}</span><b class="mono">{{ f(r.design.burden) }} × {{ f(r.design.spacing) }} m · {{ __(r.design.pattern_type || "") }}</b></div>
					<div><span>{{ __("Diameter / subdrill") }}</span><b class="mono">{{ r.design.diameter_mm }} mm / {{ f(r.design.subdrill) }} m</b></div>
					<div><span>{{ __("Inclination / dip") }}</span><b class="mono">{{ r.design.inclination }}° / {{ Math.round(r.design.dip) }}°</b></div>
					<div><span>{{ __("Drilled length") }}</span><b class="mono">{{ f(r.summary.drilled_length) }} m</b></div>
					<div><span>{{ __("Hole length (min / mean / max)") }}</span><b class="mono">{{ f(r.summary.min_length) }} / {{ f(r.summary.mean_length) }} / {{ f(r.summary.max_length) }}</b></div>
					<div v-if="r.summary.pattern_area"><span>{{ __("Pattern area") }}</span><b class="mono">{{ f(r.summary.pattern_area) }} m²</b></div>
					<div v-if="r.summary.rock_per_hole"><span>{{ __("Rock per hole (B × S × H)") }}</span><b class="mono">{{ f(r.summary.rock_per_hole) }} m³</b></div>
				</div>
				<ul v-if="r.problems && r.problems.length" class="small is-warning cad-warnings">
					<li v-for="(t, i) in r.problems.slice(0, 50)" :key="i">{{ t }}</li>
				</ul>
				<table class="cad-table small">
					<tr><th>{{ __("Hole") }}</th><th>{{ __("Collar X, Y, Z") }}</th><th>{{ __("Toe Z") }}</th><th>{{ __("Length") }}</th></tr>
					<tr v-for="h in r.holes.slice(0, 300)" :key="h.hole_id">
						<td class="mono">{{ h.hole_id }}</td>
						<td class="mono">{{ f(h.collar[0]) }}, {{ f(h.collar[1]) }}, {{ f(h.collar[2]) }}</td>
						<td class="mono">{{ f(h.toe[2]) }}</td>
						<td class="mono">{{ f(h.length) }}</td>
					</tr>
				</table>
				<p class="muted small">{{ __("Layout geometry only. Save, then Validate Design; File → Export → Drill holes (CSV) gives the hole table for the rigs.") }}</p>
			</template>

			<!-- published to Geo Planning -->
			<template v-else-if="data.kind === 'publication'">
				<div class="cad-report">
					<div><span>{{ __("Revision") }}</span><b>{{ r.revision_label }} · <span class="mono" :title="r.geometry_checksum">{{ (r.geometry_checksum || "").slice(0, 12) }}…</span></b></div>
					<div><span>{{ __("Design standard / validation") }}</span><b>{{ r.design_standard || "—" }} · {{ __(r.validation_status || "") }}</b></div>
					<div><span>{{ __("Layout") }}</span><b><a :href="`/desk/geo-pit-layout/${encodeURIComponent(r.geo_pit_layout)}`" target="_blank">{{ r.geo_pit_layout }}</a></b></div>
					<div><span>{{ __("Blocks") }}</span><b class="mono">{{ r.blocks_published }} ({{ r.blocks_created }} {{ __("new") }}, {{ r.blocks_updated }} {{ __("updated") }}, {{ r.blocks_unchanged }} {{ __("unchanged") }})</b></div>
					<div><span>{{ __("Volume / tonnes") }}</span><b class="mono">{{ f(r.total_volume) }} m³ / {{ f(r.total_tonnes) }} t</b></div>
					<div v-if="r.schedule_selection"><span>{{ __("Schedule selection") }}</span><b><a :href="`/desk/mining-schedule-selection/${encodeURIComponent(r.schedule_selection)}`" target="_blank">{{ r.schedule_selection }}</a></b></div>
				</div>
				<table class="cad-table small">
					<tr><th>{{ __("Material") }}</th><th>{{ __("Blocks") }}</th><th>{{ __("Density") }}</th><th>{{ __("Volume m³") }}</th><th>{{ __("Tonnes") }}</th></tr>
					<tr v-for="(m, name) in r.materials" :key="name"><td>{{ name }} · {{ __(m.status) }}</td><td>{{ m.blocks }}</td><td class="mono">{{ m.density }}</td><td class="mono">{{ f(m.volume) }}</td><td class="mono">{{ f(m.tonnes) }}</td></tr>
				</table>
				<template v-if="(r.report && r.report.flagged || []).length">
					<h4 class="cad-subhead is-warning">{{ __("Flagged for review ({0})", [r.report.flagged.length]) }}</h4>
					<ul class="small cad-warnings"><li v-for="(x, i) in r.report.flagged.slice(0, 50)" :key="i">{{ x.block }}: {{ x.reason }}</li></ul>
				</template>
				<p class="muted small"><a :href="`/desk/cad-design-publication/${encodeURIComponent(r.name)}`" target="_blank">{{ r.name }}</a> · {{ __("The Mining Blocks are in Geo Planning (Mining Block Selector, schedules).") }}</p>
			</template>

			<!-- planning view legend -->
			<template v-else-if="data.kind === 'planning_view'">
				<p class="small">{{ __("{0} published blocks ({1}). View only: Mining → Planning View Off.", [r.blocks, r.publication || "—"]) }}</p>
				<table class="cad-table small">
					<tr><th></th><th>{{ __("Class") }}</th><th>{{ __("Blocks") }}</th><th>{{ __("Design volume m³") }}</th></tr>
					<tr v-for="g in r.legend" :key="g.label"><td><i class="cad-swatch" :style="{ background: g.colour }"></i></td><td>{{ g.label }}</td><td>{{ g.count }}</td><td class="mono">{{ f(g.volume) }}</td></tr>
				</table>
			</template>

			<!-- survey reconciliation -->
			<template v-else-if="data.kind === 'reconciliation'">
				<div class="cad-report">
					<div><span>{{ __("Survey") }}</span><b>{{ r.survey }} · {{ r.survey_date }}</b></div>
					<div><span>{{ __("Planned (design)") }}</span><b class="mono">{{ f(r.totals.planned) }} m³</b></div>
					<div><span>{{ __("Mined to date") }}</span><b class="mono">{{ f(r.totals.mined) }} m³ ({{ pct(r.totals.mined - r.totals.over, r.totals.planned) }})</b></div>
					<div><span>{{ __("Remaining in design") }}</span><b class="mono">{{ f(r.totals.remaining) }} m³</b></div>
					<div><span>{{ __("Mined beyond design") }}</span><b class="mono" :class="r.totals.over > 0.005 * r.totals.planned ? 'is-warning' : ''">{{ f(r.totals.over) }} m³</b></div>
					<div v-if="r.scenario"><span>{{ __("Scheduled to date / variance") }}</span><b class="mono">{{ f(r.totals.scheduled) }} / {{ f(r.totals.mined - r.totals.scheduled) }} m³</b></div>
					<div><span>{{ __("Blocks complete / in progress / not started") }}</span><b class="mono">{{ r.states.Complete }} / {{ r.states["In Progress"] }} / {{ r.states["Not Started"] }}</b></div>
					<div><span>{{ __("Checks") }}</span><b :class="r.valid ? 'is-ok' : 'is-error'">{{ r.valid ? __("Design matches the publication") : __("Design differs from the publication by {0} m³", [f(r.checks.max_design_difference)]) }}</b></div>
				</div>
				<p v-if="!r.checks.balance_holds" class="small is-warning">{{ __("Some blocks have material above the pre-mining surface (dumped or rehandled): there, mined − beyond design + remaining ≠ planned.") }}</p>
				<table class="cad-table small">
					<tr><th>{{ __("Block") }}</th><th>{{ __("Planned") }}</th><th>{{ __("Mined") }}</th><th>{{ __("Beyond") }}</th><th v-if="r.scenario">{{ __("Scheduled") }}</th><th>%</th><th></th></tr>
					<tr v-for="b in r.rows.slice(0, 300)" :key="b.mining_block">
						<td class="mono">{{ b.block_id }}</td>
						<td class="mono">{{ f(b.planned_volume) }}</td>
						<td class="mono">{{ f(b.mined_volume) }}</td>
						<td class="mono">{{ f(b.over_mined_volume) }}</td>
						<td v-if="r.scenario" class="mono">{{ f(b.scheduled_volume) }}</td>
						<td class="mono">{{ Math.round(b.percent_complete) }}</td>
						<td><button class="small" @click="$emit('select', [b.cad_entity_id])">{{ __("Show") }}</button></td>
					</tr>
				</table>
				<p class="muted small"><a :href="`/desk/cad-survey-reconciliation/${encodeURIComponent(r.name)}`" target="_blank">{{ r.name }}</a></p>
			</template>

			<!-- revision comparison -->
			<template v-else-if="data.kind === 'revision_diff'">
				<div class="cad-report">
					<div><span>{{ __("Added / removed / changed") }}</span><b class="mono">{{ r.counts.added }} / {{ r.counts.removed }} / {{ r.counts.changed }}</b></div>
					<div><span>{{ __("Blocks (volume)") }}</span><b class="mono">{{ r.design.older.blocks }} ({{ f(r.design.older.block_volume) }}) → {{ r.design.newer.blocks }} ({{ f(r.design.newer.block_volume) }})</b></div>
					<div><span>{{ __("Pit stages / drill holes / surfaces") }}</span><b class="mono">{{ r.design.older.pit_stages }}/{{ r.design.older.drill_holes }}/{{ r.design.older.surfaces }} → {{ r.design.newer.pit_stages }}/{{ r.design.newer.drill_holes }}/{{ r.design.newer.surfaces }}</b></div>
				</div>
				<table class="cad-table small">
					<tr><th>{{ __("Type") }}</th><th>{{ __("Added") }}</th><th>{{ __("Removed") }}</th><th>{{ __("Changed") }}</th></tr>
					<tr v-for="(c, t) in r.by_type" :key="t"><td>{{ __(t) }}</td><td>{{ c.added }}</td><td>{{ c.removed }}</td><td>{{ c.changed }}</td></tr>
				</table>
				<div class="cad-row">
					<button :disabled="!r.added.length" @click="$emit('select', r.added.map((x) => x.entity_id))">{{ __("Select added") }}</button>
					<button :disabled="!r.changed.length" @click="$emit('select', r.changed.map((x) => x.entity_id))">{{ __("Select changed") }}</button>
				</div>
				<ul v-if="r.removed.length" class="small cad-warnings">
					<li v-for="x in r.removed.slice(0, 100)" :key="x.entity_id">{{ __("Removed") }}: {{ __(x.entity_type) }} {{ x.name || x.entity_id }} ({{ x.layer }})</li>
				</ul>
				<ul v-if="r.changed.length" class="small cad-warnings">
					<li v-for="x in r.changed.slice(0, 100)" :key="x.entity_id">{{ __(x.entity_type) }} {{ x.name || x.entity_id }}: {{ x.changed.join(", ") }}</li>
				</ul>
				<p class="muted small">{{ __("The older state is drawn faded (File → Compare Overlay Off to hide it).") }}</p>
			</template>

			<!-- design validation -->
			<template v-else-if="data.kind === 'design_validation'">
				<p>
					<b :class="{ 'is-ok': r.status === 'Pass', 'is-warning': r.status === 'Warning', 'is-error': r.status === 'Fail' }">{{ __(r.status) }}</b>
					— {{ r.summary }}
					<a :href="`/desk/cad-design-validation/${encodeURIComponent(r.name)}`" target="_blank">{{ r.name }}</a>
				</p>
				<table class="cad-table small">
					<tr><th></th><th>{{ __("Check") }}</th><th>{{ __("Where") }}</th><th>{{ __("Value") }}</th><th>{{ __("Limit") }}</th><th></th></tr>
					<tr v-for="(c, i) in sorted_checks" :key="i">
						<td :class="{ 'is-ok': c.status === 'pass', 'is-warning': c.status === 'warning', 'is-error': c.status === 'fail' }">{{ c.status === "pass" ? "✓" : c.status === "warning" ? "!" : "✗" }}</td>
						<td>{{ c.category }}: {{ c.check }}</td>
						<td>{{ c.message }}</td>
						<td class="mono">{{ c.value === null || c.value === undefined ? "" : f(c.value) }} {{ c.value === null || c.value === undefined ? "" : c.unit }}</td>
						<td class="mono">{{ c.limit === null || c.limit === undefined ? "" : f(c.limit) }}</td>
						<td>
							<button v-if="c.at" class="small" @click="$emit('zoom', c.at)">{{ __("Zoom") }}</button>
							<button v-if="c.entities && c.entities.length" class="small" @click="$emit('select', c.entities)">{{ __("Select") }}</button>
						</td>
					</tr>
				</table>
			</template>
		</div>
	</div>
</template>

<script setup>
import { computed, defineComponent, h, ref, watch } from "vue";
import { format_number } from "./geometry";
import { CLASS_NAMES } from "./pointclouds";
import Icon from "./Icon.vue";

const props = defineProps({ data: Object, decimals: { type: Number, default: 3 } });
defineEmits(["close", "zoom", "select"]);

const COLORS = ["#4aa8d8", "#e5a53b", "#5fbf6a", "#d5596b", "#a782e0", "#cfcf5a"];
const UNITS3 = { Metres: "m³", Millimetres: "mm³", Kilometres: "km³", Feet: "ft³", "US Survey Feet": "US ft³" };

const r = computed(() => props.data.result || {});
const stats = computed(() => props.data.stats || {});
const units3 = computed(() => UNITS3[props.data.units] || "");
const f = (v) => (v === null || v === undefined ? "—" : format_number(v, props.decimals));
const pct = (a, b) => (b > 0 ? `${Math.round((100 * a) / b)} %` : "—");
const n = (v) => (v === null || v === undefined ? "—" : Number(v).toLocaleString());
const fmt_xy = (p) => (p ? `${f(p[0])}, ${f(p[1])}` : "");

// ------------------------------------------------------------------ checks
const check_rows = computed(() => {
	const v = stats.value.validation || {};
	return [
		[__("Degenerate triangles"), v.degenerate_triangles],
		[__("Flipped triangles"), v.flipped_triangles],
		[__("Duplicate triangles"), v.duplicate_triangles],
		[__("Non-manifold edges"), v.non_manifold_edges],
		[__("Duplicate plan points"), v.duplicate_plan_points],
		[__("Possible spikes"), v.spike_count],
		[__("Outline loops"), v.outline_loops > 1 ? v.outline_loops : 0],
	]
		.filter(([, value]) => value !== undefined)
		.map(([label, value]) => ({ label, value }));
});
const spikes = computed(() => ((stats.value.validation || {}).spikes || []).slice(0, 50));

// ------------------------------------------------------------------ volume
const bool_checks = computed(() => Object.fromEntries(Object.entries(r.value.validation || {}).filter(([, v]) => typeof v === "boolean")));
function check_label(key) {
	return (
		{
			net_matches_direct_integral: __("Net volume equals the direct integral of the difference"),
			areas_add_up: __("Cut area + fill area = plan area"),
			cut_not_negative: __("No negative cut"),
			fill_not_negative: __("No negative fill"),
		}[key] || key
	);
}

// ------------------------------------------------------------------ profile and sections
// ------------------------------------------------------------------ mining design
const std = computed(() => props.data.standard || null);
// the deviation histogram: bins from − to + range; the end bins also hold the tails beyond it
const deviation_rows = computed(() => {
	const h = props.data.kind === "cloud_compare" && r.value.comparison.histogram;
	if (!h) return [];
	const last = h.counts.length - 1;
	return h.counts.map((c, i) => ({ label: i === 0 && h.below ? `≤ ${h.edges[1].toFixed(2)}` : i === last && h.beyond ? `≥ ${h.edges[last].toFixed(2)}` : `${h.edges[i].toFixed(2)} … ${h.edges[i + 1].toFixed(2)}`, value: c }));
});

function cloud_classes(classes) {
	return Object.entries(classes || {})
		.map(([c, k]) => `${CLASS_NAMES[c] || c} ${Number(k).toLocaleString()}`)
		.join(" · ");
}

// block geology by bench (bottom elevation, top bench first)
const geology_benches = computed(() => {
	if (props.data.kind !== "block_geology_report") return [];
	const by = new Map();
	for (const b of props.data.rows || []) {
		const g = b.geology || {};
		const row = by.get(b.bench_bottom) || { bench: b.bench_bottom, blocks: 0, coal_blocks: 0, tonnes: 0, waste: 0 };
		row.blocks += 1;
		row.coal_blocks += g.coal_tonnes > 0 && b.material !== props.data.result.rule.waste_material ? 1 : 0;
		row.tonnes += g.coal_tonnes || 0;
		row.waste += g.waste_volume || 0;
		by.set(b.bench_bottom, row);
	}
	return [...by.values()].sort((a, b) => b.bench - a.bench);
});

// value of each nested shell at revenue factor 1 (the pit-by-pit graph)
const shell_series = computed(() => {
	if (props.data.kind !== "optimisation_report") return [];
	return [{ name: __("Value at RF 1"), color: COLORS[0], runs: [r.value.shells.map((s) => [s.revenue_factor, s.value / 1e6])] }];
});

const road_series = computed(() => {
	const st = r.value.stations || [];
	return st.length ? [{ name: __("Centreline"), color: COLORS[0], runs: [st.map((s) => [s[0], s[3]])] }] : [];
});
const grade_bad = computed(() => std.value && std.value.maximum_gradient && r.value.max_grade > std.value.maximum_gradient + 1e-9);
const radius_bad = computed(() => std.value && std.value.minimum_turning_radius && r.value.min_radius && r.value.min_radius < std.value.minimum_turning_radius - 1e-9);
const over_grade = computed(() => (std.value && std.value.maximum_gradient ? (r.value.stations || []).filter((s) => Math.abs(s[4]) > std.value.maximum_gradient + 1e-9).length : 0));
const block_total = computed(() => (props.data.rows || []).reduce((a, b) => a + (b.volume || 0), 0));
function group_by(key) {
	const groups = new Map();
	for (const b of props.data.rows || []) {
		const k = b[key] ?? "";
		const g = groups.get(k) || { key: k, count: 0, volume: 0 };
		g.count++;
		g.volume += b.volume || 0;
		groups.set(k, g);
	}
	return [...groups.values()];
}
const by_bench = computed(() => group_by("bench_bottom").sort((a, b) => b.key - a.key));
const by_material = computed(() => group_by("material").sort((a, b) => b.volume - a.volume));
const sorted_checks = computed(() => {
	const order = { fail: 0, warning: 1, pass: 2 };
	return [...(r.value.checks || [])].sort((a, b) => order[a.status] - order[b.status]);
});

function download_blocks() {
	const head = ["sequence", "block_id", "bench_bottom", "bench_top", "material", "volume_m3", "plan_area_m2", "upper_surface", "lower_surface", "entity_id"];
	const esc = (v) => (/[",\n]/.test(String(v ?? "")) ? `"${String(v).replace(/"/g, '""')}"` : String(v ?? ""));
	const lines = [head.join(",")].concat(props.data.rows.map((b) => [b.sequence, b.block_id, b.bench_bottom, b.bench_top, b.material || "", (b.volume || 0).toFixed(3), (b.plan_area || 0).toFixed(3), b.upper, b.lower, b.entity_id].map(esc).join(",")));
	const url = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/csv" }));
	const a = document.createElement("a");
	a.href = url;
	a.download = `${props.data.model || "mining"}_blocks.csv`;
	a.click();
	URL.revokeObjectURL(url);
}

const names = computed(() => r.value.names || {});
const profile_series = computed(() =>
	Object.entries(r.value.profiles || {}).map(([ref, runs], i) => ({ name: names.value[ref] || ref.slice(0, 8), color: COLORS[i % COLORS.length], runs: runs.map((run) => run.map((p) => [p[0], p[3]])) }))
);
const sections = computed(() => r.value.sections || []);
const station = ref(0);
watch(() => props.data, () => (station.value = 0));
const current_section = computed(() => sections.value[Math.min(station.value, sections.value.length - 1)] || {});
const section_series = computed(() =>
	Object.entries(current_section.value.surfaces || {}).map(([ref, runs], i) => ({ name: names.value[ref] || ref.slice(0, 8), color: COLORS[i % COLORS.length], runs }))
);

// A line chart of runs of [x, y] (gaps between runs), with axes and a legend.
const Chart = defineComponent({
	props: { series: Array, x_label: String, y_label: String, equal: Boolean, plain: Boolean }, // plain: not a section in metres (no exaggeration)
	setup(p) {
		const W = 560, H = 220, L = 56, R = 10, T = 10, B = 30;
		return () => {
			const pts = p.series.flatMap((s) => s.runs.flat());
			if (!pts.length) return h("p", { class: "muted" }, __("The alignment does not cross these surfaces."));
			let [x0, x1] = [Math.min(...pts.map((q) => q[0])), Math.max(...pts.map((q) => q[0]))];
			let [y0, y1] = [Math.min(...pts.map((q) => q[1])), Math.max(...pts.map((q) => q[1]))];
			if (x1 - x0 < 1e-9) x1 = x0 + 1;
			const pad = Math.max((y1 - y0) * 0.08, 0.5);
			y0 -= pad;
			y1 += pad;
			let sx = (W - L - R) / (x1 - x0);
			let sy = (H - T - B) / (y1 - y0);
			if (p.equal) {
				// true shape (no exaggeration): one scale, centred
				const k = Math.min(sx, sy);
				const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
				sx = sy = k;
				x0 = cx - (W - L - R) / k / 2;
				y0 = cy - (H - T - B) / k / 2;
				x1 = x0 + (W - L - R) / k;
				y1 = y0 + (H - T - B) / k;
			}
			const exaggeration = sy / sx;
			const X = (x) => L + (x - x0) * sx;
			const Y = (y) => H - B - (y - y0) * sy;
			const ticks = (a, b, count) => {
				const step = nice((b - a) / count);
				const out = [];
				for (let v = Math.ceil(a / step) * step; v <= b + 1e-9; v += step) out.push(v);
				return out;
			};
			const children = [];
			for (const v of ticks(x0, x1, 6)) children.push(h("line", { x1: X(v), x2: X(v), y1: T, y2: H - B, class: "grid" }), h("text", { x: X(v), y: H - B + 14, "text-anchor": "middle" }, round(v)));
			for (const v of ticks(y0, y1, 4)) children.push(h("line", { x1: L, x2: W - R, y1: Y(v), y2: Y(v), class: "grid" }), h("text", { x: L - 4, y: Y(v) + 4, "text-anchor": "end" }, round(v)));
			for (const s of p.series) for (const run of s.runs) if (run.length > 1) children.push(h("polyline", { points: run.map((q) => `${X(q[0]).toFixed(1)},${Y(q[1]).toFixed(1)}`).join(" "), stroke: s.color, fill: "none", "stroke-width": 1.6 }));
			children.push(h("text", { x: (L + W - R) / 2, y: H - 2, "text-anchor": "middle", class: "label" }, p.x_label));
			children.push(h("text", { x: 10, y: (T + H - B) / 2, "text-anchor": "middle", class: "label", transform: `rotate(-90 10 ${(T + H - B) / 2})` }, p.y_label));
			return h("div", [
				h("svg", { viewBox: `0 0 ${W} ${H}`, class: "cad-chart", role: "img" }, children),
				h(
					"div",
					{ class: "cad-row small" },
					[...p.series.map((s) => h("span", { class: "cad-legend" }, [h("i", { style: { background: s.color } }), s.name])), ...(p.plain ? [] : [h("span", { class: "muted" }, __("vertical × {0}", [exaggeration.toFixed(1)]))])]
				),
			]);
		};
	},
});

const Bars = defineComponent({
	props: { rows: Array, format: Function },
	setup(p) {
		return () => {
			const max = Math.max(...p.rows.map((r) => r.value), 1e-12);
			return h(
				"table",
				{ class: "cad-table small" },
				p.rows.map((row) =>
					h("tr", [h("td", row.label), h("td", { class: "cad-bar-cell" }, [h("i", { class: "cad-bar", style: { width: `${(100 * row.value) / max}%` } })]), h("td", { class: "mono" }, p.format(row.value))])
				)
			);
		};
	},
});

function nice(step) {
	const p = Math.pow(10, Math.floor(Math.log10(step || 1)));
	const m = step / p;
	return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * p;
}

function round(v) {
	return Math.abs(v) >= 100 ? v.toFixed(0) : Number(v.toFixed(2)).toString();
}
</script>
