<template>
	<!-- Plugins → Manage and Install Plugins (laid out like QGIS Desktop's plugin manager) -->
	<div class="gis-modal-backdrop" @click.self="$emit('close')">
		<div class="gis-modal gis-modal-wide gis-plugin-manager">
			<header class="gis-toolbox-head">
				<h3>{{ __("Plugins") }}</h3>
				<span class="muted small">{{ admin ? __("System Manager: you approve, install and enable plugins") : __("Approved plugins, and requests for new ones") }}</span>
				<span class="gis-spacer"></span>
				<button class="gis-icon-btn" :title="__('Close')" @click="$emit('close')">✕</button>
			</header>
			<p v-if="admin && !thirdParty" class="is-warning small">
				{{ __("Third-party plugins are switched off.") }} <a href="/desk/gis-plugin-settings" target="_blank">{{ __("GIS Plugin Settings") }}</a>
			</p>
			<div class="gis-plugin-body">
				<nav class="gis-plugin-tabs">
					<button v-for="t in tabs" :key="t.id" :class="{ active: tab === t.id }" @click="tab = t.id">
						{{ t.label }} <span v-if="t.count" class="muted small">({{ t.count }})</span>
					</button>
					<a v-if="admin" class="gis-plugin-settings-link small" href="/desk/gis-plugin-settings" target="_blank">{{ __("Settings…") }}</a>
				</nav>
				<section class="gis-plugin-list">
					<input v-model="query" class="gis-input" type="search" :placeholder="__('Search the QGIS plugin repository and installed plugins…')" @input="debounced_search" />
					<p v-if="loading" class="muted pad">{{ __("Loading…") }}</p>
					<div class="gis-scroll">
						<div
							v-for="e in entries"
							:key="e.key"
							class="gis-plugin-row"
							:class="{ active: current && current.key === e.key }"
							@click="current = e"
						>
							<Icon name="plugin" :size="16" />
							<div class="gis-plugin-row-text">
								<b>{{ e.title }}</b> <span class="muted small">{{ e.version }}</span>
								<div class="small muted">{{ e.badge }}</div>
							</div>
							<input
								v-if="e.record && e.record.status === 'Enabled' && e.record.category === 'QGIS Processing Plugin'"
								type="checkbox"
								:checked="e.record.in_my_toolbox"
								:title="__('Use in my Processing Toolbox')"
								@click.stop
								@change="toggle_mine(e, $event.target.checked)"
							/>
						</div>
						<p v-if="!loading && !entries.length" class="muted pad">{{ empty_text }}</p>
					</div>
				</section>
				<section class="gis-plugin-detail">
					<template v-if="current">
						<h4>{{ current.title }} <span class="muted small">{{ current.version }}</span></h4>
						<p class="small">
							<span v-if="current.record" class="gis-pill">{{ __(current.record.status) }}</span>
							<span v-if="current.record && current.record.compatibility_status !== 'Not Analysed'" class="gis-pill" :class="compat_class(current.record.compatibility_status)">{{ current.record.compatibility_status }}</span>
							<span v-if="current.repo && current.repo.server" class="gis-pill">{{ __("server") }}</span>
							<span v-if="current.experimental" class="gis-pill is-warning">{{ __("experimental") }}</span>
							<span v-if="current.upgradeable" class="gis-pill is-ok">{{ __("upgrade to {0}", [current.repo.version]) }}</span>
						</p>
						<p class="small gis-plugin-description">{{ current.description || __("No description.") }}</p>
						<p v-if="current.author" class="small muted">{{ __("Author") }}: {{ current.author }}</p>
						<p v-if="current.homepage" class="small"><a :href="current.homepage" target="_blank" rel="noopener">{{ current.homepage }}</a></p>
						<p v-if="current.record && current.record.last_error" class="is-error small">{{ current.record.last_error }}</p>
						<p v-if="current.record && current.record.rejection_reason" class="is-warning small">{{ __("Declined") }}: {{ current.record.rejection_reason }}</p>
						<p v-if="current.record && current.record.requested_by" class="muted small">{{ __("Requested by {0}: {1}", [current.record.requested_by, current.record.request_reason || ""]) }}</p>

						<!-- what this user can do -->
						<div class="gis-plugin-actions">
							<template v-if="current.record && current.record.status === 'Enabled' && current.record.category === 'QGIS Processing Plugin'">
								<label class="gis-check"><input type="checkbox" :checked="current.record.in_my_toolbox" @change="toggle_mine(current, $event.target.checked)" /> {{ __("Use in my Processing Toolbox") }}</label>
							</template>
							<template v-if="!admin && !current.record && current.repo">
								<textarea v-model="reason" class="gis-input" rows="2" :placeholder="__('Why do you need it? (sent to the System Managers)')"></textarea>
								<button class="gis-primary" :disabled="busy" @click="request_current">{{ __("Request this Plugin") }}</button>
							</template>
							<template v-if="admin">
								<button v-if="!current.record && current.repo" class="gis-primary" :disabled="busy" @click="act('register_from_repository', { name: current.repo.name, download_url: current.repo.download_url })">{{ __("Register and Analyse") }}</button>
								<template v-if="current.record">
									<button v-if="['Requested', 'Rejected', 'Registered'].includes(current.record.status)" class="gis-primary" :disabled="busy" @click="act('approve_request')">{{ __("Download and Analyse") }}</button>
									<button v-if="current.record.status === 'Requested'" :disabled="busy" @click="reject">{{ __("Decline…") }}</button>
									<button v-if="current.record.status === 'Analysed' && installable(current.record)" class="gis-primary" :disabled="busy" @click="act('install_plugin')">{{ __("Install") }}</button>
									<button v-if="current.record.status === 'Installed'" class="gis-primary" :disabled="busy" @click="act('set_plugin_enabled', { enabled: 1 })">{{ __("Enable for Everyone") }}</button>
									<button v-if="['Enabled', 'Error'].includes(current.record.status)" :disabled="busy" @click="act('set_plugin_enabled', { enabled: 0 })">{{ __("Disable") }}</button>
									<button v-if="current.upgradeable" :disabled="busy" @click="act('upgrade_plugin', { download_url: current.repo.download_url })">{{ __("Upgrade") }}</button>
									<button v-if="['Installed', 'Enabled', 'Error'].includes(current.record.status)" :disabled="busy" @click="act('uninstall_plugin')">{{ __("Uninstall") }}</button>
									<a class="gis-button" :href="`/desk/gis-plugin/${current.record.name}`" target="_blank">{{ __("Details in Desk") }}</a>
								</template>
							</template>
							<p v-if="current.record && current.record.category === 'QGIS Desktop Plugin'" class="muted small">{{ __("Desktop plugins use the QGIS Desktop interface, which cannot run in a browser; they are registered and classified only.") }}</p>
							<p v-if="busy" class="muted small">{{ __("Working… (downloading, analysing or loading can take a minute)") }}</p>
							<p v-if="error" class="is-error small">{{ error }}</p>
						</div>
					</template>
					<p v-else class="muted">{{ __("Choose a plugin.") }}</p>
				</section>
			</div>
		</div>
	</div>
</template>

<script setup>
import { computed, onMounted, ref } from "vue";
import { call } from "./api.js";
import Icon from "./Icon.vue";

const __ = window.__;
const SVC = "is_production.geo_planning.services.gis_plugin_service.";
const emit = defineEmits(["close", "changed", "message"]);

const tab = ref("installed");
const query = ref("");
const records = ref([]);
const repo = ref([]);
const admin = ref(false);
const thirdParty = ref(false);
const loading = ref(false);
const busy = ref(false);
const error = ref("");
const reason = ref("");
const current = ref(null);
let timer = null;

onMounted(async () => {
	await load_catalogue();
	await search();
});

async function load_catalogue() {
	const data = await call(SVC + "plugin_catalogue");
	records.value = data.plugins;
	admin.value = data.is_admin;
	thirdParty.value = data.third_party_enabled;
	if (current.value) current.value = all_entries.value.find((e) => e.key === current.value.key) || null;
}

async function search() {
	loading.value = true;
	try {
		repo.value = await call(SVC + "search_repository", { query: query.value });
	} catch (e) {
		error.value = e.message;
	} finally {
		loading.value = false;
	}
}

function debounced_search() {
	clearTimeout(timer);
	timer = setTimeout(search, 400);
}

const all_entries = computed(() => {
	const out = [];
	const used = new Set();
	for (const r of records.value) {
		const match = repo.value.find((p) => p.record === r.name);
		if (match) used.add(match.download_url);
		out.push(entry(r, match));
	}
	for (const p of repo.value) if (!used.has(p.download_url) && !p.record) out.push(entry(null, p));
	return out;
});

function entry(record, repoItem) {
	const title = record ? record.title : repoItem.name;
	return {
		key: record ? `r:${record.name}` : `p:${repoItem.download_url}`,
		record,
		repo: repoItem,
		title,
		version: record ? record.installed_version || record.version : repoItem.version,
		description: (record && record.description) || (repoItem && repoItem.description),
		author: (record && record.author) || (repoItem && repoItem.author),
		homepage: (record && record.homepage) || (repoItem && repoItem.homepage),
		experimental: (record && record.experimental) || (repoItem && repoItem.experimental),
		upgradeable: !!(repoItem && repoItem.upgradeable),
		badge: record ? `${__(record.status)} · ${record.category || ""}` : __("In the QGIS plugin repository"),
	};
}

const q = computed(() => query.value.trim().toLowerCase());
const filters = {
	all: () => true,
	installed: (e) => e.record && ["Installed", "Enabled", "Error"].includes(e.record.status),
	not_installed: (e) => !e.record || ["Registered", "Analysed"].includes(e.record.status),
	upgradeable: (e) => e.upgradeable,
	invalid: (e) => e.record && (["Error", "Rejected"].includes(e.record.status) || e.record.compatibility_status === "Unsupported"),
	requests: (e) => e.record && e.record.status === "Requested",
};
const tabs = computed(() =>
	[
		{ id: "all", label: __("All") },
		{ id: "installed", label: __("Installed") },
		{ id: "not_installed", label: __("Not Installed") },
		{ id: "upgradeable", label: __("Upgradeable") },
		{ id: "invalid", label: __("Invalid") },
		{ id: "requests", label: admin.value ? __("Requests") : __("My Requests") },
	].map((t) => ({ ...t, count: all_entries.value.filter(filters[t.id]).length }))
);
const entries = computed(() =>
	all_entries.value.filter(filters[tab.value]).filter((e) => !q.value || !e.record || `${e.title} ${e.description || ""}`.toLowerCase().includes(q.value))
);
const empty_text = computed(() =>
	tab.value === "installed" ? __("No plugins are installed yet. Search the repository under All or Not Installed.") : __("Nothing here.")
);

function installable(record) {
	return ["QGIS Processing Plugin", "QGIS Server Plugin"].includes(record.category);
}

function compat_class(status) {
	return { "Native server-compatible": "is-ok", "Processing-compatible": "is-ok", "Unsupported": "is-error", "Requires manual adaptation": "is-warning" }[status] || "";
}

async function act(method, args = {}) {
	busy.value = true;
	error.value = "";
	try {
		const name = current.value.record ? current.value.record.name : undefined;
		const result = await call(SVC + method, name && !args.name ? { name, ...args } : args);
		await load_catalogue();
		if (method === "register_from_repository") {
			current.value = all_entries.value.find((e) => e.record && e.record.name === result) || current.value;
		}
		await search();
		emit("changed");
	} catch (e) {
		error.value = e.message;
	} finally {
		busy.value = false;
	}
}

async function reject() {
	const why = window.prompt(__("Why is this plugin declined? (sent to the requester)"));
	if (why) await act("reject_plugin", { reason: why });
}

async function request_current() {
	busy.value = true;
	error.value = "";
	try {
		await call(SVC + "request_plugin", { title: current.value.repo.name, download_url: current.value.repo.download_url, reason: reason.value });
		reason.value = "";
		emit("message", __("Request sent to the System Managers."));
		await load_catalogue();
		await search();
	} catch (e) {
		error.value = e.message;
	} finally {
		busy.value = false;
	}
}

async function toggle_mine(e, on) {
	try {
		await call(SVC + "set_my_plugin", { name: e.record.name, use: on ? 1 : 0 });
		e.record.in_my_toolbox = on;
		emit("changed");
	} catch (err) {
		error.value = err.message;
	}
}
</script>
