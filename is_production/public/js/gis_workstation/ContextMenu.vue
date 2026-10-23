<template>
	<!-- Right-click menu (layer tree, browser). items: [{label, icon, action, disabled, items}] or "-" -->
	<div v-if="menu" class="gis-context-backdrop" @click="close" @contextmenu.prevent="close">
		<div class="gis-menu-items gis-context-menu" :style="{ left: menu.x + 'px', top: menu.y + 'px' }" @click.stop>
			<template v-for="(item, i) in menu.items" :key="i">
				<hr v-if="item === '-'" />
				<div v-else-if="item.items" class="gis-menu-row" @mouseenter="sub = i">
					<button><span class="gis-menu-check"></span><Icon v-if="item.icon" :name="item.icon" :size="15" /><span v-else class="gis-menu-icon-space"></span><span class="gis-menu-label">{{ item.label }}</span><span class="gis-menu-hint">▸</span></button>
					<div v-if="sub === i" class="gis-menu-items gis-submenu">
						<button v-for="(child, j) in item.items" :key="j" :disabled="child.disabled" @click="run(child)">
							<span class="gis-menu-check">{{ child.checked ? "✓" : "" }}</span><span class="gis-menu-icon-space"></span><span class="gis-menu-label">{{ child.label }}</span>
						</button>
					</div>
				</div>
				<button v-else :disabled="item.disabled" @mouseenter="sub = null" @click="run(item)">
					<span class="gis-menu-check">{{ item.checked ? "✓" : "" }}</span>
					<Icon v-if="item.icon" :name="item.icon" :size="15" /><span v-else class="gis-menu-icon-space"></span>
					<span class="gis-menu-label">{{ item.label }}</span>
					<span class="gis-menu-hint">{{ item.shortcut || "" }}</span>
				</button>
			</template>
		</div>
	</div>
</template>

<script setup>
import { ref } from "vue";
import Icon from "./Icon.vue";

const menu = ref(null);
const sub = ref(null);

function show(event, items) {
	const width = 240;
	const height = items.length * 28;
	menu.value = {
		x: Math.min(event.clientX, window.innerWidth - width - 8),
		y: Math.min(event.clientY, window.innerHeight - height - 8),
		items,
	};
	sub.value = null;
}

function close() {
	menu.value = null;
}

function run(item) {
	close();
	if (!item.disabled && item.action) item.action();
}

defineExpose({ show, close });
</script>
