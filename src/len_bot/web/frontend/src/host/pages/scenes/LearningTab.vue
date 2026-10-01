<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { api } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { notify, readPendingRestart } from '../../store.js'
import { clone, numberOrBlank, same } from '../../forms.js'
import ErrorNote from '../../components/ErrorNote.vue'
import SettingSection from '../../components/SettingSection.vue'
import AdvancedFields from '../../components/AdvancedFields.vue'
import DevOnly from '../../components/DevOnly.vue'

const props = defineProps({ scene: { type: String, required: true } })
const root = `/api/host/scenes/${encodeURIComponent(props.scene)}`

// Learning switches (saved to the root config, restart to apply).
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

// Review lists.
const filter = ref('pending')
const kinds = {
  expressions: { title: '说话方式', path: `${root}/learning/expressions`, key: 'status' },
  jargon: { title: '黑话', path: `${root}/learning/jargon/terms`, key: 'status' },
  stickers: { title: '表情', path: `${root}/learning/stickers/candidates`, key: 'review' },
}
const lists = {}
for (const [kind, item] of Object.entries(kinds)) {
  lists[kind] = useResource(() => api(`${item.path}?${new URLSearchParams({ [item.key]: filter.value, ...(kind === 'stickers' && filter.value === 'pending' ? { status: 'complete' } : {}) })}`))
}
watch(filter, () => Object.values(lists).forEach(list => list.reload()))
const edits = reactive({})
const review = useAction()
const fresh = (kind, item) => kind === 'expressions' ? { situation: item.situation, style: item.style }
  : kind === 'jargon' ? { meaning: item.meaning ?? item.latest_meaning ?? '' }
  : { description: item.description ?? '', text: item.text ?? '', emotions: [...item.emotions], tags: [...item.tags] }
for (const kind of Object.keys(kinds)) {
  watch(lists[kind].data, value => {
    for (const item of value?.items || []) edits[`${kind}:${item.id}`] ??= fresh(kind, item)
  })
}
const editOf = (kind, item) => edits[`${kind}:${item.id}`]
async function decide(kind, item, decision) {
  const edit = editOf(kind, item)
  const body = kind === 'expressions' ? { ...edit, status: decision }
    : kind === 'jargon' ? { meaning: edit.meaning || null, status: decision }
    : { description: edit.description || null, text: edit.text || null, emotions: edit.emotions, tags: edit.tags, review: decision }
  const done = await review.run(() => api(`${kinds[kind].path}/${item.id}`, { method: 'PUT', body: JSON.stringify(body) }))
  if (done) lists[kind].reload()
}

// Turning an adopted expression into a persona example.
const example = ref(null)
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

// Reply effects over the last 7 days.
const effects = useResource(() => api(`${root}/reply-effects?days=7`))
const effectLabels = [['agree', '认同'], ['continue', '接着聊'], ['correct', '纠正'], ['negative', '反感'], ['unrelated', '没接话'], ['uncertain', '看不出来']]

const learner = useResource(() => api(`${root}/learning`))
const run = useAction()
async function learnNow() {
  const done = await run.run(() => api(`${root}/learning/request`, { method: 'POST' }))
  if (done) notify('已开始学习，结果稍后出现在下面')
}
</script>

