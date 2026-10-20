<script setup>
// One Lucide icon as inline SVG (see icons.js).
import { h } from "vue";
import { ICONS } from "./icons.js";

const props = defineProps({ name: String, size: { type: Number, default: 18 } });

function render() {
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
