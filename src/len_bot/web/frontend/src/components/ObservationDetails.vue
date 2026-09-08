<script setup>
import { computed } from 'vue'
import { fmtTime } from '../api.js'
import EntityLink from './EntityLink.vue'
import StatusBadge from './StatusBadge.vue'
import ResourceViewer from './ResourceViewer.vue'

const props = defineProps({ observation: { type: Object, required: true }, sceneId: String, rangeLabel: { type: String, default: '本次页面阅读范围' } })
const unit = computed(() => ({ characters: '字符', records: '记录' }[props.observation.coordinate_unit] || props.observation.coordinate_unit || '单位未记录'))
const localLabel = computed(() => props.observation.coordinate_unit === 'records' ? '本地记录续读参数' : '本地正文续读参数')
function sourceLink(url) { return /^https?:\/\//i.test(url || '') ? url : undefined }
</script>

<template>
  <section class="observation-details" aria-label="工具资料状态与来源">
    <div class="observation-facts"><StatusBadge domain="observation" :status="observation.status" /><code v-if="observation.error_code">{{ observation.error_code }}</code><span v-if="observation.fetched_at">取得于 {{ fmtTime(observation.fetched_at) }}</span><span v-if="observation.content_length!==undefined">已保存 {{ observation.content_length }} 字符</span></div>
    <p v-if="observation.coverage" class="coverage">覆盖记录：{{ observation.coverage }}</p>
    <p v-if="observation.displayed_range">{{ rangeLabel }}：{{ observation.displayed_range.start }}–{{ observation.displayed_range.end }} / {{ observation.displayed_range.total }} {{ unit }}（起含止不含）。</p>
    <p v-if="observation.source_truncated" class="text-warning">源端资料有未取得的部分，当前保存正文不代表源全文。</p>
    <p v-else-if="observation.truncated && observation.source_truncated===undefined" class="muted">原记录带截断标记，未分别记录源端和本地正文覆盖。</p>
    <p v-if="observation.attachments?.length" class="muted">媒体引用 {{ observation.attachments.length }} 项；仅登记引用不表示模型已看到原图。</p>
    <div v-if="observation.sources?.length" class="source-list"><div v-for="(source,index) in observation.sources" :key="index" class="source-item"><a v-if="sourceLink(source.url)" :href="sourceLink(source.url)" target="_blank" rel="noopener noreferrer">{{ source.title || source.url }}</a><span v-else-if="source.title || source.url">{{ source.title || source.url }}</span><EntityLink v-if="source.event_id" type="event" :id="source.event_id" :scene-id="sceneId" label="来源原话" /><span v-if="source.published_at" class="muted">源发布时间 {{ source.published_at }}</span></div></div>
    <details v-if="observation.next_call"><summary>{{ localLabel }}</summary><ResourceViewer :title="localLabel" :content="observation.next_call" /></details>
    <details v-if="observation.source_next_call"><summary>源端下一批参数</summary><p class="muted">{{ observation.source_next_call_note || '源端下一批，仅位置未取得。' }}</p><ResourceViewer title="源端下一批工具调用" :content="observation.source_next_call" /></details>
  </section>
</template>

<style scoped>
.observation-details{display:grid;gap:10px;min-width:0;font-size:13px;line-height:1.7}.observation-details p{margin:0;overflow-wrap:anywhere}.observation-facts{display:flex;align-items:center;gap:8px 14px;flex-wrap:wrap}.observation-facts>span,.coverage{color:var(--muted)}.observation-facts code{overflow-wrap:anywhere}.source-list,.source-item{display:grid;gap:6px;min-width:0}.source-item{padding:8px 0;border-bottom:1px solid var(--line)}.source-item>a{overflow-wrap:anywhere}.source-item>.muted{font-size:12px}.observation-details summary{cursor:pointer;color:rgb(var(--v-theme-primary));font-weight:500}.observation-details details[open]>summary{margin-bottom:12px}
</style>
