<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, fmtTime } from '../api.js'
import EntityLink from './EntityLink.vue'
import ResourceViewer from './ResourceViewer.vue'

const route = useRoute(), router = useRouter()
const page = computed(() => Math.max(1, Number(route.query.page) || 1))
const selectedId = computed(() => typeof route.query.interest === 'string' ? route.query.interest : '')
const query = ref(''), data = ref(null), detail = ref(null), error = ref(''), loading = ref(false)
let request = 0
const types = { public_fact: '公共事实', agent_evaluation: 'Agent 评价', research_intent: '研究意图' }
const statuses = { active: '有效', superseded: '已替代', withdrawn: '已撤回', expired: '已过期' }
function navigate(values) { router.push({ name: 'memories', query: { ...route.query, ...values, tab: 'interests' } }) }
async function load() {
  const current = ++request
  loading.value = true; error.value = ''; detail.value = null; data.value = null
  query.value = typeof route.query.query === 'string' ? route.query.query : ''
  try {
    const value = await api(selectedId.value ? '/api/cockpit/public-interests/' + encodeURIComponent(selectedId.value)
      : '/api/cockpit/public-interests?' + new URLSearchParams({ page: page.value, query: query.value }))
    if (current !== request) return
    if (selectedId.value) detail.value = value
    else data.value = value
  } catch (failure) { if (current === request) error.value = failure.message }
  finally { if (current === request) loading.value = false }
}
watch(() => [route.query.page, route.query.query, route.query.interest], load, { immediate: true })
onBeforeUnmount(() => { ++request })
</script>

<template>
  <section class="interests">
    <div class="actions"><h2>公共兴趣</h2><v-btn v-if="selectedId" variant="text" @click="navigate({interest:undefined})">返回列表</v-btn><v-btn variant="outlined" :loading="loading" @click="load">刷新</v-btn></div>
    <p>公共事实、Agent 评价与研究意图分别标注。这里读取已保存记录；是否供 Agent 使用还取决于来源证明、状态和有效期。</p>
    <v-form v-if="!selectedId" class="actions" @submit.prevent="navigate({query:query || undefined,page:1})"><v-text-field v-model="query" label="查找主题或内容" hide-details /><v-btn type="submit">查找</v-btn></v-form>
    <v-alert v-if="error" type="error" variant="tonal">{{ error }}</v-alert><v-progress-linear v-if="loading" indeterminate />
    <template v-if="data"><p>共 {{ data.total }} 条 · 读取于 {{ fmtTime(data.read_at) }}</p><v-card v-for="item in data.items" :key="item.id" class="pa-4"><h3>{{ item.topic }}</h3><p>{{ types[item.record_type] }} · v{{ item.revision }} · {{ statuses[item.status] || item.status }}{{ item.expired ? ' · 当前已过期' : '' }}</p><p class="copy">{{ item.statement }}</p><p>观察 {{ fmtTime(item.observed_at) }} · 有效至 {{ item.valid_until === null ? '未设定' : fmtTime(item.valid_until) }}</p><v-btn variant="tonal" @click="navigate({interest:item.id})">来源与修订</v-btn></v-card><p v-if="!data.items.length">没有符合条件的公共兴趣。</p><v-pagination v-if="data.total > data.page_size" :model-value="page" :length="Math.ceil(data.total/data.page_size)" @update:model-value="value => navigate({page:value})" /></template>
    <template v-if="detail"><v-card class="pa-4"><h3>{{ detail.topic }}</h3><p>{{ types[detail.record_type] }} · v{{ detail.revision }} · {{ statuses[detail.status] || detail.status }}{{ detail.expired ? ' · 当前已过期' : '' }}</p><p class="copy">{{ detail.statement }}</p><p>发布 {{ fmtTime(detail.published_at) }} · 观察 {{ fmtTime(detail.observed_at) }} · 有效至 {{ detail.valid_until === null ? '未设定' : fmtTime(detail.valid_until) }}</p><v-alert :type="detail.public_sources_confirmed ? 'info' : 'warning'" variant="tonal">{{ detail.public_sources_confirmed ? '来源链具有匿名公共资料证明。' : '来源链未全部确认，不能作为已验证公共资料使用。' }} 来源关联不代表后续调用已呈现正文或图像。</v-alert><ResourceViewer title="采用时的具体证据范围" :content="detail.evidence_spans" /></v-card><h3>原始资料来源</h3><v-card v-for="source in detail.sources" :key="source.id" class="pa-4"><EntityLink type="result" :id="source.id" :scene-id="source.scene_id" /><EntityLink v-if="source.event_id" type="event" :id="source.event_id" :scene-id="source.scene_id" label="取得资料的事件" /><ResourceViewer title="资料范围与来源事实" :content="source.result" /></v-card><h3>最近修订（最多 50 条）</h3><p v-if="detail.more_changes">仍有更早修订，可按下列来源场景进入运行记录查看。</p><v-card v-for="change in detail.changes" :key="change.id" class="pa-4"><EntityLink type="event" :id="change.id" :scene-id="change.scene_id" :label="fmtTime(change.timestamp)" /><EntityLink v-if="change.payload.job_id" type="job" :id="change.payload.job_id" :scene-id="change.scene_id" label="采用此修订的工作" /><ResourceViewer title="修订内容与原因" :content="change.payload" /></v-card></template>
  </section>
</template>

<style scoped>
.interests{display:grid;gap:16px;min-width:0}.interests p{margin:10px 0;overflow-wrap:anywhere}.actions{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.actions .v-text-field{min-width:180px}.copy{white-space:pre-wrap;line-height:1.7}.interests .entity-link{margin-right:12px}
</style>
