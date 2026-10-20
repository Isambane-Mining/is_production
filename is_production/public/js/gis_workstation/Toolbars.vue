<template>
	<div class="gis-toolbars" role="toolbar">
		<div v-for="group in groups.filter((g) => g.visible)" :key="group.id" class="gis-toolbar-group" :title="group.title">
			<span class="gis-toolbar-grip" aria-hidden="true"></span>
			<template v-for="item in group.items" :key="item.id">
				<span v-if="item === '|'" class="gis-sep"></span>
				<label v-else-if="item.kind === 'input'" class="gis-toolbar-input" :title="item.title">
					{{ item.label }}
					<input :type="item.type || 'text'" :value="item.value" :placeholder="item.placeholder" @change="item.change($event.target.value)" />
				</label>
				<select v-else-if="item.kind === 'select'" class="gis-toolbar-select" :title="item.title" :value="item.value" @change="item.change($event.target.value)">
					<option v-for="o in item.options" :key="o.value" :value="o.value">{{ o.label }}</option>
				</select>
				<button
					v-else
					class="gis-tool"
					:class="{ active: item.active }"
					:disabled="item.disabled"
					:title="item.title + (item.shortcut ? ` (${item.shortcut})` : '')"
					:aria-pressed="item.active ? 'true' : undefined"
					@click="item.action()"
				>
					<Icon :name="item.icon" />
					<span v-if="item.label" class="gis-tool-label">{{ item.label }}</span>
				</button>
			</template>
		</div>
	</div>
</template>

<script setup>
import Icon from "./Icon.vue";

defineProps({ groups: Array });
</script>
