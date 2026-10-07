<script setup>
import { computed, ref, watch } from 'vue'
import { api, sceneName } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { notify, readPendingRestart } from '../../store.js'
import { toolLabel } from '../../labels.js'
import { clone, same } from '../../forms.js'
import SettingSection from '../../ui/SettingSection.vue'
import ResourceState from '../../ui/ResourceState.vue'
import AllowList from '../../components/AllowList.vue'

const props = defineProps({ scene: { type: String, required: true } })
const emit = defineEmits(['dirty'])
const caps = useResource(() => api(`/api/host/capabilities?scene=${encodeURIComponent(props.scene)}`))
const draft = ref(null)
const save = useAction()
watch(() => caps.data.value, value => { if (value) draft.value = clone(value.role_tools.saved) })
const sorted = value => value === 'all' ? value : [...value].sort()
const dirty = computed(() => Boolean(caps.data.value) && !same(sorted(draft.value), sorted(caps.data.value.role_tools.saved)))
watch(dirty, value => emit('dirty', value), { immediate: true })

const items = computed(() => (caps.data.value?.tools || []).map(tool => ({
  name: tool.name, label: tool.source ? `${tool.name}（${tool.source}）` : toolLabel(tool.name), tool,
  note: tool.reasons.filter(reason => reason !== '角色没有允许这个工具').join('；'),
})))
const companions = { schedule: ['schedule_list', 'schedule_cancel'], delegate: ['task'] }
function update(value) {
  if (value !== 'all' && draft.value !== 'all') {
    for (const [lead, needed] of Object.entries(companions)) {
      if (value.includes(lead) && !draft.value.includes(lead)) value = [...new Set([...value, ...needed])]
    }
    const deferred = caps.data.value.tools.filter(tool => tool.deferred).map(tool => tool.name)
    if (value.some(name => deferred.includes(name)) && !value.includes('tool_search')) value = [...value, 'tool_search']
  }
  draft.value = value
}
const shared = computed(() => (caps.data.value?.role_tools.affected_scenes || []).filter(item => item !== props.scene))

async function submit() {
  const result = await save.run(() => api(`/api/host/scenes/${encodeURIComponent(props.scene)}/role-tools`, {
    method: 'PUT', body: JSON.stringify({ directory: caps.data.value.role_tools.directory, tools: draft.value }),
  }))
  if (result) {
    caps.data.value = { ...caps.data.value, role_tools: result }
    readPendingRestart()
    notify('已保存')
  }
}
</script>

<template>
  <ResourceState :resource="caps" error-title="读取工具失败">
  <SettingSection v-if="draft !== null" title="工具"
    :description="`已保存角色 ${caps.data.value.role_tools.persona.name} 在群里能用哪些工具。` + (shared.length ? `这个角色也用在 ${shared.map(sceneName).join('、')}，修改会一起生效。` : '')"
    :dirty="dirty" :saving="save.busy.value" :error="save.error.value" @save="submit">
    <AllowList :model-value="draft" @update:model-value="update" :items="items" all-label="全部可用的工具">
      <template #item="{ item }">
        <div class="tool-preview">
          <p>{{ item.tool.summary }}</p>
          <span class="state" :class="{ off: !item.tool.registered }">{{ item.tool.registered ? (item.tool.discovered ? '已加载到模型' : '可按需发现') : '当前不可用' }}</span>
          <details>
            <summary>模型调用说明</summary>
            <p>{{ item.tool.description }}</p>
            <pre>{{ JSON.stringify(item.tool.parameters, null, 2) }}</pre>
            <template v-if="item.tool.instructions">
              <strong>插件共享指南（发现工具后加载）</strong>
              <pre>{{ item.tool.instructions }}</pre>
            </template>
          </details>
        </div>
      </template>
    </AllowList>
  </SettingSection>
  </ResourceState>
</template>

<style scoped>
.tool-preview{min-width:0;margin-left:40px;font-size:var(--fs-sm);color:var(--muted)}
.tool-preview>p{margin:0 0 var(--sp-2);display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.state{display:inline-block;padding:1px 8px;border-radius:999px;background:var(--fill);font-size:var(--fs-xs)}
.state.off{opacity:.7}
.tool-preview details{margin-top:var(--sp-2)}
.tool-preview summary{cursor:pointer;width:fit-content}
.tool-preview details p{white-space:pre-wrap}
.tool-preview pre{overflow:auto;max-height:24rem;white-space:pre-wrap;overflow-wrap:anywhere;padding:var(--sp-2);border-radius:8px;background:var(--fill)}
</style>
