<template>
	<div class="cad-panel-body">
		<div class="cad-row">
			<input v-model="new_name" class="cad-grow" :placeholder="__('New layer name')" :disabled="!can_edit" @keydown.enter="add" />
			<button :disabled="!can_edit || !new_name.trim()" @click="add">{{ __("Add") }}</button>
		</div>
		<div class="cad-list">
			<div
				v-for="layer in layers"
				:key="layer.name || layer.layer_name"
				class="cad-layer-row"
				:class="{ active: current === key(layer) }"
				:style="{ paddingLeft: 6 + depth(layer) * 12 + 'px' }"
				@click="$emit('current', key(layer))"
			>
				<input
					type="color"
					:value="layer.color || '#e0e0e0'"
					:disabled="!can_edit"
					:title="__('Colour')"
					@click.stop
					@change="change(layer, { color: $event.target.value })"
				/>
				<span class="cad-grow cad-ellipsis" :title="layer.description || ''">
					{{ layer.layer_name }}
					<span v-if="layer.is_group" class="muted small">({{ __("group") }})</span>
				</span>
				<button class="cad-icon-btn" :title="layer.visible ? __('Hide') : __('Show')" @click.stop="change(layer, { visible: layer.visible ? 0 : 1 }, true)">
					<Icon :name="layer.visible ? 'eye' : 'eye-off'" :size="15" />
				</button>
				<button class="cad-icon-btn" :disabled="!can_edit" :title="layer.locked ? __('Unlock') : __('Lock')" @click.stop="change(layer, { locked: layer.locked ? 0 : 1 })">
					<Icon :name="layer.locked ? 'lock' : 'lock-open'" :size="15" />
				</button>
			</div>
		</div>
		<p class="muted small pad">{{ __("Click a layer to draw on it. Layer changes are saved with the model.") }}</p>
	</div>
</template>

<script setup>
import { ref } from "vue";
import Icon from "./Icon.vue";

const props = defineProps({ layers: Array, current: String, can_edit: Boolean });
const emit = defineEmits(["change", "current"]);
const new_name = ref("");

function key(layer) {
	return layer.name || layer.layer_name;
}

function depth(layer) {
	let d = 0;
	let parent = layer.parent_layer;
	while (parent && d < 8) {
		const p = props.layers.find((l) => l.name === parent);
		parent = p && p.parent_layer;
		d++;
	}
	return d;
}

function change(layer, values, view_only = false) {
	emit("change", key(layer), values, view_only);
}

function add() {
	const name = new_name.value.trim();
	if (!name) return;
	if (props.layers.some((l) => l.layer_name === name)) return;
	emit("change", name, { layer_name: name, color: "#e0e0e0", visible: 1, locked: 0, display_order: props.layers.length });
	emit("current", name);
	new_name.value = "";
}
</script>