<template>
  <div class="page-stack">
    <ErrorNote v-if="config.error.value" title="读取学习设置失败" :error="config.error.value" />
    <SettingSection v-if="config.data.value" title="学习" description="Bot 从群聊里学说话方式、黑话和表情，学到的内容经你审核后使用。"
      :dirty="configDirty" :saving="saveConfig.busy.value" :error="saveConfig.error.value" @save="submitConfig">
      <v-switch :model-value="draft !== null" label="开启学习" @update:model-value="toggleLearning" />
      <v-alert v-if="draft && noLearner" type="warning" variant="tonal">
        还没有给学习分配模型，先到 <RouterLink :to="{ name: 'host-models' }">模型页</RouterLink> 设置，再回来保存。</v-alert>
      <template v-if="draft">
        <div class="form-grid">
          <v-switch v-model="draft.extract" label="学说话方式" />
          <v-switch v-model="draft.jargon_extract" label="学黑话" />
          <v-switch v-model="draft.collect_stickers" label="收集表情" />
          <v-switch v-model="draft.reply_effects" label="观察群友对 Bot 发言的反应" hint="主动开话题需要打开这一项" persistent-hint />
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

    <section class="surface">
      <div class="review-head">
        <h2>学到的内容</h2>
        <v-btn-toggle v-model="filter" mandatory density="compact" color="primary">
          <v-btn value="pending">待审核</v-btn><v-btn value="adopted">已采用</v-btn><v-btn value="rejected">不要的</v-btn></v-btn-toggle>
        <v-btn v-if="learner.data.value?.enabled" variant="text" :loading="run.busy.value" @click="learnNow">现在学一次</v-btn>
      </div>
      <ErrorNote v-if="review.error.value" title="没有保存成功" :error="review.error.value" />
      <ErrorNote v-if="run.error.value" title="没有开始学习" :error="run.error.value" />
      <div v-for="(item, kind) in kinds" :key="kind" class="review-group">
        <h3>{{ item.title }}</h3>
        <ErrorNote v-if="lists[kind].error.value" :title="`读取${item.title}失败`" :error="lists[kind].error.value" />
        <p v-if="lists[kind].data.value && !lists[kind].data.value.items.length" class="muted">没有内容</p>
        <article v-for="entry in lists[kind].data.value?.items || []" :key="entry.id" class="review-card">
          <template v-if="kind === 'expressions'">
            <div class="form-grid">
              <v-text-field v-model="editOf(kind, entry).situation" label="什么时候" />
              <v-text-field v-model="editOf(kind, entry).style" label="怎么说" />
            </div>
          </template>
          <template v-else-if="kind === 'jargon'">
            <p><strong>{{ entry.term }}</strong> <span class="muted">出现 {{ entry.count }} 次</span></p>
            <v-text-field v-model="editOf(kind, entry).meaning" label="意思" />
          </template>
          <template v-else>
            <div class="sticker">
              <img :src="`${kinds.stickers.path}/${entry.id}/image`" alt="表情" loading="lazy" />
              <div class="sticker-fields">
                <v-text-field v-model="editOf(kind, entry).description" label="描述" />
                <v-text-field v-model="editOf(kind, entry).text" label="图上的字" />
                <v-combobox v-model="editOf(kind, entry).emotions" label="情绪" multiple chips closable-chips />
              </div>
            </div>
          </template>
          <div class="review-actions">
            <v-btn v-if="filter !== 'adopted'" color="primary" variant="tonal" size="small" :loading="review.busy.value" @click="decide(kind, entry, 'adopted')">采用</v-btn>
            <v-btn v-if="filter === 'adopted'" variant="tonal" size="small" :loading="review.busy.value" @click="decide(kind, entry, 'adopted')">保存修改</v-btn>
            <v-btn v-if="filter !== 'rejected'" variant="text" size="small" :loading="review.busy.value" @click="decide(kind, entry, 'rejected')">不要</v-btn>
            <v-btn v-if="filter !== 'pending'" variant="text" size="small" :loading="review.busy.value" @click="decide(kind, entry, 'pending')">放回待审核</v-btn>
            <v-btn v-if="kind === 'expressions' && filter === 'adopted'" variant="text" size="small" @click="openExample(entry)">加到角色样例</v-btn>
          </div>
          <DevOnly label="原始数据"><pre>{{ JSON.stringify(entry, null, 2) }}</pre></DevOnly>
        </article>
      </div>
    </section>

    <section class="surface">
      <h2>群友的反应（最近 7 天）</h2>
      <ErrorNote v-if="effects.error.value" title="读取回复效果失败" :error="effects.error.value" />
      <template v-if="effects.data.value">
        <p v-if="!effects.data.value.enabled" class="muted">没有开启观察，可以在上面的学习设置里打开。</p>
        <p v-else-if="!effects.data.value.distribution.samples" class="muted">还没有记录</p>
        <div v-else class="effects">
          <div v-for="[key, label] in effectLabels" :key="key"><span>{{ label }}</span><strong>{{ effects.data.value.distribution.states[key] }}</strong></div>
        </div>
        <DevOnly label="原始统计"><pre>{{ JSON.stringify(effects.data.value, null, 2) }}</pre></DevOnly>
      </template>
    </section>
    <DevOnly label="学习服务状态"><pre>{{ JSON.stringify(learner.data.value, null, 2) }}</pre></DevOnly>

    <v-dialog :model-value="example !== null" max-width="560" @update:model-value="value => { if (!value) example = null }">
      <v-card v-if="example" title="加到角色样例">
        <v-card-text class="example-form">
          <p class="muted">会写进这个群所用角色包的人工样例，重启后生效。用同一个角色包的群都会受影响。</p>
          <v-textarea v-model="example.context" label="情境" rows="2" auto-grow />
          <v-textarea v-model="example.line" label="台词" rows="2" auto-grow />
          <v-combobox v-model="example.tags" label="标签" multiple chips closable-chips />
          <ErrorNote v-if="addExample.error.value" title="没有加成功" :error="addExample.error.value" />
        </v-card-text>
        <v-card-actions><v-spacer /><v-btn @click="example = null">取消</v-btn>
          <v-btn color="primary" :loading="addExample.busy.value" @click="submitExample">加入</v-btn></v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped>
.review-head{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.review-head h2{margin:0 auto 0 0 !important}
.review-group{margin-top:16px}
.review-group h3{font-size:15px;margin:0 0 8px}
.review-card{border:1px solid var(--line);border-radius:10px;padding:12px;margin-bottom:10px;display:grid;gap:10px}
.review-card p{margin:0}
.review-actions{display:flex;gap:6px;flex-wrap:wrap}
.sticker{display:flex;gap:14px;align-items:flex-start;flex-wrap:wrap}
.sticker img{width:120px;height:120px;object-fit:contain;border:1px solid var(--line);border-radius:8px;background:var(--list-heading-bg)}
.sticker-fields{flex:1 1 280px;display:grid;gap:10px}
.effects{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:10px;margin-top:8px}
.effects>div{border:1px solid var(--line);border-radius:10px;padding:10px}
.effects span{display:block;color:var(--muted);font-size:13px}
.effects strong{font-size:22px}
.example-form{display:grid;gap:12px}
</style>
