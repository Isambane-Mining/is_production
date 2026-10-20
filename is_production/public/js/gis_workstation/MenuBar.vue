<template>
	<nav class="gis-menubar" @mouseleave="open = null">
		<span class="gis-brand">{{ __("GIS Workstation") }}</span>
		<div v-for="menu in menus" :key="menu.label" class="gis-menu">
			<button
				class="gis-menu-title"
				:class="{ open: open === menu.label }"
				@click="open = open === menu.label ? null : menu.label"
				@mouseenter="open && (open = menu.label)"
			>
				{{ menu.label }}
			</button>
			<MenuItems v-if="open === menu.label" :items="menu.items" @run="run" />
		</div>
		<span class="gis-project-title" :title="projectTitle">{{ projectTitle }}</span>
		<span class="gis-spacer"></span>
		<a class="gis-menubar-link" href="/desk/gis-dataset" target="_blank">{{ __("Desk") }}</a>
	</nav>
</template>

<script setup>
import { defineComponent, h, ref } from "vue";
import Icon from "./Icon.vue";

const __ = window.__;
defineProps({ menus: Array, projectTitle: String });
const open = ref(null);

function run(item) {
	open.value = null;
	if (!item.disabled && item.action) item.action();
}

defineExpose({ close: () => (open.value = null) });

// A menu's items; an item with `items` opens a submenu on hover (QGIS: View → Panels, Vector → …).
const MenuItems = defineComponent({
	name: "MenuItems",
	props: { items: Array, sub: Boolean },
	emits: ["run"],
	setup(props, { emit }) {
		const hover = ref(null);
		return () =>
			h(
				"div",
				{ class: ["gis-menu-items", props.sub && "gis-submenu"] },
				props.items.map((item, i) => {
					if (item === "-") return h("hr", { key: i });
					const children = [
						h("span", { class: "gis-menu-check" }, item.checked ? "✓" : ""),
						item.icon ? h(Icon, { name: item.icon, size: 15 }) : h("span", { class: "gis-menu-icon-space" }),
						h("span", { class: "gis-menu-label" }, item.label),
						h("span", { class: "gis-menu-hint" }, item.items ? "▸" : item.shortcut || item.hint || ""),
					];
					return h(
						"div",
						{ key: i, class: "gis-menu-row", onMouseenter: () => (hover.value = i) },
						[
							h(
								"button",
								{
									disabled: item.disabled,
									title: item.title || item.hint || "",
									onClick: () => (item.items ? (hover.value = i) : emit("run", item)),
								},
								children
							),
							item.items && hover.value === i ? h(MenuItems, { items: item.items, sub: true, onRun: (x) => emit("run", x) }) : null,
						]
					);
				})
			);
	},
});
</script>
