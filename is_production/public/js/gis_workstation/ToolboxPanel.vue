<template>
	<!-- QGIS Processing Toolbox, docked: providers › groups › algorithms; double-click opens the algorithm. -->
	<div class="gis-toolbox-panel">
		<div class="gis-panel-tools">
			<input v-model="search" class="gis-input" type="search" :placeholder="__('Search…')" />
		</div>
		<div class="gis-panel-tools small">
			<select v-model="provider" class="gis-input">
				<option value="">{{ __("All providers") }}</option>
				<option v-for="p in providers" :key="p.id" :value="p.id">{{ p.name }}</option>
			</select>
			<label class="gis-check" :title="__('Also list algorithms that cannot run from the web')"><input v-model="show_unavailable" type="checkbox" /> {{ __("All") }}</label>
		</div>
		<p v-if="loading" class="muted pad">{{ __("Loading algorithms…") }}</p>
		<div class="gis-scroll">
			<details v-for="prov in tree" :key="prov.id" :open="!!search || prov.id === 'native'" class="gis-tree-group">
				<summary><Icon name="processing" :size="14" /> {{ prov.name }} <span class="muted">({{ prov.count }})</span></summary>
				<details v-for="g in prov.groups" :key="g.name" :open="!!search" class="gis-tree-subgroup">
					<summary>{{ g.name }} <span class="muted">({{ g.items.length }})</span></summary>
					<div
						v-for="a in g.items"
						:key="a.id"
						class="gis-tree-item gis-alg-item"
						:class="{ unavailable: !a.supported }"
						:title="a.supported ? `${a.id} — ${a.description || ''}` : a.unsupported_reason"
						@dblclick="a.supported && $emit('open', a.id)"
						@keydown.enter="a.supported && $emit('open', a.id)"
						tabindex="0"
					>
						{{ a.name }}<span v-if="a.deprecated" class="muted small"> · {{ __("deprecated") }}</span>
					</div>
				</details>
			</details>
		</div>
		<p class="muted small pad">{{ __("Double-click an algorithm to run it. QGIS {0}.", [qgisVersion || ""]) }}</p>
	</div>
</template>

<script setup>
import { computed, ref } from "vue";
import Icon from "./Icon.vue";

const __ = window.__;
const props = defineProps({ algorithms: { type: Array, default: () => [] }, loading: Boolean, qgisVersion: String });
defineEmits(["open"]);
const search = ref("");
const provider = ref("");
const show_unavailable = ref(false);

const providers = computed(() => {
	const seen = new Map();
	for (const a of props.algorithms) seen.set(a.provider, a.provider_name);
	return [...seen].map(([id, name]) => ({ id, name }));
});

const tree = computed(() => {
	const q = search.value.trim().toLowerCase();
	const out = new Map();
	for (const a of props.algorithms) {
		if (provider.value && a.provider !== provider.value) continue;
		if (!show_unavailable.value && !a.supported) continue;
		if (q && !`${a.name} ${a.id} ${a.group} ${(a.tags || []).join(" ")} ${a.description}`.toLowerCase().includes(q)) continue;
		if (!out.has(a.provider)) out.set(a.provider, { id: a.provider, name: a.provider_name, groups: new Map(), count: 0 });
		const prov = out.get(a.provider);
		const group = a.group || __("Other");
		if (!prov.groups.has(group)) prov.groups.set(group, []);
		prov.groups.get(group).push(a);
		prov.count++;
	}
	return [...out.values()]
		.sort((a, b) => (a.id === "native" ? -1 : b.id === "native" ? 1 : a.name.localeCompare(b.name)))
		.map((p) => ({
			...p,
			groups: [...p.groups].sort(([a], [b]) => a.localeCompare(b)).map(([name, items]) => ({ name, items: items.sort((x, y) => x.name.localeCompare(y.name)) })),
		}));
});
</script>
