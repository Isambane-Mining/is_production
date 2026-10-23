<template>
	<div class="cad-panel-body">
		<div class="cad-row pad">
			<button v-if="can_edit && working" class="cad-primary" :disabled="unsaved" :title="unsaved ? __('Save first') : ''" @click="submit">
				{{ __("Submit {0} for Review", [working.revision_label]) }}
			</button>
			<span v-else-if="in_review" class="muted small">{{ __("{0} is in review: editing is locked until it is approved or rejected.", [in_review]) }}</span>
			<span class="cad-grow"></span>
			<button :disabled="revisions.filter((r) => r.geometry_checksum).length < 1" :title="__('What changed between two revisions')" @click="$emit('compare')">{{ __("Compare…") }}</button>
		</div>
		<div class="cad-list">
			<div v-for="r in revisions" :key="r.name" class="cad-revision">
				<div class="cad-row">
					<b>{{ r.revision_label }}</b>
					<span class="cad-chip" :class="chip(r.revision_status)">{{ __(r.revision_status) }}</span>
					<span class="cad-grow"></span>
					<span class="muted small">{{ short_date(r.submitted_on || r.creation) }}</span>
				</div>
				<div v-if="r.revision_description" class="small">{{ r.revision_description }}</div>
				<div class="muted small">
					<span v-if="r.parent_revision">{{ __("from {0}", [r.parent_revision.split("-").pop()]) }} · </span>
					<span v-if="r.entity_count">{{ r.entity_count }} {{ __("entities") }} · </span>
					<span v-if="r.geometry_checksum" class="mono" :title="r.geometry_checksum">SHA-256 {{ r.geometry_checksum.slice(0, 12) }}…</span>
				</div>
				<div v-if="r.approved_by" class="muted small">
					{{ r.revision_status === "Rejected" ? __("Rejected by") : __("Approved by") }} {{ r.approved_by }}
					<span v-if="r.approval_notes">: {{ r.approval_notes }}</span>
				</div>
				<div class="cad-row">
					<template v-if="r.revision_status === 'In Review' && can_approve">
						<button class="cad-primary" @click="$emit('approve', r.name)">{{ __("Approve") }}</button>
						<button @click="$emit('reject', r.name)">{{ __("Reject") }}</button>
					</template>
					<button v-if="r.geometry_checksum && can_edit && !in_review" :disabled="unsaved" @click="$emit('restore', r.name)">
						{{ __("Restore as New Revision") }}
					</button>
				</div>
			</div>
		</div>
	</div>
</template>

<script setup>
import { computed } from "vue";

const props = defineProps({ revisions: Array, can_edit: Boolean, can_approve: Boolean, in_review: String, unsaved: Boolean });
const emit = defineEmits(["submit", "approve", "reject", "restore", "compare"]);
const working = computed(() => props.revisions.find((r) => r.revision_status === "Working"));

function chip(status) {
	return { Approved: "ok", "In Review": "info", Rejected: "error", Working: "warning" }[status] || "";
}

function short_date(value) {
	return value ? String(value).slice(0, 16) : "";
}

function submit() {
	emit("submit", working.value.name);
}
</script>
