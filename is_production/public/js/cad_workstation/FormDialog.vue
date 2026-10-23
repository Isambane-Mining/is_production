<template>
	<div class="cad-modal" @click.self="cancel">
		<form class="cad-dialog" @submit.prevent="ok">
			<div class="cad-row">
				<h3 class="cad-grow">{{ title }}</h3>
				<button type="button" class="cad-icon-btn" @click="cancel"><Icon name="close" /></button>
			</div>
			<p v-if="intro" class="muted small">{{ intro }}</p>
			<div class="cad-form-grid">
				<template v-for="f in visible_fields" :key="f.key">
					<label :for="'f-' + f.key">{{ f.label }}</label>
					<div>
						<select v-if="f.type === 'select'" :id="'f-' + f.key" v-model="values[f.key]">
							<option v-for="o in f.options" :key="o.value ?? o" :value="o.value ?? o">{{ o.label ?? o }}</option>
						</select>
						<div v-else-if="f.type === 'multiselect'" class="cad-multi">
							<label v-for="o in f.options" :key="o.value" class="cad-row small">
								<input v-model="values[f.key]" type="checkbox" :value="o.value" /> {{ o.label }}
							</label>
							<span v-if="!f.options.length" class="muted small">{{ __("Nothing to choose.") }}</span>
						</div>
						<label v-else-if="f.type === 'check'" class="cad-row"><input :id="'f-' + f.key" v-model="values[f.key]" type="checkbox" /> {{ f.check_label || "" }}</label>
						<input v-else-if="f.type === 'number'" :id="'f-' + f.key" v-model.number="values[f.key]" type="number" step="any" :required="f.required" />
						<input v-else :id="'f-' + f.key" v-model="values[f.key]" type="text" :required="f.required" />
						<div v-if="f.help" class="muted small">{{ f.help }}</div>
					</div>
				</template>
			</div>
			<p v-if="error" class="is-error small">{{ error }}</p>
			<div class="cad-row">
				<span class="cad-grow"></span>
				<button type="button" @click="cancel">{{ __("Cancel") }}</button>
				<button type="submit" class="cad-primary">{{ submit_label || __("OK") }}</button>
			</div>
		</form>
	</div>
</template>

<script setup>
import { computed, reactive, ref } from "vue";
import Icon from "./Icon.vue";

// fields: [{key, label, type: text|number|select|multiselect|check, options, default, required, help, show_if(values)}]
const props = defineProps({ title: String, intro: String, fields: Array, submit_label: String, check: Function });
const emit = defineEmits(["done"]);
const values = reactive(Object.fromEntries(props.fields.map((f) => [f.key, f.default ?? (f.type === "multiselect" ? [] : f.type === "check" ? false : "")])));
const error = ref("");
const visible_fields = computed(() => props.fields.filter((f) => !f.show_if || f.show_if(values)));

function ok() {
	const message = props.check ? props.check(values) : null;
	if (message) {
		error.value = message;
		return;
	}
	emit("done", JSON.parse(JSON.stringify(values)));
}

function cancel() {
	emit("done", null);
}
</script>
