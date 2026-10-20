<template>
	<div class="gis-attribute-table">
		<header class="gis-section-head">
			<span>
				{{ __("Attribute Table") }}
				<template v-if="layer">— {{ layer.title }}</template>
				<span v-if="editable" class="gis-badge">{{ __("editing: double-click a cell") }}</span>
			</span>
			<span class="muted small" v-if="layer">
				{{ __("{0} of {1} features in view", [rows.length, features.length]) }}
				<template v-if="layer.state === 'truncated'"> · {{ __("zoom in to see more") }}</template>
				<template v-if="selectedIds.length"> · {{ __("{0} selected", [selectedIds.length]) }}</template>
			</span>
			<span class="gis-table-tools">
				<template v-if="layer">
					<label class="gis-check small"><input v-model="only_selected" type="checkbox" /> {{ __("Selected only") }}</label>
					<input v-model="search" class="gis-input gis-table-search" type="search" :placeholder="__('Filter…')" />
				</template>
				<button class="gis-icon-btn" :title="__('Close panel')" @click="$emit('close')">✕</button>
			</span>
		</header>
		<p v-if="!layer" class="muted pad">{{ __("Select a layer in the Layers panel.") }}</p>
		<div v-else ref="scroller" class="gis-scroll">
			<table class="gis-table">
				<thead>
					<tr>
						<th v-for="c in columns" :key="c.key" :class="{ sorted: sort.key === c.key }" @click="sort_by(c.key)">
							{{ c.label }}<b v-if="c.required" class="is-error">*</b><span v-if="sort.key === c.key">{{ sort.dir > 0 ? " ▲" : " ▼" }}</span>
						</th>
					</tr>
				</thead>
				<tbody>
					<tr
						v-for="r in rows"
						:key="r.id"
						:class="{ selected: selected_set.has(r.id), invalid: r.validation_status === 'Invalid', added: r.id < 0 }"
						:data-id="r.id"
						@click="$emit('select', r.id, $event.shiftKey || $event.ctrlKey || $event.metaKey)"
					>
						<td
							v-for="c in columns"
							:key="c.key"
							:class="{ mono: c.mono, editable: editable && c.editable }"
							@dblclick.stop="start_edit(r, c)"
						>
							<input
								v-if="cell && cell.id === r.id && cell.key === c.key"
								ref="cell_input"
								class="gis-cell-input"
								:value="cell.value"
								@click.stop
								@keydown.enter.prevent="commit($event.target.value)"
								@keydown.esc.prevent.stop="cell = null"
								@blur="commit($event.target.value)"
							/>
							<template v-else>{{ c.key === "id" && r.id < 0 ? __("new") : display(r[c.key]) }}</template>
						</td>
					</tr>
				</tbody>
			</table>
		</div>
	</div>
</template>

<script setup>
import { computed, nextTick, reactive, ref, watch } from "vue";

const __ = window.__;
const props = defineProps({
	layer: Object,
	features: Array,
	selectedIds: { type: Array, default: () => [] },
	editable: Boolean,
	schema: Object,
});
const emit = defineEmits(["select", "edit", "close"]);

const search = ref("");
const only_selected = ref(false);
const sort = reactive({ key: "id", dir: 1 });
const scroller = ref(null);
const cell = ref(null); // { id, key, value } being edited
const cell_input = ref(null);
const selected_set = computed(() => new Set(props.selectedIds));

// Features are OpenLayers features; the table reads plain rows from them.
const all_rows = computed(() =>
	(props.features || []).map((f) => ({
		id: f.getId(),
		label: f.get("label"),
		validation_status: f.get("validation_status"),
		...prefix(f.get("attributes") || {}),
	}))
);

const columns = computed(() => {
	let attributes;
	if (props.schema) {
		attributes = props.schema.attributes.map((a) => ({ key: `a:${a.field_name}`, label: a.label, required: a.required, editable: true }));
	} else {
		const keys = new Set();
		for (const r of all_rows.value) Object.keys(r).forEach((k) => k.startsWith("a:") && keys.add(k));
		attributes = [...keys].map((k) => ({ key: k, label: k.slice(2) }));
	}
	return [
		{ key: "id", label: __("ID"), mono: true },
		{ key: "label", label: __("Label"), editable: true },
		...attributes,
		{ key: "validation_status", label: __("Validation") },
	];
});

const rows = computed(() => {
	const q = search.value.trim().toLowerCase();
	let list = [...all_rows.value];
	if (only_selected.value) list = list.filter((r) => selected_set.value.has(r.id));
	if (q) list = list.filter((r) => Object.values(r).some((v) => v != null && String(v).toLowerCase().includes(q)));
	const { key, dir } = sort;
	return list.sort((a, b) => compare(a[key], b[key]) * dir);
});

watch(
	() => props.selectedIds[props.selectedIds.length - 1],
	(id) =>
		nextTick(() => {
			const row = scroller.value && scroller.value.querySelector(`tr[data-id="${id}"]`);
			row && row.scrollIntoView({ block: "nearest" });
		})
);

function start_edit(row, column) {
	if (!props.editable || !column.editable) return;
	const value = row[column.key];
	cell.value = { id: row.id, key: column.key, value: value === null || value === undefined ? "" : String(value) };
	nextTick(() => {
		const input = Array.isArray(cell_input.value) ? cell_input.value[0] : cell_input.value;
		input && (input.focus(), input.select());
	});
}

function commit(value) {
	const editing = cell.value;
	if (!editing) return;
	cell.value = null;
	if (value === editing.value) return;
	emit("edit", editing.id, editing.key === "label" ? "label" : editing.key.slice(2), value);
}

function prefix(attributes) {
	const out = {};
	for (const k in attributes) out[`a:${k}`] = attributes[k];
	return out;
}

function sort_by(key) {
	sort.dir = sort.key === key ? -sort.dir : 1;
	sort.key = key;
}

function compare(a, b) {
	if (a == null && b == null) return 0;
	if (a == null) return 1;
	if (b == null) return -1;
	return typeof a === "number" && typeof b === "number" ? a - b : String(a).localeCompare(String(b), undefined, { numeric: true });
}

function display(value) {
	if (value === null || value === undefined) return "";
	if (value === true) return "✓";
	if (value === false) return "✗";
	return String(value);
}
</script>
