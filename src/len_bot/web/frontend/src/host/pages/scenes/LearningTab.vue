<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { api } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { notify, readPendingRestart } from '../../store.js'
import { clone, numberOrBlank, same } from '../../forms.js'
import { confirm } from '../../../composables/useConfirm.js'
import Panel from '../../ui/Panel.vue'
import ResourceState from '../../ui/ResourceState.vue'
import ErrorNote from '../../ui/ErrorNote.vue'
import SettingSection from '../../ui/SettingSection.vue'
import AdvancedFields from '../../ui/AdvancedFields.vue'
import FormDialog from '../../ui/FormDialog.vue'
import StatGrid from '../../ui/StatGrid.vue'
import DevOnly from '../../ui/DevOnly.vue'

const props = defineProps({ scene: { type: String, required: true } })
const emit = defineEmits(['dirty'])
const root = `/api/host/scenes/${encodeURIComponent(props.scene)}`

const config = useResource(() => api(`/api/host/settings/scenes/${encodeURIComponent(props.scene)}/learning`))
const draft = ref(null)
const saveConfig = useAction()
watch(() => JSON.stringify(config.data.value?.saved), () => { if (config.data.value) draft.value = clone(config.data.value.saved) }, { immediate: true })
const models = useResource(() => api('/api/host/settings'))
const noLearner = computed(() => models.data.value && !models.data.value.saved.models.roles.learner)
const configDirty = computed(() => Boolean(config.data.value) && !same(draft.value, config.data.value.saved))
function toggleLearning(value) {
  draft.value = value ? { extract: true, jargon_extract: false, collect_stickers: false, reply_effects: false,
    min_messages: 20, batch_size: 50, idle_seconds: 300, max_age_seconds: 1800, auto_adopt: false,
    embedding: config.data.value.saved?.embedding ?? null } : null
}
async function submitConfig() {
  const result = await saveConfig.run(() => api(`/api/host/settings/scenes/${encodeURIComponent(props.scene)}/learning`, {
    method: 'PUT', body: JSON.stringify({ learning: draft.value }) }))
  if (result) { config.data.value = result; readPendingRestart(); notify('已保存') }
}

const filter = ref('pending')
const jargonFilter = ref('pending')
const selectedFilter = kind => kind === 'jargon' ? jargonFilter.value : filter.value
const kinds = {
  expressions: { title: '说话方式', path: `${root}/learning/expressions`, key: 'status' },
  jargon: { title: '黑话', path: `${root}/learning/jargon/terms`, key: 'status' },
  stickers: { title: '表情', path: `${root}/learning/stickers/candidates`, key: 'review' },
}
const lists = {}
for (const [kind, item] of Object.entries(kinds)) {
  lists[kind] = useResource(async (offset = 0, discardEdits = false) => ({
    ...(await api(`${item.path}?${new URLSearchParams({ [item.key]: selectedFilter(kind), offset, limit: 20,
      ...(kind === 'stickers' && filter.value === 'pending' ? { status: 'complete' } : {}) })}`)), discardEdits,
  }))
}
const edits = reactive({})
const review = useAction()
const fresh = (kind, item) => kind === 'expressions' ? { situation: item.situation, style: item.style }
  : kind === 'jargon' ? { meaning: item.meaning ?? item.latest_meaning ?? '' }
  : { description: item.description ?? '', text: item.text ?? '', emotions: [...item.emotions], tags: [...item.tags] }
for (const kind of Object.keys(kinds)) {
  watch(lists[kind].data, value => {
    for (const item of value?.items || []) {
      const key = `${kind}:${item.id}`
      if (value.discardEdits || !Object.hasOwn(edits, key)) edits[key] = fresh(kind, item)
    }
  })
}
const editOf = (kind, item) => edits[`${kind}:${item.id}`]
const reviewDirty = kind => (lists[kind].data.value?.items || []).some(item => !same(editOf(kind, item), fresh(kind, item)))
const discard = title => confirm({ title, text: '没保存的修改会丢失。', confirmLabel: '放弃修改', danger: true })
async function changeFilter(value) {
  if (value === filter.value) return
  if (Object.keys(kinds).some(reviewDirty) && !await discard('放弃候选内容中没保存的修改？')) return
  filter.value = value
  Object.values(lists).forEach(list => list.reload(0, true))
}
async function changePage(kind, offset) {
  if (reviewDirty(kind) && !await discard('放弃这一页没保存的修改？')) return
  lists[kind].reload(offset, true)
}
async function changeJargonFilter(value) {
  if (value === jargonFilter.value) return
  if (reviewDirty('jargon') && !await discard('放弃黑话中没保存的修改？')) return
  jargonFilter.value = value
  lists.jargon.reload(0, true)
}
async function decide(kind, item, decision) {
  const edit = editOf(kind, item)
  const body = kind === 'expressions' ? { ...edit, status: decision }
    : kind === 'jargon' ? { meaning: decision === 'pending' ? null : edit.meaning || null, status: decision }
    : { description: edit.description || null, text: edit.text || null, emotions: edit.emotions, tags: edit.tags, review: decision }
  const done = await review.run(() => api(`${kinds[kind].path}/${item.id}`, { method: 'PUT', body: JSON.stringify(body) }))
  if (done) {
    edits[`${kind}:${item.id}`] = fresh(kind, done)
    lists[kind].reload(lists[kind].data.value.offset)
  }
}

