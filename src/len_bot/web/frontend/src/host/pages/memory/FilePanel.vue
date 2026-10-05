<script setup>
import { computed, ref, watch } from 'vue'
import { api, queryString } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { notify } from '../../store.js'
import { formatTime } from '../../time.js'
import { confirm } from '../../../composables/useConfirm.js'
import Panel from '../../ui/Panel.vue'
import ErrorNote from '../../ui/ErrorNote.vue'
import FormDialog from '../../ui/FormDialog.vue'
import ResourceState from '../../ui/ResourceState.vue'
import ObjectList from '../../ui/ObjectList.vue'
import Fold from '../../ui/Fold.vue'
import CodeBlock from '../../ui/CodeBlock.vue'
import ForgetSources from './ForgetSources.vue'
import AdoptPending from './AdoptPending.vue'

const props = defineProps({
  scene: { type: String, required: true }, scope: { type: String, required: true },
  path: { type: String, default: null }, directory: { type: String, required: true },
  state: { type: Object, required: true },
})
const emit = defineEmits(['dirty', 'changed'])
const can = action => props.state.actions.includes(action)
const writable = computed(() => can('write') && (props.scope === 'scene' || props.state.public_writable))
const inScene = props.scope === 'scene'

const doc = useResource(() => api('/api/host/memory/read?' + queryString({ scene: props.scene, path: props.path, scope: props.scope })),
  { immediate: props.path !== null })
const original = ref(''), content = ref(''), reason = ref('')
const newPath = ref(props.directory ? `${props.directory}/` : '')
const editing = ref(props.path === null)
watch(() => doc.data.value, value => { if (value) { original.value = value.content; content.value = value.content } })
const dirty = computed(() => editing.value && (content.value !== original.value || reason.value !== ''
  || (props.path === null && newPath.value !== (props.directory ? `${props.directory}/` : ''))))
watch(dirty, value => emit('dirty', value), { immediate: true })

const save = useAction()
async function submit() {
  const path = props.path ?? newPath.value.trim()
  const result = await save.run(() => api('/api/host/memory/file', { method: 'PUT', body: JSON.stringify({
    scene: props.scene, path, scope: props.scope, content: content.value, reason: reason.value }) }))
  if (!result) return
  original.value = result.after
  content.value = original.value
  reason.value = ''
  editing.value = false
  notify('已保存')
  if (props.path === null) emit('changed', path)
}
async function cancel() {
  if (dirty.value && !await confirm({ title: '放弃没保存的修改？', confirmLabel: '放弃', danger: true })) return
  if (props.path === null) { emit('dirty', false); emit('changed', null); return }
  content.value = original.value
  reason.value = ''
  editing.value = false
}

// Delete keeps history; forget also removes reachable history and stops chosen messages from being learned again.
const removing = ref(false), forget = ref(false), removeReason = ref(''), sources = ref([])
const remove = useAction()
async function confirmRemove() {
  const result = await remove.run(() => api('/api/host/memory/delete', { method: 'POST', body: JSON.stringify({
    scene: props.scene, path: props.path, reason: removeReason.value, forget: forget.value,
    exclude_records: forget.value ? sources.value.map(item => item.record) : null }) }))
  if (!result) return
  removing.value = false
  notify(forget.value ? '已忘掉' : '已删除')
  emit('changed', null)
}

const historyOpen = ref(false)
const history = useResource(() => api('/api/host/memory/history?' + queryString({ scene: props.scene, path: props.path })), { immediate: false })
function openHistory() {
  historyOpen.value = true
  history.reload()
}
const actionLabel = { write: '修改', delete: '删除', forget: '忘记' }
const pending = computed(() => inScene && props.path?.startsWith('legacy-import/'))
</script>

