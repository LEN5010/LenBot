<script setup>
// The in-page choice of group (or of role) for pages that show one at a time.
// Groups are chips in one scrollable row; role mode lists each role once, with how many groups use it.
import { computed } from 'vue'
import { sceneName } from '../../api.js'
import { host } from '../store.js'
import SceneAvatar from './SceneAvatar.vue'
const props = defineProps({ modelValue: { type: String, required: true }, mode: { type: String, default: 'scene' } })
const emit = defineEmits(['update:modelValue'])
const scenes = computed(() => host.state?.scenes || [])
const current = computed(() => scenes.value.find(item => item.scene === props.modelValue))
const roles = computed(() => {
  const groups = new Map()
  for (const item of scenes.value) {
    if (!groups.has(item.persona_path)) groups.set(item.persona_path, { path: item.persona_path, name: item.persona.name, scenes: [] })
    groups.get(item.persona_path).scenes.push(item.scene)
  }
  return [...groups.values()]
})
// One role has nothing to switch to.
const visible = computed(() => props.mode !== 'role' || roles.value.length > 1)
function pickRole(role) {
  if (!role.scenes.includes(props.modelValue)) emit('update:modelValue', role.scenes[0])
}
</script>
<template>
  <div v-if="visible" class="scene-switch" role="tablist" :aria-label="mode === 'role' ? '选择角色' : '选择群聊'">
    <template v-if="mode === 'role'">
      <button v-for="role in roles" :key="role.path" type="button" role="tab" class="switch-chip"
        :class="{ active: current?.persona_path === role.path }" :aria-selected="current?.persona_path === role.path"
        :title="role.scenes.map(sceneName).join('、')" @click="pickRole(role)">
        <strong>{{ role.name }}</strong><span>{{ role.scenes.length }} 个群</span>
      </button>
    </template>
    <template v-else>
      <button v-for="item in scenes" :key="item.scene" type="button" role="tab" class="switch-chip"
        :class="{ active: item.scene === modelValue }" :aria-selected="item.scene === modelValue"
        @click="emit('update:modelValue', item.scene)">
        <SceneAvatar :scene="item.scene" :size="22" /><strong>{{ sceneName(item.scene) }}</strong>
      </button>
    </template>
  </div>
</template>
<style scoped>
.scene-switch{display:flex;gap:var(--sp-2);overflow-x:auto;padding:2px 2px 6px;margin-top:calc(-1 * var(--sp-2));scrollbar-width:thin}
.switch-chip{display:inline-flex;align-items:center;gap:var(--sp-2);flex:none;max-width:260px;padding:6px 12px 6px 8px;border:1px solid var(--line);
  border-radius:999px;background:var(--surface);color:inherit;font:inherit;cursor:pointer;transition:border-color var(--dur-1),background var(--dur-1)}
.switch-chip:hover{border-color:var(--line-strong)}
.switch-chip.active{border-color:var(--brand);background:var(--selected);color:var(--primary)}
.switch-chip strong{font-weight:600;font-size:var(--fs-sm);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.switch-chip span{font-size:var(--fs-xs);color:var(--muted)}
</style>