const example = ref(null)
watch(() => configDirty.value || Object.keys(kinds).some(reviewDirty) || example.value !== null,
  value => emit('dirty', value), { immediate: true })
const addExample = useAction()
function openExample(item) {
  example.value = { expression_id: item.id, context: item.situation, line: item.style, tags: [] }
}
async function submitExample() {
  const result = await addExample.run(async () => {
    const target = await api(`${root}/persona-location`)
    return api(`${root}/persona-examples`, { method: 'POST', body: JSON.stringify({ ...example.value, directory: target.saved_path }) })
  })
  if (result) { example.value = null; readPendingRestart(); notify('已加到角色样例') }
}

const effects = useResource(() => api(`${root}/reply-effects?days=7`))
const effectLabels = [['agree', '认同'], ['continue', '接着聊'], ['correct', '纠正'], ['negative', '反感'], ['unrelated', '没接话'], ['uncertain', '看不出来']]
const effectStats = value => effectLabels.map(([key, label]) => ({ label, value: value.distribution.states[key] }))

const learner = useResource(() => api(`${root}/learning`))
const run = useAction()
async function learnNow() {
  const done = await run.run(() => api(`${root}/learning/request`, { method: 'POST' }))
  if (done) notify('已开始学习，结果稍后出现在下面')
}
</script>