<template>
  <Panel v-if="editing" tag="form" :title="path ?? '新建记忆'" @submit.prevent="submit">
    <v-text-field v-if="path === null" v-model="newPath" label="文件路径" hint="以 .md 结尾，例如 people/小明.md" persistent-hint />
    <v-textarea v-model="content" label="内容" rows="12" auto-grow class="mono" />
    <v-text-field v-model="reason" label="为什么修改" hint="会记在修改历史里" persistent-hint />
    <ErrorNote v-if="save.error.value" title="没有保存成功" :error="save.error.value" />
    <template #footer>
      <v-spacer />
      <v-btn variant="text" :disabled="save.busy.value" @click="cancel">取消</v-btn>
      <v-btn type="submit" color="primary" :loading="save.busy.value" :disabled="!reason.trim() || (path === null && !newPath.trim().endsWith('.md'))">保存</v-btn>
    </template>
  </Panel>
  <Panel v-else :title="path">
    <template #actions>
      <v-btn v-if="writable && doc.data.value" size="small" variant="tonal" color="primary" @click="editing = true">编辑</v-btn>
      <v-btn v-if="inScene && can('history')" size="small" variant="text" @click="openHistory">修改历史</v-btn>
      <v-btn v-if="inScene && (can('delete') || can('forget')) && doc.data.value" size="small" variant="text" color="error"
        @click="removing = true; forget = !can('delete'); removeReason = ''; sources = []">删除</v-btn>
    </template>
    <ResourceState :resource="doc" error-title="读取记忆失败">
      <p class="content">{{ original }}</p>
      <AdoptPending v-if="pending" :scene="scene" :source="path" :original="original"
        :persona-ids="state.persona_ids[scene] || []" @adopted="value => emit('changed', value.source_removed ? null : path)" />
    </ResourceState>
  </Panel>

  <FormDialog v-model="removing" :title="`删除 ${path}`" size="md" :busy="remove.busy.value">
    <v-radio-group v-if="can('forget') && can('delete')" v-model="forget" hide-details>
      <v-radio label="只删除这个文件" :value="false" />
      <v-radio label="彻底忘记" :value="true" />
    </v-radio-group>
    <p class="muted">{{ forget
      ? '删除这个文件和它的修改历史。还可以在下面选出相关的聊天消息，以后整理记忆时跳过它们，避免又记回来。'
      : '删除这个文件，修改历史还在。' }}</p>
    <v-text-field v-model="removeReason" label="原因" />
    <ForgetSources v-if="forget" :scene="scene" @selection="value => sources = value" />
    <ErrorNote v-if="remove.error.value" title="没有删除成功" :error="remove.error.value" />
    <template #actions>
      <v-btn color="error" :loading="remove.busy.value" :disabled="!removeReason.trim()" @click="confirmRemove">
        {{ forget ? `忘记${sources.length ? `，并跳过 ${sources.length} 条消息` : ''}` : '删除' }}</v-btn>
    </template>
  </FormDialog>

  <FormDialog v-model="historyOpen" :title="`${path} 的修改历史`" size="md" cancel-label="关闭">
    <ResourceState :resource="history" error-title="读取修改历史失败" :empty="!history.data.value?.changes.length" empty-text="还没有修改记录" compact v-slot="{ data }">
      <ObjectList divided>
        <li v-for="(item, index) in data.changes" :key="index" class="change">
          <strong>{{ actionLabel[item.action] || item.action }} · {{ formatTime(item.changed_at) }}</strong>
          <p>{{ item.reason }}</p>
          <Fold label="改动前后">
            <h4>之前</h4><CodeBlock :text="item.before ?? '（没有）'" /><h4>之后</h4><CodeBlock :text="item.after ?? '（没有）'" />
          </Fold>
        </li>
      </ObjectList>
    </ResourceState>
  </FormDialog>
</template>

<style scoped>
.content{white-space:pre-wrap;overflow-wrap:anywhere;margin:0;line-height:1.7}
.change{padding:var(--sp-3) 0;display:grid;gap:var(--sp-1)}
.change p{margin:0}
</style>
