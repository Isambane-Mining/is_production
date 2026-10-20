<template>
	<!-- A dock area holding panels as tabs (QGIS: Processing Toolbox / Identify Results on the right). -->
	<section class="gis-dock" :class="'gis-dock-' + side">
		<header class="gis-dock-head">
			<button
				v-for="p in panels"
				:key="p.id"
				class="gis-dock-tab"
				:class="{ active: p.id === active }"
				@click="$emit('activate', p.id)"
			>
				{{ p.title }}
			</button>
			<span class="gis-spacer"></span>
			<button class="gis-icon-btn" :title="__('Close panel')" @click="$emit('close', active)"><Icon name="close" :size="14" /></button>
		</header>
		<div class="gis-dock-body">
			<slot :name="active" />
		</div>
	</section>
</template>

<script setup>
import Icon from "./Icon.vue";

const __ = window.__;
defineProps({ side: String, panels: Array, active: String });
defineEmits(["activate", "close"]);
</script>
