<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api, fmtTime } from '../api.js'
import EntityLink from './EntityLink.vue'

const props = defineProps({ sceneId: { type: String, required: true } })
const state = ref(null), loading = ref(false), readError = ref(''), readAt = ref(null)
const busyScene = ref(''), actionError = ref(''), feedback = ref('')
let request = 0, operation = 0
const busy = computed(() => Boolean(busyScene.value))
const count = value => value === null || value === undefined ? '未取得' : String(value)
const rebuildLabels = { indexed: '此重建请求完成', cancelled: '因场景退出而停止', error: '构建失败', disabled: '未开始构建', stale_profile: '绑定已变化，未采用旧绑定结果' }
async function refresh() {
  const scene = props.sceneId, seq = ++request
  if (!scene) return
  loading.value = true; readError.value = ''
  try {
    const result = await api('/api/cockpit/memory-index?scene_id=' + encodeURIComponent(scene))
    if (seq !== request || scene !== props.sceneId) return
    state.value = result; readAt.value = result.sampled_at
  } catch (error) { if (seq === request && scene === props.sceneId) readError.value = error.message }
  finally { if (seq === request) loading.value = false }
}
async function rebuild() {
  const scene = props.sceneId
  if (!scene || busy.value || !state.value?.enabled || readError.value) return
  if (!window.confirm(`为 ${scene} 重建认识与已完成摘要的派生索引？这会将本场景相关文本发给已配置的嵌入服务并产生调用；不会改写原记录。`)) return
  const seq = ++operation
  busyScene.value = scene; actionError.value = ''; feedback.value = ''
  try {
    const result = await api('/api/cockpit/memory-index/rebuild?scene_id=' + encodeURIComponent(scene), { method: 'POST' })
    if (seq !== operation || scene !== props.sceneId) return
    feedback.value = `此重建请求完成，写入 ${result.indexed} 条派生索引；当前覆盖以重新读取的状态为准。`
  } catch (error) { if (seq === operation && scene === props.sceneId) actionError.value = error.message }
  finally {
    if (seq === operation) {
      busyScene.value = ''
      if (scene === props.sceneId) await refresh()
    }
  }
}
watch(() => props.sceneId, () => {
  state.value = null; readAt.value = null; readError.value = ''; actionError.value = ''; feedback.value = ''
  refresh()
}, { immediate: true })
onBeforeUnmount(() => { ++request; ++operation })
defineExpose({ refresh })
</script>

<template>
  <v-card class="memory-index">
    <v-card-text>
      <div class="index-heading"><h2>本场景的检索索引</h2><v-btn size="small" variant="text" :loading="loading" @click="refresh">刷新索引状态</v-btn></div>
      <p class="index-note">范围：{{ sceneId }}。这里只读状态，不会自动重建；覆盖统计不随人物、类型或内容筛选变化。</p>
      <v-alert v-if="readError" type="error" variant="tonal" class="my-3">索引状态读取失败：{{ readError }}<div v-if="readAt">保留读取于 {{ fmtTime(readAt) }} 的状态；这不代表认识列表读取失败。</div></v-alert>
      <p v-if="loading && !state" class="index-note">正在读取原表数量与派生索引覆盖…</p>
      <template v-if="state">
        <p class="index-note">{{ state.reason || '本场景已开启语义检索且绑定已配置；是否能完成请求仍以实际调用为准。' }}</p>
        <p v-if="state.profile" class="index-note">当前编码绑定：{{ state.profile.provider_id }} / {{ state.profile.model }} · {{ state.profile.dimension ?? '服务返回的' }} 维</p>
        <div class="coverage-grid">
          <div><strong>当前有效认识</strong><span>{{ count(state.indexed) }} / {{ count(state.total) }} 条匹配当前修订与绑定</span><small>尚未匹配：{{ count(state.pending) }} 条</small></div>
          <div><strong>已完成历史摘要</strong><span>{{ count(state.summary_coverage?.indexed) }} / {{ count(state.summary_coverage?.total) }} 条匹配当前版本与绑定</span><small>尚未匹配：{{ count(state.summary_coverage?.pending) }} 条</small></div>
        </div>
        <p class="index-note">索引是派生数据，未覆盖不等于没有记忆；覆盖完整也不证明内容正确或所有原话都已总结。读取于 {{ fmtTime(readAt) }}。</p>
        <div v-if="state.last_rebuild" class="index-record">
          <strong>最近一次显式重建：{{ rebuildLabels[state.last_rebuild.status] || state.last_rebuild.status }}</strong>
          <p>{{ fmtTime(state.last_rebuild.created_at) }} · 已写入 {{ count(state.last_rebuild.indexed) }} 条</p>
          <p v-if="state.last_rebuild.error" class="error-copy">{{ state.last_rebuild.error }}</p>
          <EntityLink type="trace" :id="state.last_rebuild.trace_id" :scene-id="sceneId" label="查看本次重建记录" />
        </div>
        <details v-if="state.last_error" class="index-record"><summary>最近保存的索引错误（历史记录，不代表当前仍失败）</summary><p>{{ fmtTime(state.last_error.created_at) }}</p><p class="error-copy">{{ state.last_error.error || state.last_error.error_type }}</p><EntityLink v-if="state.last_error.trace_id" type="trace" :id="state.last_error.trace_id" :scene-id="sceneId" label="查看错误记录" /></details>
      </template>
      <v-alert v-if="actionError" type="error" variant="tonal" class="my-3" role="alert">重建未确认完成：{{ actionError }}。已经写入的派生索引不会因此回滚；请查看当前覆盖与原记录，不自动重试。</v-alert>
      <v-alert v-if="feedback" type="info" variant="tonal" class="my-3" role="status">{{ feedback }}</v-alert>
      <p v-if="busy" class="index-note">{{ busyScene }} 的重建请求仍待返回。切换页面不表示服务器已取消。</p>
      <v-btn variant="outlined" size="small" :loading="busy && busyScene === sceneId" :disabled="busy || loading || !state?.enabled || Boolean(readError)" @click="rebuild">重建此场景索引</v-btn>
    </v-card-text>
  </v-card>
</template>

<style scoped>
.memory-index{margin-top:20px}.index-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.index-heading h2{font-size:16px}.index-note{font-size:13px;line-height:1.7;color:var(--muted);margin:12px 0;overflow-wrap:anywhere}.coverage-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;margin:16px 0}.coverage-grid>div{display:grid;gap:7px;min-width:0;font-size:13px}.coverage-grid small{color:var(--muted)}.index-record{margin:16px 0;font-size:13px;line-height:1.7;overflow-wrap:anywhere}.index-record summary{cursor:pointer}.index-record p{margin:8px 0;white-space:pre-wrap}.error-copy{color:rgb(var(--v-theme-error))}@media(max-width:650px){.coverage-grid{grid-template-columns:1fr}}
</style>
