<template>
	<div class="gis-feature-panel">
		<header class="gis-section-head">
			<span>{{ __("Feature") }}</span>
			<button v-if="selection" class="gis-icon-btn" :title="__('Clear selection (Esc)')" @click="$emit('clear')">✕</button>
		</header>
		<div class="gis-scroll pad">
			<p v-if="!selection" class="muted">{{ __("Click a feature on the map, or a row in the attribute table.") }}</p>
			<template v-else>
				<h4 class="gis-feature-title">{{ selection.label || `#${selection.id}` }}</h4>
				<table class="gis-kv">
					<tbody>
						<tr><th>{{ __("Dataset") }}</th><td>{{ selection.dataset_title }}</td></tr>
						<tr><th>{{ __("Feature ID") }}</th><td class="mono">{{ selection.id }}</td></tr>
						<tr><th>{{ __("Geometry") }}</th><td>{{ selection.geometry_type }}</td></tr>
						<tr><th>{{ __("CRS") }}</th><td class="mono">{{ selection.crs }}</td></tr>
						<tr><th>{{ __("Revision") }}</th><td>{{ selection.revision }}</td></tr>
						<tr>
							<th>{{ __("Validation") }}</th>
							<td :class="selection.validation_status === 'Invalid' ? 'is-error' : ''">{{ selection.validation_status }}</td>
						</tr>
					</tbody>
				</table>
				<h5 class="gis-subhead">{{ __("Attributes") }}</h5>
				<p v-if="!attribute_keys.length" class="muted small">{{ __("This dataset has no attributes.") }}</p>
				<table v-else class="gis-kv">
					<tbody>
						<tr v-for="key in attribute_keys" :key="key">
							<th>{{ key }}</th>
							<td>{{ display(selection.attributes[key]) }}</td>
						</tr>
					</tbody>
				</table>
				<div class="gis-actions">
					<button @click="$emit('zoom')">{{ __("Zoom to Feature") }}</button>
					<a class="gis-button" :href="`/desk/gis-feature/${selection.id}`" target="_blank">
						{{ canEdit ? __("Edit in Desk") : __("Open in Desk") }}
					</a>
				</div>
			</template>
		</div>
	</div>
</template>

<script setup>
import { computed } from "vue";

const __ = window.__;
const props = defineProps({ selection: Object, canEdit: Boolean });
defineEmits(["zoom", "clear"]);

const attribute_keys = computed(() => (props.selection ? Object.keys(props.selection.attributes || {}) : []));

function display(value) {
	if (value === null || value === undefined || value === "") return "—";
	if (value === true) return __("Yes");
	if (value === false) return __("No");
	return String(value);
}
</script>
