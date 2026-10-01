<script setup>
import { computed, ref, watch } from 'vue'
import { api, fmtTime, queryString } from '../api.js'
import { developerDetails } from '../composables/useDeveloperMode.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'

const props = defineProps({ scene: { type: String, required: true }, scope: { type: String, required: true },
  scenes: { type: Array, required: true } })
const sourceScene = ref(props.scene), recordScope = ref(props.scope)
let epoch = 0
const data = ref(null), detail = ref(null), selected = ref(null)
const loading = ref(false), detailLoading = ref(false), error = ref(''), detailError = ref('')
const selection = () => `${sourceScene.value}\u0000${recordScope.value}\u0000${epoch}`
const beginList = useRequestGuard(selection)
const beginDetail = useRequestGuard(() => `${selection()}\u0000${selected.value}`)
const detailStale = computed(() => detail.value !== null &&
  (detail.value.call.id !== selected.value || detailError.value !== ''))
const PURPOSE = { index: '正文索引', query: '查询向量', reindex: '离线重建' }
watch([sourceScene, recordScope], () => {
  ++epoch; data.value = null; detail.value = null; selected.value = null
  loading.value = false; detailLoading.value = false; error.value = ''; detailError.value = ''
}, { flush: 'sync' })
function status(call) {
  return call.ended === null ? '记录未结束（不证明服务仍活跃）' : call.error !== null ? '结束记录带错误／中断' : '已保存结束记录'
}
async function read(more = false) {
  if (loading.value || !sourceScene.value || (more && data.value?.next_offset == null)) return
  const fresh = beginList(), target = { scene: sourceScene.value, scope: recordScope.value, limit: '20' }
  if (more) { target.offset = String(data.value.next_offset); target.snapshot = String(data.value.snapshot) }
  loading.value = true; error.value = ''
  try {
    const result = await api('/api/host/memory/embedding-calls?' + queryString(target))
    if (!fresh()) return
    data.value = more ? { ...result, calls: [...data.value.calls, ...result.calls] } : result
  } catch (caught) { if (fresh()) error.value = caught.message }
  finally { if (fresh()) loading.value = false }
}
async function readDetail(id) {
  if (!developerDetails.value || detailLoading.value) return
  selected.value = id
  const fresh = beginDetail()
  detailLoading.value = true; detailError.value = ''
  try {
    const result = await api(`/api/host/memory/embedding-calls/${id}?` + queryString({ scene: sourceScene.value, scope: recordScope.value }))
    if (fresh()) detail.value = result
  } catch (caught) { if (fresh()) detailError.value = caught.message }
  finally { if (fresh()) detailLoading.value = false }
}
</script>

<template>
  <section class="surface embedding-records">
    <div class="section-heading"><h2>记忆向量调用</h2>
      <v-btn variant="outlined" :loading="loading" :disabled="!sourceScene" @click="read(false)">读取当前范围记录</v-btn></div>
    <div class="record-selection"><v-select v-model="sourceScene" :items="scenes" label="历史记录场景（与文件编辑选择分开）" hide-details="auto" />
      <v-select v-model="recordScope" :items="[{title:'此场景原记录',value:'scene'},{title:'原 public 记录（不是公开证明）',value:'public'}]"
        label="历史记账分区" hide-details="auto" /></div>
    <p class="muted">停用当前记忆后仍可只读原处理库；不存在或格式不符会报原错，不创建或升级处理库、结束旧调用或重新启用服务。</p>
    <p class="muted">这里只读已保存调用，不请求模型、不重建索引或重试。范围取原记录字段；旧版整根重建曾记为公共，其输入不因此都是公开资料。缺费用仍是未知，不补零。</p>
    <v-alert v-if="error" type="error" variant="tonal" role="alert" :title="data ? '读取失败 · 下方保留上次结果' : '读取失败'">{{ error }}</v-alert>
    <p v-if="!data && !loading && !error" class="muted">按需读取，未自动查询。</p>
    <template v-if="data">
      <p class="muted">上次读取 {{ fmtTime(data.read_at) }} · 原记录分区 {{ data.source }} · 行范围上限 #{{ data.snapshot }}。行范围固定，结果仍可能在服务返回后变化，不是原子快照。</p>
      <p v-if="data.calls.length === 0" class="muted">这次读取范围内没有向量调用记录；不代表上游没有产生过费用。</p>
      <ul class="calls"><li v-for="call in data.calls" :key="call.id">
        <strong>#{{ call.id }} · {{ PURPOSE[call.purpose] || call.purpose }}</strong>
        <p>{{ status(call) }} · 开始 {{ fmtTime(call.started) }} · 结束记录 {{ fmtTime(call.ended) }}</p>
        <p>{{ call.cost === null ? '估算费用未知' : `当时估算 ${call.cost.amount} ${call.cost.currency}` }}；不代表供应商账单。</p>
        <p v-if="call.error !== null" class="original">{{ call.error }}</p>
        <details><summary>服务用量与已保存响应摘要</summary>
          <p class="muted">这里只保存向量条数与维度，没有完整数值向量或完整上游响应。结束记录不代表索引已替换。</p>
          <pre>{{ JSON.stringify({ usage: call.usage, response_summary: call.response, cost: call.cost }, null, 2) }}</pre></details>
        <v-btn v-if="developerDetails" variant="text" :loading="detailLoading && selected === call.id" :disabled="detailLoading"
          @click="readDetail(call.id)">读取 #{{ call.id }} 实际输入与价目快照</v-btn>
      </li></ul>
      <v-btn v-if="data.next_offset !== null" variant="outlined" :loading="loading" @click="read(true)">读取下一页（20 项）</v-btn>
    </template>
    <template v-if="developerDetails">
      <v-alert v-if="detailError" type="error" variant="tonal" role="alert">{{ detailError }}<span v-if="detail"> 下方是上次成功读取的 #{{ detail.call.id }}，不是本次读取成功。</span></v-alert>
      <div v-if="detail" class="detail">
        <h3>{{ detailStale ? '上次已读取详情' : '已读取详情' }} #{{ detail.call.id }}</h3>
        <p class="muted">读取于 {{ fmtTime(detail.read_at) }}。当前已知配置凭据被隐藏；其余输入、真实身份和私人事实仍保留。这不是匿名导出或可直接公开的资料。</p>
        <pre>{{ JSON.stringify(detail.call, null, 2) }}</pre>
      </div>
    </template>
  </section>
</template>

<style scoped>
.embedding-records{display:grid;gap:12px;overflow-wrap:anywhere}
.section-heading{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
.record-selection{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,260px),1fr));gap:12px}
.calls{list-style:none;padding:0;margin:0;display:grid;gap:12px}
.calls li,.detail{border:1px solid var(--line);border-radius:10px;padding:14px;min-width:0}
.original,pre{white-space:pre-wrap;overflow-wrap:anywhere}
summary{cursor:pointer;min-height:44px}.calls p{margin:8px 0}
</style>
