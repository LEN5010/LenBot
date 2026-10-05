<script setup>
// The current scene in the top bar: group name and avatar, with a menu of all scenes.
import { computed } from 'vue'
import { mdiCheck, mdiUnfoldMoreHorizontal } from '@mdi/js'
import { sceneName, sceneNumber } from '../../api.js'
import { host } from '../store.js'
import SceneAvatar from './SceneAvatar.vue'
const props = defineProps({ modelValue: { type: String, required: true } })
const emit = defineEmits(['update:modelValue'])
const scenes = computed(() => host.state?.scenes || [])
const current = computed(() => scenes.value.find(item => item.scene === props.modelValue))
</script>
<template>
  <v-menu location="bottom start" offset="8" max-height="420">
    <template #activator="{ props: menu }">
      <button v-bind="menu" type="button" class="scene-picker" aria-label="切换群聊">
        <SceneAvatar :scene="modelValue" :size="30" />
        <span class="picker-text"><strong>{{ sceneName(modelValue) }}</strong>
          <span>{{ sceneNumber(modelValue) }}<template v-if="current"> · {{ current.persona.name }}</template></span></span>
        <v-icon :icon="mdiUnfoldMoreHorizontal" size="18" class="picker-caret" />
      </button>
    </template>
    <v-list density="comfortable" class="picker-list" nav>
      <v-list-subheader>切换群聊</v-list-subheader>
      <v-list-item v-for="item in scenes" :key="item.scene" :active="item.scene === modelValue" color="primary"
        @click="emit('update:modelValue', item.scene)">
        <template #prepend><SceneAvatar :scene="item.scene" :size="32" class="mr-3" /></template>
        <v-list-item-title>{{ sceneName(item.scene) }}</v-list-item-title>
        <v-list-item-subtitle>{{ sceneNumber(item.scene) }} · {{ item.persona.name }}</v-list-item-subtitle>
        <template v-if="item.scene === modelValue" #append><v-icon :icon="mdiCheck" size="18" /></template>
      </v-list-item>
    </v-list>
  </v-menu>
</template>
<style scoped>
.scene-picker{display:flex;align-items:center;gap:var(--sp-2);min-width:0;max-width:340px;padding:5px 10px 5px 6px;border:1px solid var(--line);border-radius:var(--radius);
  background:var(--surface);color:inherit;font:inherit;text-align:left;cursor:pointer;transition:border-color var(--dur-1),box-shadow var(--dur-2) var(--ease-out),transform var(--dur-1) var(--ease-spring)}
.scene-picker:hover{border-color:var(--line-strong);box-shadow:var(--shadow-card)}
.scene-picker:active{transform:scale(.98)}
.picker-text{display:grid;min-width:0;line-height:1.3}
.picker-text strong,.picker-text span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.picker-text strong{font-size:var(--fs-md)}
.picker-text span{font-size:var(--fs-xs);color:var(--muted)}
.picker-caret{color:var(--muted);margin-left:auto}
.picker-list{min-width:280px}
</style>
