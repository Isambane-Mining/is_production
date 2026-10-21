<script setup>
// An icon: a QGIS theme icon (as an image, see qgis_icons.js) for GIS actions, or a Lucide SVG for interface chrome.
import { h } from "vue";
import { ICONS } from "./icons.js";
import { QGIS_ICON_BASE, QGIS_ICONS } from "./qgis_icons.js";

const props = defineProps({ name: String, size: { type: Number, default: 20 } });

function render() {
	const qgis = QGIS_ICONS[props.name];
	if (qgis) {
		return h("img", { src: QGIS_ICON_BASE + qgis, width: props.size, height: props.size, class: "gis-icon gis-qgis-icon", alt: "", draggable: "false" });
	}
	const node = ICONS[props.name];
	if (!node) return h("span", { class: "gis-icon-missing" }, "?");
	const [, attrs, children] = node;
	return h(
		"svg",
		{ ...attrs, width: props.size, height: props.size, class: "gis-icon", "aria-hidden": "true", "stroke-width": 1.75 },
		(children || []).map(([tag, childAttrs]) => h(tag, childAttrs))
	);
}
</script>

<template>
	<component :is="render" />
</template>
