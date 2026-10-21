<template>
	<div class="cad-references">
		<h4 class="cad-subhead">{{ __("External References") }}</h4>
		<div v-for="(r, i) in references" :key="i" class="cad-layer-row">
			<button class="cad-icon-btn" :title="r.visible ? __('Hide') : __('Show')" @click="update(i, { visible: r.visible ? 0 : 1 })">
				<Icon :name="r.visible ? 'eye' : 'eye-off'" :size="15" />
			</button>
			<span class="cad-grow cad-ellipsis" :title="r.reference_revision || __('live working state')">
				{{ r.reference_model }}
				<span class="muted small">{{ r.reference_revision ? r.reference_revision.split("-").pop() : __("live") }}</span>
			</span>
			<button class="cad-icon-btn" :disabled="!can_edit" :title="__('Remove')" @click="remove(i)"><Icon name="close" :size="15" /></button>
		</div>
		<div v-if="can_edit" class="cad-row pad">
			<select v-model="choice" class="cad-grow">
				<option value="">{{ __("Reference another model…") }}</option>
				<option v-for="m in candidates" :key="m.name" :value="m.name">{{ m.model_name }} ({{ m.name }})</option>
			</select>
			<select v-if="choice" v-model="revision">
				<option value="">{{ __("Live") }}</option>
				<option v-for="r in revisions" :key="r.name" :value="r.name">{{ r.revision_label }} · {{ __(r.revision_status) }}</option>
			</select>
			<button :disabled="!choice" @click="add">{{ __("Add") }}</button>
		</div>
		<p class="muted small pad">{{ __("Referenced models are drawn faded and read-only; you can snap to them. Saved with the model.") }}</p>
	</div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { call } from "./api";
import Icon from "./Icon.vue";

const props = defineProps({ references: Array, model: String, can_edit: Boolean });
const emit = defineEmits(["change", "error"]);
const models = ref([]);
const choice = ref("");
const revision = ref("");
const revisions = ref([]);

const candidates = computed(() => models.value.filter((m) => m.name !== props.model && !props.references.some((r) => r.reference_model === m.name)));

watch(choice, async (name) => {
	revision.value = "";
	revisions.value = name ? await call("list_revisions_of", { model: name }).catch(() => []) : [];
});

function update(i, values) {
	emit("change", props.references.map((r, k) => (k === i ? { ...r, ...values } : r)));
}

function remove(i) {
	emit("change", props.references.filter((_r, k) => k !== i));
}

function add() {
	emit("change", [...props.references, { reference_model: choice.value, reference_revision: revision.value || null, offset_x: 0, offset_y: 0, offset_z: 0, visible: 1 }]);
	choice.value = "";
}

onMounted(async () => {
	try {
		models.value = await call("list_models");
	} catch (e) {
		emit("error", e.message);
	}
});
</script>
