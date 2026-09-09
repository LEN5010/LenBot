<script setup>
import { computed, ref, watch } from 'vue'
import { fmtTime } from '../api.js'
import EntityLink from './EntityLink.vue'
import StatusBadge from './StatusBadge.vue'
import ResourceViewer from './ResourceViewer.vue'
import PluginOrigin from './PluginOrigin.vue'

const props = defineProps({ observation: { type: Object, required: true }, sceneId: String, rangeLabel: { type: String, default: '本次页面阅读范围' } })
const unit = computed(() => ({ characters: '字符', records: '记录' }[props.observation.coordinate_unit] || props.observation.coordinate_unit || '单位未记录'))
const localLabel = computed(() => props.observation.coordinate_unit === 'records' ? '本地记录续读参数' : '本地正文续读参数')
const failed = computed(() => ['error', 'unsupported'].includes(props.observation.status))
const stages = { availability: '当前能力检查', arguments: '参数解析', references: '来源引用', execution: '工具执行', presentation: '正文展示', commit: '事务提交' }
const copied = ref(false), copyError = ref('')
watch(()=>props.observation.evidence_span,()=>{copied.value=false;copyError.value=''})
function sourceLink(url) { return /^https?:\/\//i.test(url || '') ? url : undefined }
function fieldPath(loc) { return loc?.length ? loc.map((part,index)=>typeof part==='number'?`[${part}]`:`${index?'.':''}${part}`).join('') : '参数对象' }
async function copySpan() {
  try { await navigator.clipboard.writeText(JSON.stringify(props.observation.evidence_span)); copied.value=true; copyError.value='' }
  catch { copyError.value='未能复制，可在下方选择范围 JSON。' }
}
</script>

<template>
  <section class="observation-details" aria-label="工具资料状态与来源">
    <PluginOrigin v-if="observation.plugin_origin && sceneId" :origin="observation.plugin_origin" :scene-id="sceneId" />
    <div class="observation-facts"><StatusBadge domain="observation" :status="observation.status" /><code v-if="observation.error_code">{{ observation.error_code }}</code><span v-if="observation.fetched_at">取得于 {{ fmtTime(observation.fetched_at) }}</span><span v-if="observation.content_length!==undefined">已保存 {{ observation.content_length }} 字符</span></div>
    <div v-if="observation.tool_name || observation.tool_call_id || observation.error_stage || observation.http_status!==null && observation.http_status!==undefined" class="observation-facts"><strong v-if="observation.tool_name">{{ observation.tool_name }}</strong><span v-if="observation.error_stage">出错阶段：{{ stages[observation.error_stage] || observation.error_stage }}</span><code v-if="observation.http_status!==null && observation.http_status!==undefined">HTTP {{ observation.http_status }}</code><span v-if="observation.tool_call_id" class="call-id">调用 ID：{{ observation.tool_call_id }}</span></div>
    <v-alert v-if="failed && observation.content" type="error" variant="tonal" class="error-body">{{ observation.content }}</v-alert>
    <div v-if="observation.error_details?.length" class="field-errors"><h4>具体字段错误</h4><dl><template v-for="(detail,index) in observation.error_details" :key="index"><dt><code>{{ fieldPath(detail.loc) }}</code><span>{{ detail.type }}</span></dt><dd>{{ detail.message }}</dd></template></dl></div>
    <ResourceViewer v-if="observation.correction" title="未提交候选的可纠正范围与续读位置" :content="observation.correction" />
    <p v-if="observation.coverage" class="coverage">覆盖记录：{{ observation.coverage }}</p>
    <p v-if="observation.displayed_range">{{ rangeLabel }}：{{ observation.displayed_range.start }}–{{ observation.displayed_range.end }} / {{ observation.displayed_range.total }} {{ unit }}（起含止不含）。</p>
    <p v-if="observation.source_truncated" class="text-warning">源端资料有未取得的部分，当前保存正文不代表源全文。</p>
    <p v-else-if="observation.truncated && observation.source_truncated===undefined" class="muted">原记录带截断标记，未分别记录源端和本地正文覆盖。</p>
    <p v-if="observation.attachments?.length" class="muted">媒体引用 {{ observation.attachments.length }} 项；仅登记引用不表示模型已看到原图。</p>
    <details v-if="observation.evidence_span"><summary>本次展示范围的来源引用</summary><p class="muted">页面阅读不增加工作模型的已读范围；结论仍须符合该工作已保存的实际采用范围。</p><ResourceViewer title="来源范围 JSON" :content="observation.evidence_span"><template #actions><v-btn variant="text" size="small" @click="copySpan">{{ copied?'已复制':'复制范围' }}</v-btn></template></ResourceViewer><p v-if="copyError" role="alert">{{ copyError }}</p></details>
    <div v-if="observation.sources?.length" class="source-list"><div v-for="(source,index) in observation.sources" :key="index" class="source-item"><a v-if="sourceLink(source.url)" :href="sourceLink(source.url)" target="_blank" rel="noopener noreferrer">{{ source.title || source.url }}</a><span v-else-if="source.title || source.url">{{ source.title || source.url }}</span><EntityLink v-if="source.event_id" type="event" :id="source.event_id" :scene-id="sceneId" label="来源记录" /><span v-if="source.published_at" class="muted">源发布时间 {{ source.published_at }}</span></div></div>
    <details v-if="observation.next_call"><summary>{{ localLabel }}</summary><ResourceViewer :title="localLabel" :content="observation.next_call" /></details>
    <details v-if="observation.source_next_call"><summary>源端下一批参数</summary><p class="muted">{{ observation.source_next_call_note || '源端下一批，仅位置未取得。' }}</p><ResourceViewer title="源端下一批工具调用" :content="observation.source_next_call" /></details>
  </section>
</template>

<style scoped>
.observation-details{display:grid;gap:10px;min-width:0;font-size:13px;line-height:1.7}.observation-details p{margin:0;overflow-wrap:anywhere}.observation-facts{display:flex;align-items:center;gap:8px 14px;flex-wrap:wrap}.observation-facts>span,.coverage{color:var(--muted)}.observation-facts code{overflow-wrap:anywhere}.source-list,.source-item{display:grid;gap:6px;min-width:0}.source-item{padding:8px 0;border-bottom:1px solid var(--line)}.source-item>a{overflow-wrap:anywhere}.source-item>.muted{font-size:12px}.observation-details summary{cursor:pointer;color:rgb(var(--v-theme-primary));font-weight:500}.observation-details details[open]>summary{margin-bottom:12px}
.error-body{white-space:pre-wrap;overflow-wrap:anywhere}.call-id{overflow-wrap:anywhere;min-width:0}.field-errors h4{font-size:13px;margin:0 0 8px}.field-errors dl{display:grid;grid-template-columns:minmax(130px,1fr) 2fr;gap:10px 16px}.field-errors dt,.field-errors dd{min-width:0;overflow-wrap:anywhere}.field-errors dt span{display:block;color:var(--muted);font-size:12px}.field-errors dd{margin:0}@media(max-width:550px){.field-errors dl{grid-template-columns:1fr;gap:5px}.field-errors dd{margin-bottom:10px}}
</style>
