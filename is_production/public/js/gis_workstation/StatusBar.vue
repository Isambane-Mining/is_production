<template>
	<!-- QGIS-style status bar: message, Coordinate, Scale, Rotation, CRS, QGIS health -->
	<footer class="gis-statusbar">
		<span class="gis-status-message" :title="message">{{ message }}</span>
		<span class="gis-spacer"></span>
		<span v-if="busy" class="gis-status-item">{{ __("Loading…") }}</span>
		<label class="gis-status-item" :title="__('Pointer position; type x, y and press Enter to go there')">
			{{ __("Coordinate") }}
			<input class="gis-status-field mono gis-coordinate" :value="editingCoordinate ? coordinateDraft : coordinate" @focus="startCoordinate" @input="coordinateDraft = $event.target.value" @keydown.enter="goCoordinate" @blur="editingCoordinate = false" />
		</label>
		<label class="gis-status-item" :title="__('Map scale; choose or type 1:N')">
			{{ __("Scale") }}
			<input class="gis-status-field mono gis-scale" list="gis-scales" :value="scaleText" @change="setScale($event.target.value)" @keydown.enter="$event.target.blur()" />
			<datalist id="gis-scales">
				<option v-for="s in SCALES" :key="s" :value="`1:${s.toLocaleString()}`"></option>
			</datalist>
		</label>
		<label class="gis-status-item" :title="__('Map rotation (degrees clockwise)')">
			{{ __("Rotation") }}
			<input class="gis-status-field mono gis-rotation" type="number" step="1" :value="rotation" @change="$emit('rotation', Number($event.target.value) || 0)" /> °
		</label>
		<span class="gis-status-item gis-crs-picker">
			<button class="gis-crs-button" :title="__('Coordinate reference system of the readout')" @click="crsOpen = !crsOpen">
				<Icon name="globe" :size="14" /> {{ crs }}
			</button>
			<div v-if="crsOpen" class="gis-menu-items gis-crs-menu" @mouseleave="crsOpen = false">
				<button v-for="code in crsOptions" :key="code" @click="choose(code)">
					<span class="gis-menu-check">{{ code === crs ? "✓" : "" }}</span><span class="gis-menu-label">{{ crsLabel(code) }}</span>
				</button>
				<hr />
				<button @click="other"><span class="gis-menu-check"></span><span class="gis-menu-label">{{ __("Other EPSG code…") }}</span></button>
			</div>
		</span>
		<a
			class="gis-status-item gis-health"
			:class="'is-' + (health.status || 'unknown').toLowerCase()"
			:href="healthLink"
			target="_blank"
			:title="healthTitle"
		>
			● QGIS {{ health.qgis_version || "" }} · {{ health.status ? __(health.status) : __("Not checked") }}
		</a>
	</footer>
</template>

<script setup>
import { computed, ref } from "vue";
import Icon from "./Icon.vue";

const __ = window.__;
const SCALES = [500, 1000, 2500, 5000, 10000, 25000, 50000, 100000, 250000, 500000, 1000000];
const props = defineProps({
	message: String,
	busy: Boolean,
	coordinate: String,
	scale: Number,
	rotation: Number,
	crs: String,
	crsOptions: Array,
	crsLabel: Function,
	health: Object,
	healthLink: String,
});
const emit = defineEmits(["go", "scale", "rotation", "crs"]);

const crsOpen = ref(false);
const editingCoordinate = ref(false);
const coordinateDraft = ref("");
const scaleText = computed(() => (props.scale ? `1:${Math.round(props.scale).toLocaleString()}` : ""));
const healthTitle = computed(() => (props.health.status ? `${props.health.summary || ""}\n${__("Checked")}: ${props.health.checked_on || ""}` : __("Run Check Dependencies in GIS Settings.")));

function startCoordinate() {
	editingCoordinate.value = true;
	coordinateDraft.value = props.coordinate;
}

function goCoordinate(e) {
	emit("go", coordinateDraft.value);
	editingCoordinate.value = false;
	e.target.blur();
}

function setScale(text) {
	const n = Number(String(text).replace(/^1\s*:\s*/, "").replace(/[\s,]/g, ""));
	if (Number.isFinite(n) && n > 0) emit("scale", n);
}

function choose(code) {
	crsOpen.value = false;
	emit("crs", code);
}

function other() {
	crsOpen.value = false;
	const code = window.prompt(__("EPSG code, e.g. EPSG:32735"), "EPSG:");
	if (code) emit("crs", code.trim().toUpperCase());
}
</script>
