<template>
	<div class="gis-feature-panel">
		<header class="gis-section-head">
			<span>{{ editing ? __("Edit Feature") : __("Feature") }}</span>
			<button v-if="selection || editing" class="gis-icon-btn" :title="__('Clear selection (Esc)')" @click="$emit('clear')">✕</button>
		</header>
		<div class="gis-scroll pad">
			<!-- save / validation errors of the edit session -->
			<div v-if="errors && errors.length" class="gis-error-list">
				<b>{{ __("Problems") }}</b>
				<ul>
					<li v-for="e in errors" :key="e.ref + e.message">
						<span class="mono">{{ Number(e.ref) < 0 ? __("new") : "#" + e.ref }}</span>
						<span v-if="e.conflict" class="is-warning"> {{ __("conflict") }}</span>: {{ e.message }}
					</li>
				</ul>
			</div>

			<!-- editing: one feature -->
			<template v-if="editing && items.length === 1">
				<h4 class="gis-feature-title">
					{{ item.id < 0 ? __("New feature") : `#${item.id}` }}
					<span v-if="item.id > 0" class="muted small">· {{ __("revision {0}", [item.revision]) }}</span>
				</h4>
				<p v-if="item.validation_status === 'Invalid'" class="is-error small">{{ item.validation_message }}</p>
				<form class="gis-form" @submit.prevent>
					<label>
						<span>{{ __("Label") }}</span>
						<input class="gis-input" :value="item.label ?? ''" :disabled="!can_change" @change="$emit('set-label', item.id, $event.target.value)" />
					</label>
					<label v-for="a in schema.attributes" :key="a.field_name">
						<span>{{ a.label }}<b v-if="a.required" class="is-error"> *</b></span>
						<select
							v-if="a.allowed_values.length"
							class="gis-input"
							:value="item.attributes[a.field_name] ?? ''"
							:disabled="!can_change"
							@change="set(a, $event.target.value)"
						>
							<option value=""></option>
							<option v-for="v in a.allowed_values" :key="v" :value="v">{{ v }}</option>
						</select>
						<select
							v-else-if="a.data_type === 'Boolean'"
							class="gis-input"
							:value="bool_value(item.attributes[a.field_name])"
							:disabled="!can_change"
							@change="set(a, $event.target.value)"
						>
							<option value=""></option>
							<option value="true">{{ __("Yes") }}</option>
							<option value="false">{{ __("No") }}</option>
						</select>
						<input
							v-else
							class="gis-input"
							:type="input_type(a)"
							:step="a.data_type === 'Float' ? 'any' : a.data_type === 'Integer' ? '1' : undefined"
							:maxlength="a.max_length || undefined"
							:value="input_value(a, item.attributes[a.field_name])"
							:disabled="!can_change"
							@change="set(a, $event.target.value)"
						/>
					</label>
				</form>
				<p v-if="!schema.attributes.length" class="muted small">{{ __("This dataset has no attributes.") }}</p>
				<div class="gis-actions">
					<button @click="$emit('zoom')">{{ __("Zoom to Feature") }}</button>
					<a v-if="item.id > 0" class="gis-button" :href="`/desk/gis-feature/${item.id}`" target="_blank">{{ __("Open in Desk") }}</a>
				</div>
			</template>

			<!-- editing: several features -->
			<template v-else-if="editing && items.length > 1">
				<h4 class="gis-feature-title">{{ __("{0} features selected", [items.length]) }}</h4>
				<p class="muted small">{{ __("Set one field on all of them:") }}</p>
				<form class="gis-form" @submit.prevent="apply_bulk">
					<label>
						<span>{{ __("Field") }}</span>
						<select v-model="bulk.field" class="gis-input">
							<option value="__label">{{ __("Label") }}</option>
							<option v-for="a in schema.attributes" :key="a.field_name" :value="a.field_name">{{ a.label }}</option>
						</select>
					</label>
					<label>
						<span>{{ __("Value") }}</span>
						<input v-model="bulk.value" class="gis-input" :placeholder="__('empty clears it')" />
					</label>
					<button type="submit" :disabled="!can_change">{{ __("Apply to {0} features", [items.length]) }}</button>
				</form>
				<ul class="gis-id-list mono small">
					<li v-for="i in items" :key="i.id">{{ i.id < 0 ? __("new") : "#" + i.id }} {{ i.label || "" }}</li>
				</ul>
			</template>

			<!-- not editing -->
			<template v-else>
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
			</template>
		</div>
	</div>
</template>

<script setup>
import { computed, reactive } from "vue";
import { typed } from "./edit.js";

const __ = window.__;
const props = defineProps({
	selection: Object,
	canEdit: Boolean,
	editing: Boolean,
	schema: Object,
	items: { type: Array, default: () => [] },
	errors: { type: Array, default: () => [] },
});
const emit = defineEmits(["zoom", "clear", "set-attribute", "set-label", "bulk-set"]);

const bulk = reactive({ field: "__label", value: "" });
const item = computed(() => props.items[0]);
const attribute_keys = computed(() => (props.selection ? Object.keys(props.selection.attributes || {}) : []));
// new features need create permission; saved ones need write permission
const can_change = computed(() => {
	if (!props.schema) return false;
	return props.items.every((i) => (i.id < 0 ? props.schema.can_create : props.schema.can_write));
});

function set(attribute, raw) {
	emit("set-attribute", item.value.id, attribute.field_name, typed(attribute, raw));
}

function apply_bulk() {
	const attribute = props.schema.attributes.find((a) => a.field_name === bulk.field);
	emit("bulk-set", bulk.field, attribute ? typed(attribute, bulk.value) : bulk.value);
}

function input_type(a) {
	return { Integer: "number", Float: "number", Date: "date", Datetime: "datetime-local" }[a.data_type] || "text";
}

function input_value(a, value) {
	if (value === null || value === undefined) return "";
	if (a.data_type === "Datetime") return String(value).replace(" ", "T").slice(0, 16);
	return value;
}

function bool_value(value) {
	return value === true ? "true" : value === false ? "false" : "";
}

function display(value) {
	if (value === null || value === undefined || value === "") return "—";
	if (value === true) return __("Yes");
	if (value === false) return __("No");
	return String(value);
}
</script>