<template>
  <ResourceState :resource="config" error-title="读取学习设置失败">
    <SettingSection title="学习"
      :dirty="configDirty" :saving="saveConfig.busy.value" :error="saveConfig.error.value" @save="submitConfig">
      <v-switch :model-value="draft !== null" label="开启学习" @update:model-value="toggleLearning" />
      <v-alert v-if="draft && noLearner" type="warning">
        还没有给学习分配模型，先到 <RouterLink :to="{ name: 'host-models', query: { tab: 'roles' } }">模型页</RouterLink> 设置，再回来保存。</v-alert>
      <template v-if="draft">
        <div class="form-grid">
          <v-switch v-model="draft.extract" label="学说话方式" />
          <v-switch v-model="draft.jargon_extract" label="学黑话" />
          <v-switch v-model="draft.collect_stickers" label="学习群聊表情包" />
          <v-switch v-model="draft.reply_effects" label="观察群友对 Bot 发言的反应" />
        </div>
        <v-switch v-model="draft.auto_adopt" label="学到的说话方式不经审核直接使用" />
        <AdvancedFields>
          <v-text-field :model-value="draft.min_messages" type="number" label="攒够多少条消息学一次" @update:model-value="value => draft.min_messages = numberOrBlank(value)" />
          <v-text-field :model-value="draft.batch_size" type="number" label="每次最多看多少条" @update:model-value="value => draft.batch_size = numberOrBlank(value)" />
          <v-text-field :model-value="draft.idle_seconds" type="number" label="群里安静多少秒后开始学" @update:model-value="value => draft.idle_seconds = numberOrBlank(value)" />
          <v-text-field :model-value="draft.max_age_seconds" type="number" label="最多攒多少秒必须学一次" @update:model-value="value => draft.max_age_seconds = numberOrBlank(value)" />
        </AdvancedFields>
      </template>
    </SettingSection>
  </ResourceState>

  <Panel title="学到的内容">
    <template #actions>
      <v-btn-toggle :model-value="filter" mandatory @update:model-value="changeFilter">
        <v-btn value="pending">待审核</v-btn><v-btn value="adopted">已采用</v-btn><v-btn value="rejected">不要的</v-btn></v-btn-toggle>
      <v-btn v-if="learner.data.value?.enabled" variant="outlined" :loading="run.busy.value" @click="learnNow">现在学一次</v-btn>
    </template>
    <ErrorNote v-if="review.error.value" title="没有保存成功" :error="review.error.value" />
    <ErrorNote v-if="run.error.value" title="没有开始学习" :error="run.error.value" />
    <div v-for="(item, kind) in kinds" :key="kind" class="review-group">
      <div class="inline">
        <h3>{{ item.title }}</h3>
        <v-btn-toggle v-if="kind === 'jargon'" :model-value="jargonFilter" mandatory class="ml-auto" @update:model-value="changeJargonFilter">
          <v-btn value="pending">自动学习</v-btn><v-btn value="adopted">人工固定</v-btn><v-btn value="rejected">已禁用</v-btn>
        </v-btn-toggle>
      </div>
      <ResourceState :resource="lists[kind]" :error-title="`读取${item.title}失败`" :empty="!lists[kind].data.value?.items.length" empty-text="没有内容" compact v-slot="{ data }">
        <article v-for="entry in data.items" :key="entry.id" class="review-card">
          <div v-if="kind === 'expressions'" class="form-grid">
            <v-text-field v-model="editOf(kind, entry).situation" label="什么时候" />
            <v-text-field v-model="editOf(kind, entry).style" label="怎么说" />
          </div>
          <template v-else-if="kind === 'jargon'">
            <p><strong>{{ entry.term }}</strong> <span class="muted">出现 {{ entry.count }} 次</span></p>
            <p class="muted">最新推断：{{ entry.latest_meaning || '尚未推断，继续积累用例' }}</p>
            <v-text-field v-model="editOf(kind, entry).meaning" label="修订词义（保存后人工固定）" />
          </template>
          <div v-else class="sticker">
            <img :src="`${kinds.stickers.path}/${entry.id}/image`" alt="表情" loading="lazy" />
            <div class="sticker-fields">
              <v-text-field v-model="editOf(kind, entry).description" label="描述" />
              <v-text-field v-model="editOf(kind, entry).text" label="图上的字" />
              <v-combobox v-model="editOf(kind, entry).emotions" label="情绪" multiple chips closable-chips />
            </div>
          </div>
          <div class="inline">
            <v-btn v-if="selectedFilter(kind) !== 'adopted'" variant="outlined" size="small" :loading="review.busy.value" @click="decide(kind, entry, 'adopted')">{{ kind === 'jargon' ? '保存并固定词义' : '采用' }}</v-btn>
            <v-btn v-if="selectedFilter(kind) === 'adopted'" variant="outlined" size="small" :loading="review.busy.value" @click="decide(kind, entry, 'adopted')">保存修改</v-btn>
            <v-btn v-if="selectedFilter(kind) !== 'rejected'" variant="text" size="small" :loading="review.busy.value" @click="decide(kind, entry, 'rejected')">{{ kind === 'jargon' ? '禁用' : '不要' }}</v-btn>
            <v-btn v-if="selectedFilter(kind) !== 'pending'" variant="text" size="small" :loading="review.busy.value" @click="decide(kind, entry, 'pending')">{{ kind === 'jargon' ? '恢复自动词义' : '放回待审核' }}</v-btn>
            <v-btn v-if="kind === 'expressions' && filter === 'adopted'" variant="text" size="small" @click="openExample(entry)">加到角色样例</v-btn>
          </div>
          <DevOnly label="原始数据" :json="entry" />
        </article>
        <div v-if="data.offset > 0 || data.total > data.limit" class="inline pager">
          <v-btn size="small" variant="text" :disabled="lists[kind].loading.value || review.busy.value || data.offset === 0"
            @click="changePage(kind, data.offset - data.limit)">上一页</v-btn>
          <span class="muted small">第 {{ Math.floor(data.offset / data.limit) + 1 }} 页 · 共 {{ data.total }} 条</span>
          <v-btn size="small" variant="text" :disabled="lists[kind].loading.value || review.busy.value || data.offset + data.limit >= data.total"
            @click="changePage(kind, data.offset + data.limit)">下一页</v-btn>
        </div>
      </ResourceState>
    </div>
  </Panel>

  <Panel title="群友的反应" description="最近 7 天">
    <ResourceState :resource="effects" error-title="读取回复效果失败" v-slot="{ data }">
      <p v-if="!data.enabled" class="muted">没有开启观察，可以在上面的学习设置里打开。</p>
      <p v-else-if="!data.distribution.samples" class="muted">还没有记录。</p>
      <StatGrid v-else :items="effectStats(data)" />
      <DevOnly label="原始统计" :json="data" />
    </ResourceState>
  </Panel>
  <DevOnly label="学习服务状态" :json="learner.data.value" />

  <FormDialog :model-value="example !== null" title="加到角色样例" :busy="addExample.busy.value" @update:model-value="value => { if (!value) example = null }">
    <template v-if="example">
      <p class="muted">会写进这个群所用角色包的人工样例，用同一个角色包的群都会受影响。重启后生效。</p>
      <v-textarea v-model="example.context" label="情境" rows="2" auto-grow />
      <v-textarea v-model="example.line" label="台词" rows="2" auto-grow />
      <v-combobox v-model="example.tags" label="标签" multiple chips closable-chips />
      <ErrorNote v-if="addExample.error.value" title="没有加成功" :error="addExample.error.value" />
    </template>
    <template #actions><v-btn color="primary" :loading="addExample.busy.value" @click="submitExample">加入</v-btn></template>
  </FormDialog>
</template>

<style scoped>
.review-group{display:grid;gap:var(--sp-3)}
.review-group + .review-group{border-top:1px solid var(--line);padding-top:var(--sp-4)}
.review-group p{margin:0}
.review-card{border:1px solid var(--line);border-radius:var(--radius);padding:var(--sp-3);display:grid;gap:var(--sp-3)}
.review-card p{margin:0}
.pager{justify-content:center}
.sticker{display:flex;gap:var(--sp-4);align-items:flex-start;flex-wrap:wrap}
.sticker img{width:120px;height:120px;object-fit:contain;border:1px solid var(--line);border-radius:var(--radius);background:var(--hover)}
.sticker-fields{flex:1 1 280px;display:grid;gap:var(--sp-3)}
</style>
