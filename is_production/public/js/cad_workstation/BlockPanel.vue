<template>
	<div class="cad-panel-body">
		<div class="cad-row">
			<input v-model="filter" class="cad-grow" :placeholder="__('Filter blocks…')" />
		</div>
		<p class="muted small">{{ __("Select objects and use Create Block to make one. Click a block, then Insert.") }}</p>
		<div class="cad-list">
			<div
				v-for="b in shown"
				:key="b.block_name"
				class="cad-layer-row"
				:class="{ active: current === b.block_name }"
				@click="$emit('current', b.block_name)"
				@dblclick="$emit('insert', b.block_name)"
			>
				<span class="cad-grow cad-ellipsis">
					<b>{{ b.block_name }}</b>
					<span class="muted small"> · {{ (b.entities || []).length }} {{ __("entities") }}</span>
					<span v-if="uses[b.block_name]" class="muted small"> · {{ uses[b.block_name] }}× {{ __("used") }}</span>
				</span>
				<button class="small" :disabled="!can_edit" @click.stop="$emit('insert', b.block_name)">{{ __("Insert") }}</button>
			</div>
			<div v-if="!shown.length" class="muted pad">{{ __("No blocks yet.") }}</div>
		</div>
		<div class="cad-row">
			<button :disabled="!can_edit || !has_selection" @click="$emit('create')">{{ __("Create Block") }}</button>
		</div>
	</div>
</template>

<script setup>
import { computed, ref } from "vue";

const props = defineProps({ blocks: Array, current: String, can_edit: Boolean, has_selection: Boolean, uses: Object });
defineEmits(["current", "insert", "create"]);
const filter = ref("");
const shown = computed(() => props.blocks.filter((b) => !filter.value || b.block_name.toLowerCase().includes(filter.value.toLowerCase())));
</script>
