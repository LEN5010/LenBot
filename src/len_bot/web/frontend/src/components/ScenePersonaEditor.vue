<script setup>
import { computed, ref, watch } from 'vue'
import { api } from '../api.js'
import { useAction, useResource } from '../composables/useResource.js'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.js'
import { confirm } from '../composables/useConfirm.js'
import { notify } from '../host/store.js'
import SettingSection from '../host/ui/SettingSection.vue'
import ResourceState from '../host/ui/ResourceState.vue'
import RowEditor from '../host/ui/RowEditor.vue'

const stored = useResource(() => api('/api/chat-test/scene-persona'))
const saved = computed(() => stored.data.value?.saved ?? null)
const draft = ref(null)
watch(saved, value => {
  if (!value) return
  draft.value = {
    persona_aliases: [...value.persona_aliases],
    relationships: Object.entries(value.relationships).map(([qq, description]) => ({ qq, description })),
    behavior_addendum: value.behavior_addendum ?? '',
  }
})
const body = value => ({
  persona_aliases: [...value.persona_aliases],
  relationships: Object.fromEntries(value.relationships.map(row => [row.qq, row.description])),
  behavior_addendum: value.behavior_addendum === '' ? null : value.behavior_addendum,
})
const dirty = computed(() => Boolean(saved.value && draft.value) && JSON.stringify(body(draft.value)) !== JSON.stringify(saved.value))
useUnsavedChanges(dirty)

const problem = computed(() => {
  if (!draft.value) return ''
  const blank = draft.value.persona_aliases.findIndex(alias => !alias.trim())
  if (blank >= 0) return `补充称呼第 ${blank + 1} 项是空的，不需要就删掉。`
  const seen = new Set()
  for (const [index, row] of draft.value.relationships.entries()) {
    if (!/^[a-z][a-z0-9_-]*:[^:\s/\\]+$/.test(row.qq)) return `关系第 ${index + 1} 行的账号要写成 onebot:QQ号。`
    if (seen.has(row.qq)) return `账号 ${row.qq} 出现了两次，请合并成一行。`
    seen.add(row.qq)
    if (!row.description.trim()) return `关系第 ${index + 1} 行的说明是空的，不需要就删掉。`
  }
  const addendum = draft.value.behavior_addendum
  return addendum !== '' && !addendum.trim() ? '行为补充不能只有空格。' : ''
})

async function reread() {
  if (dirty.value && !await confirm({ title: '放弃修改并重新读取？', text: '没保存的场景补充会丢失。', confirmLabel: '放弃', danger: true })) return
  stored.reload()
}
const save = useAction()
async function submit() {
  const result = await save.run(() => api('/api/chat-test/scene-persona', { method: 'PUT', body: JSON.stringify(body(draft.value)) }))
  if (!result) return
  stored.data.value = result
  notify(result.restart_required ? '已保存，重启后生效' : '已保存')
}
</script>

<template>
  <ResourceState :resource="stored" error-title="读取场景补充失败">
    <SettingSection v-if="draft" title="本场景补充"
      :restart="false" :dirty="dirty" :saving="save.busy.value" :error="save.error.value" :problem="problem" @save="submit">
      <template #actions><v-btn variant="text" :loading="stored.loading.value" @click="reread">重新读取</v-btn></template>
      <v-alert v-if="stored.data.value.restart_required" type="warning">有修改等待重启，重启测试实例后生效。</v-alert>
      <div class="group">
        <h3>补充称呼</h3>
        <RowEditor :items="draft.persona_aliases" :make="() => ''" add-label="添加称呼" empty-text="还没有补充称呼。" v-slot="{ index }">
          <v-text-field v-model="draft.persona_aliases[index]" :label="`称呼 ${index + 1}`" hide-details="auto" />
        </RowEditor>
      </div>
      <div class="group">
        <h3>关系说明</h3>
        <p class="muted small">一位群友一行，同一个账号只能出现一次。</p>
        <RowEditor :items="draft.relationships" :make="() => ({ qq: '', description: '' })" add-label="添加关系" empty-text="还没有关系说明。"
          columns="minmax(120px,180px) minmax(0,1fr)" v-slot="{ item }">
          <v-text-field v-model="item.qq" label="账号" placeholder="onebot:QQ号" hide-details="auto" />
          <v-textarea v-model="item.description" label="说明" rows="1" auto-grow hide-details="auto" />
        </RowEditor>
      </div>
      <v-textarea v-model="draft.behavior_addendum" label="行为补充" rows="4" auto-grow />
    </SettingSection>
  </ResourceState>
</template>

<style scoped>
.group{display:grid;gap:var(--sp-2)}
.group p{margin:0}
</style>
