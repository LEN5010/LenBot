<script setup>
import { computed, ref, watch } from 'vue'
import { api, queryString } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { notify } from '../../store.js'
import { formatTime } from '../../time.js'
import ErrorNote from '../../components/ErrorNote.vue'
import DevOnly from '../../components/DevOnly.vue'
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
  original.value = typeof result.after === 'string' ? result.after : content.value
  content.value = original.value
  reason.value = ''
  editing.value = false
  notify('已保存')
  if (props.path === null) emit('changed', path)
}
function cancel() {
  if (dirty.value && !window.confirm('放弃没保存的修改？')) return
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
const diffs = ref({})
const diff = useAction()
function openHistory() {
  historyOpen.value = true
  history.reload()
}
async function readDiff(item) {
  const args = { scene: props.scene, path: props.path, target: item.oid }
  if (item.parents.length) args.previous = item.parents[0]
  const result = await diff.run(() => api('/api/host/memory/history-diff?' + queryString(args)))
  if (result) diffs.value = { ...diffs.value, [item.oid]: result.diff_text || '没有文字改动' }
}
const actionLabel = { write: '修改', delete: '删除', forget: '忘记' }
const pending = computed(() => props.state.backend === 'local' && inScene && props.path?.startsWith('legacy-import/'))
</script>

<template>
  <section class="surface file">
    <form v-if="editing" class="editor" @submit.prevent="submit">
      <h2>{{ path ?? '新建记忆' }}</h2>
      <v-text-field v-if="path === null" v-model="newPath" label="文件路径" hint="以 .md 结尾，例如 people/小明.md" persistent-hint />
      <v-textarea v-model="content" label="内容" rows="12" auto-grow class="mono" />
      <v-text-field v-model="reason" label="为什么修改" hint="会记在修改历史里" persistent-hint />
      <ErrorNote v-if="save.error.value" title="没有保存成功" :error="save.error.value" />
      <div class="actions">
        <v-btn type="submit" color="primary" :loading="save.busy.value" :disabled="!reason.trim() || (path === null && !newPath.trim().endsWith('.md'))">保存</v-btn>
        <v-btn variant="text" :disabled="save.busy.value" @click="cancel">取消</v-btn>
      </div>
    </form>
    <template v-else>
      <div class="head">
        <h2>{{ path }}</h2>
        <div class="actions">
          <v-btn v-if="writable && doc.data.value" size="small" variant="tonal" color="primary" @click="editing = true">编辑</v-btn>
          <v-btn v-if="inScene && can('history')" size="small" variant="text" @click="openHistory">修改历史</v-btn>
          <v-btn v-if="inScene && (can('delete') || can('forget')) && doc.data.value" size="small" variant="text" color="error"
            @click="removing = true; forget = !can('delete'); removeReason = ''; sources = []">删除</v-btn>
        </div>
      </div>
      <ErrorNote v-if="doc.error.value" title="读取记忆失败" :error="doc.error.value" />
      <pre v-if="doc.data.value" class="content">{{ original }}</pre>
      <AdoptPending v-if="pending && doc.data.value" :scene="scene" :source="path" :original="original"
        :persona-ids="state.persona_ids[scene] || []" @adopted="value => emit('changed', value.source_removed ? null : path)" />
    </template>
  </section>

  <v-dialog v-model="removing" max-width="640" scrollable>
    <v-card :title="`删除 ${path}`">
      <v-card-text class="remove-form">
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
      </v-card-text>
      <v-card-actions><v-spacer /><v-btn @click="removing = false">取消</v-btn>
        <v-btn color="error" :loading="remove.busy.value" :disabled="!removeReason.trim()" @click="confirmRemove">
          {{ forget ? `忘记${sources.length ? `，并跳过 ${sources.length} 条消息` : ''}` : '删除' }}</v-btn></v-card-actions>
    </v-card>
  </v-dialog>

  <v-dialog v-model="historyOpen" max-width="760" scrollable>
    <v-card :title="`${path} 的修改历史`">
      <v-card-text>
        <ErrorNote v-if="history.error.value" title="读取修改历史失败" :error="history.error.value" />
        <ErrorNote v-if="diff.error.value" title="读取改动失败" :error="diff.error.value" />
        <p v-if="history.data.value && !history.data.value.changes.length" class="muted">还没有修改记录</p>
        <ul class="history">
          <li v-for="(item, index) in history.data.value?.changes || []" :key="index">
            <template v-if="item.source === 'snapshot'">
              <strong>{{ item.message }}</strong>
              <v-btn v-if="!diffs[item.oid]" size="small" variant="text" :loading="diff.busy.value" @click="readDiff(item)">查看改动</v-btn>
              <pre v-else>{{ diffs[item.oid] }}</pre>
              <DevOnly><span class="muted">{{ item.oid }}</span></DevOnly>
            </template>
            <template v-else>
              <strong>{{ actionLabel[item.action] || item.action }} · {{ formatTime(item.changed_at) }}</strong>
              <p>{{ item.reason }}</p>
              <details><summary>改动前后</summary>
                <h4>之前</h4><pre>{{ item.before ?? '（没有）' }}</pre><h4>之后</h4><pre>{{ item.after ?? '（没有）' }}</pre></details>
            </template>
          </li>
        </ul>
      </v-card-text>
      <v-card-actions><v-spacer /><v-btn @click="historyOpen = false">关闭</v-btn></v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.file{display:grid;gap:12px;min-width:0}
.head{display:flex;justify-content:space-between;align-items:flex-start;gap:8px;flex-wrap:wrap}
.head h2,.editor h2{overflow-wrap:anywhere}
.actions{display:flex;gap:4px;flex-wrap:wrap}
.editor{display:grid;gap:12px}
.mono :deep(textarea){font-family:ui-monospace,SFMono-Regular,Consolas,monospace;line-height:1.55}
.content{white-space:pre-wrap;overflow-wrap:anywhere;margin:0;font-family:inherit;line-height:1.7}
.remove-form{display:grid;gap:12px}
.history{list-style:none;margin:0;padding:0;display:grid;gap:12px}
.history li{border-bottom:1px solid var(--line);padding-bottom:10px}
.history p{margin:4px 0}
.history pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}
.history summary{cursor:pointer;color:var(--muted)}
.history h4{font-size:13px;margin:8px 0 4px}
</style>
