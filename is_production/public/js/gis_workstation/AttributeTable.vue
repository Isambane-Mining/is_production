<template>
	<div class="gis-attribute-table">
		<header class="gis-section-head">
			<span>
				{{ __("Attribute Table") }}
				<template v-if="layer">— {{ layer.title }}</template>
			</span>
			<span class="muted small" v-if="layer">
				{{ __("{0} of {1} features in view", [rows.length, features.length]) }}
				<template v-if="layer.state === 'truncated'"> · {{ __("zoom in to see more") }}</template>
			</span>
			<input v-if="layer" v-model="search" class="gis-input gis-table-search" type="search" :placeholder="__('Filter…')" />
		</header>
		<p v-if="!layer" class="muted pad">{{ __("Select a layer in the Layers panel.") }}</p>
		<div v-else ref="scroller" class="gis-scroll">
			<table class="gis-table">
				<thead>
					<tr>
						<th v-for="c in columns" :key="c.key" :class="{ sorted: sort.key === c.key }" @click="sort_by(c.key)">
							{{ c.label }}<span v-if="sort.key === c.key">{{ sort.dir > 0 ? " ▲" : " ▼" }}</span>
						</th>
					</tr>
				</thead>
				<tbody>
					<tr
						v-for="r in rows"
						:key="r.id"
						:class="{ selected: r.id === selectedId, invalid: r.validation_status === 'Invalid' }"
						:data-id="r.id"
						@click="$emit('select', r.id)"
					>
						<td v-for="c in columns" :key="c.key" :class="{ mono: c.mono }">{{ display(r[c.key]) }}</td>
					</tr>
				</tbody>
			</table>
		</div>
	</div>
</template>

<script setup>
import { computed, nextTick, reactive, ref, watch } from "vue";

const __ = window.__;
const props = defineProps({ layer: Object, features: Array, selectedId: [Number, String, null] });
defineEmits(["select"]);

const search = ref("");
const sort = reactive({ key: "id", dir: 1 });
const scroller = ref(null);

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
	const keys = new Set();
	for (const r of all_rows.value) Object.keys(r).forEach((k) => k.startsWith("a:") && keys.add(k));
	return [
		{ key: "id", label: __("ID"), mono: true },
		{ key: "label", label: __("Label") },
		...[...keys].map((k) => ({ key: k, label: k.slice(2) })),
		{ key: "validation_status", label: __("Validation") },
	];
});

const rows = computed(() => {
	const q = search.value.trim().toLowerCase();
	const list = q ? all_rows.value.filter((r) => Object.values(r).some((v) => v != null && String(v).toLowerCase().includes(q))) : [...all_rows.value];
	const { key, dir } = sort;
	return list.sort((a, b) => compare(a[key], b[key]) * dir);
});

watch(
	() => props.selectedId,
	(id) =>
		nextTick(() => {
			const row = scroller.value && scroller.value.querySelector(`tr[data-id="${id}"]`);
			row && row.scrollIntoView({ block: "nearest" });
		})
);

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
