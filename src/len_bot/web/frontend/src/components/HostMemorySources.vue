<script setup>
import { computed, ref, watch } from 'vue'
import { api } from '../api.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'

const props = defineProps({ scene: { type: String, required: true }, target: { type: String, required: true } })
const emit = defineEmits(['selection'])
const queryText = ref(''), who = ref(''), activeQuery = ref(''), activeWho = ref('')
const rows = ref([]), selected = ref(new Map()), snapshot = ref(null), nextOffset = ref(null)
const searched = ref(false), loading = ref(false), error = ref('')
let generation = 0
const requestSelection = () => `${props.scene}\u0000${props.target}\u0000${generation}`
const beginRead = useRequestGuard(requestSelection)
const chosen = computed(() => [...selected.value.values()])
const maxSelected = 500
function reset() {
  ++generation
  rows.value = []; selected.value = new Map(); snapshot.value = null; nextOffset.value = null
  searched.value = false; loading.value = false; error.value = ''
  emit('selection', [])
}
watch(() => [props.scene, props.target], reset, { immediate: true })
function toggle(item, checked) {
  if (item.excluded || (checked && !selected.value.has(item.record) && selected.value.size >= maxSelected)) return
  const next = new Map(selected.value)
  if (checked) next.set(item.record, item)
  else next.delete(item.record)
  selected.value = next
  emit('selection', [...next.values()])
}
function search() {
  activeQuery.value = queryText.value
  activeWho.value = who.value
  rows.value = []; snapshot.value = null; nextOffset.value = null
  searched.value = true; error.value = ''
  ++generation
  read(false)
}
async function read(more) {
  if (more && (nextOffset.value === null || loading.value)) return
  const target = props.scene, text = activeQuery.value, sender = activeWho.value
  const offset = more ? nextOffset.value : 0
  const values = new URLSearchParams({ scene: target, offset: String(offset) })
  if (text !== '') values.set('query', text)
  if (sender !== '') values.set('who', sender)
  if (more) values.set('snapshot', String(snapshot.value))
  const fresh = beginRead()
  loading.value = true
  try {
    const result = await api(`/api/host/memory/sources?${values}`)
    if (!fresh()) return
    const nextChosen = new Map(selected.value)
    for (const item of result.previews) {
      if (item.excluded) nextChosen.delete(item.record)
    }
    if (nextChosen.size !== selected.value.size) {
      selected.value = nextChosen
      emit('selection', [...nextChosen.values()])
    }
    rows.value = more ? [...rows.value, ...result.previews] : result.previews
    snapshot.value = result.snapshot
    nextOffset.value = result.next_offset
    error.value = ''
  } catch (problem) { if (fresh()) error.value = problem.message }
  finally { if (fresh()) loading.value = false }
}
</script>

<template>
  <section class="source-picker" aria-labelledby="memory-sources-title">
    <h3 id="memory-sources-title">选择停止再次抽取的原消息</h3>
    <p class="muted">只选择确实属于本次定向遗忘的原聊天记录；不会跳过整个群或较早的无关消息。搜索、翻页不会清除已选项。</p>
    <form class="source-search" @submit.prevent="search"><v-text-field v-model="queryText" label="原话搜索（可空）" hide-details="auto" />
      <v-text-field v-model="who" label="发言 QQ（可空）" inputmode="numeric" hide-details="auto" />
      <v-btn type="submit" variant="outlined" :loading="loading" :disabled="loading">查询当前场景</v-btn></form>
    <v-alert v-if="error" type="error" variant="tonal" role="alert">来源查询失败：{{ error }}</v-alert>
    <p v-if="!searched" class="muted">输入原话或 QQ 后查询；也可留空查看当前场景的实际候选。</p>
    <p v-if="searched" class="muted">下方结果对应最近一次提交：原话 {{ activeQuery || '不限' }}；QQ {{ activeWho || '不限' }}。修改输入框后须再次查询。</p>
    <p v-if="searched && loading && !rows.length" class="muted" role="status">正在读取原消息预览…</p>
    <p v-else-if="searched && !rows.length && !error" class="muted">本次查询没有返回原消息。</p>
    <ul v-if="rows.length" class="source-list"><li v-for="item in rows" :key="item.record">
      <v-checkbox :model-value="selected.has(item.record)" :disabled="item.excluded || (!selected.has(item.record) && chosen.length>=maxSelected)" hide-details
        :label="item.excluded?'已排除 · 不计本次新增':'将此原消息加入本次排除'" @update:model-value="value=>toggle(item,value)" />
      <p class="source-meta">QQ {{ item.sender_qq }} · {{ item.send_status }}<span v-if="item.platform_message_id"> · 平台消息 {{ item.platform_message_id }}</span></p>
      <p class="original-text">{{ item.text }}</p>
      <p v-if="item.next_offset!==null" class="muted">仅显示原文预览 {{ item.offset }} 至 {{ item.next_offset }} / {{ item.total_chars }} 字符；选择以该条实际消息为单位，不把预览当全文。</p>
      <p v-else class="muted">原文 {{ item.total_chars }} 字符</p>
    </li></ul>
    <v-btn v-if="nextOffset!==null" variant="outlined" :loading="loading" :disabled="loading" @click="read(true)">读取下一页（10 条）</v-btn>
    <div class="chosen"><h4>本次明确选择 · {{ chosen.length }} 条</h4>
      <p v-if="chosen.length>=maxSelected" class="muted">已达到单次最多 500 条来源选择；可先移除部分选择。此限制不扩大为整群排除。</p>
      <p v-if="!chosen.length" class="muted">未选原消息：若继续 forget，只移除目标文件及可访问历史版本，旧原话仍可能参与后续抽取。</p>
      <ul v-else><li v-for="item in chosen" :key="item.record"><div><strong>QQ {{ item.sender_qq }}</strong> · {{ item.send_status }}<p class="original-text">{{ item.text }}</p></div>
        <v-btn variant="text" @click="toggle(item,false)">移除此选择</v-btn></li></ul>
    </div>
  </section>
</template>

<style scoped>
.source-picker{border:1px solid var(--line);border-radius:10px;padding:14px;margin:14px 0;min-width:0;overflow-wrap:anywhere}.source-picker h3{font-size:16px;margin:0 0 8px}.source-picker h4{font-size:15px;margin:0 0 8px}
.source-search{display:flex;gap:10px;flex-wrap:wrap;align-items:start;margin:12px 0}.source-search>:not(.v-btn){flex:1 1 210px;min-width:0}.source-list,.chosen ul{list-style:none;padding:0;margin:0;display:grid;gap:10px}
.source-list li,.chosen li{border:1px solid var(--line);border-radius:8px;padding:10px;min-width:0;overflow-wrap:anywhere}.source-meta{font-size:13px;color:var(--muted);margin:4px 0}.original-text{white-space:pre-wrap;overflow-wrap:anywhere;margin:6px 0}
.chosen{border-top:1px solid var(--line);margin-top:16px;padding-top:14px}.chosen li{display:flex;justify-content:space-between;align-items:flex-start;gap:10px;flex-wrap:wrap}.chosen li>div{min-width:0;flex:1 1 220px}
.source-picker :deep(.v-btn){min-height:44px}.source-picker :deep(.v-alert),.source-picker .muted{overflow-wrap:anywhere}
@media(max-width:600px){.source-search>.v-btn{width:100%}.source-picker{padding:12px}}
</style>
