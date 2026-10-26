<script setup>
// A Lucide icon (https://lucide.dev, ISC licence) as an inline SVG.
import { h } from "vue";
import { ICONS } from "./icons.js";

const props = defineProps({ name: String, size: { type: Number, default: 18 } });

function render() {
	const node = ICONS[props.name];
	if (!node) return h("span", { class: "cad-icon-missing" }, "?");
	const [, attrs, children] = node;
	return h(
		"svg",
		{ ...attrs, width: props.size, height: props.size, class: "cad-icon", "aria-hidden": "true", "stroke-width": 1.75 },
		(children || []).map(([tag, childAttrs]) => h(tag, childAttrs))
	);
}
</script>

<template>
	<component :is="render" />
</template>
