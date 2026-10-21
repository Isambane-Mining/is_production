<template>
	<nav class="cad-menubar" @mouseleave="hover_open && (open = null)">
		<div v-for="menu in menus" :key="menu.label" class="cad-menu">
			<button
				class="cad-menu-title"
				:class="{ open: open === menu.label }"
				@click="toggle(menu.label)"
				@mouseenter="open && (open = menu.label)"
			>
				{{ menu.label }}
			</button>
			<div v-if="open === menu.label" class="cad-menu-items">
				<template v-for="(item, i) in menu.items" :key="i">
					<div v-if="item === '-'" class="cad-menu-sep"></div>
					<button
						v-else
						class="cad-menu-item"
						:disabled="item.disabled"
						:title="item.hint || ''"
						@click="run(item)"
					>
						<span class="cad-menu-check">{{ item.checked ? "✓" : "" }}</span>
						<span class="cad-menu-label">{{ item.label }}</span>
						<span class="cad-menu-key">{{ item.phase ? item.phase : item.key || "" }}</span>
					</button>
				</template>
			</div>
		</div>
		<div class="cad-spacer"></div>
		<slot></slot>
	</nav>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from "vue";

defineProps({ menus: { type: Array, required: true } });
const emit = defineEmits(["command"]);
const open = ref(null);
const hover_open = false;

function toggle(label) {
	open.value = open.value === label ? null : label;
}

function run(item) {
	open.value = null;
	if (!item.disabled) emit("command", item.command, item.args);
}

function close_outside(event) {
	if (!event.target.closest(".cad-menubar")) open.value = null;
}

onMounted(() => document.addEventListener("mousedown", close_outside));
onBeforeUnmount(() => document.removeEventListener("mousedown", close_outside));
</script>
