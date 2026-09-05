<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api.js'
const jobs = ref([]), error = ref(''), scene = ref(''), selected = ref(null)
const goal = ref(''), constraints = ref(''), resource = ref(null), resourceText = ref('')
const states = { pending: '等待执行', claimed: '正在启动', processing: '正在查询', result_ready: '结果待回应', awaiting_delivery: '等待送达', completed: '已送达', cancelled: '已取消', failed: '失败', review_required: '中断待核对', delivery_unknown: '送达不确定', shadow_observed: '已记录候选' }
const terminal = ['completed', 'cancelled', 'delivery_unknown', 'shadow_observed']
async function load() {
  try { jobs.value = await api('/api/cockpit/jobs' + (scene.value ? '?scene_id=' + encodeURIComponent(scene.value) : '')); error.value = '' }
  catch (e) { error.value = e.message }
}
function select(job) { selected.value = job; goal.value = job.goal; constraints.value = job.constraints.join('\n'); resource.value = null; resourceText.value = '' }
async function control(operation) {
  try {
    const job = selected.value
    const items = [...new Set(constraints.value.split('\n').map(v => v.trim()).filter(Boolean))]
    const res = await api(`/api/cockpit/jobs/${encodeURIComponent(job.id)}/${operation}`, { method: 'POST', body: JSON.stringify({
      expected_revision: job.revision, goal: operation === 'revise' ? goal.value : null,
      constraints_add: operation === 'revise' ? items.filter(v => !job.constraints.includes(v)) : [],
      constraints_remove: operation === 'revise' ? job.constraints.filter(v => !items.includes(v)) : [],
    }) })
    select(res.job); await load()
  } catch (e) { error.value = e.message }
}
async function readResult(id, offset = 0) {
  try {
    const res = await api(`/api/cockpit/tool-results/${encodeURIComponent(id)}?scene_id=${encodeURIComponent(selected.value.scene_id)}&offset=${offset}`)
    resource.value = res; resourceText.value = offset ? resourceText.value + res.content : res.content
  } catch (e) { error.value = e.message }
}
onMounted(load)
</script>
<template>
  <div>
    <div class="toolbar"><h1>信息工作</h1><input v-model="scene" placeholder="按群聊筛选，例如 group:123" /><button @click="load">刷新</button></div>
    <p class="muted">查看正在查的资料，补充要求或停止工作。查询结束与回复送达分别记录。</p>
    <p v-if="error" class="tag bad" role="alert">{{ error }}</p>
    <div class="panel"><table><thead><tr><th>目标</th><th>群聊</th><th>状态</th><th>使用量</th><th></th></tr></thead><tbody>
      <tr v-for="job in jobs" :key="job.id"><td>{{ job.goal }}</td><td>{{ job.scene_id }}</td><td>{{ states[job.status] || job.status }}<span v-if="job.result"> · {{ job.result.status }}</span></td><td>{{ job.model_steps }} 次模型 / {{ job.tool_calls }} 次工具</td><td><button @click="select(job)">查看</button></td></tr>
      <tr v-if="!jobs.length"><td colspan="5" class="muted">当前没有信息工作</td></tr>
    </tbody></table></div>
    <div v-if="selected" class="panel" style="margin-top:20px">
      <h2>工作详情</h2><p class="muted">{{ selected.id }} · 版本 {{ selected.revision }} · 已用 {{ selected.elapsed_seconds.toFixed(1) }} 秒</p>
      <label>目标<textarea v-model="goal" rows="2" /></label>
      <label>要求（每行一条）<textarea v-model="constraints" rows="3" /></label>
      <div class="toolbar"><button :disabled="terminal.includes(selected.status)" @click="control('revise')">保存修订</button><button :disabled="!['review_required','failed'].includes(selected.status)" @click="control('resume')">核对后恢复</button><button class="danger" :disabled="terminal.includes(selected.status)" @click="control('cancel')">停止工作</button></div>
      <p v-if="selected.result">{{ selected.result.summary }}</p>
      <ul v-if="selected.result?.unresolved?.length"><li v-for="item in selected.result.unresolved" :key="item">{{ item }}</li></ul>
      <h3>已取得资料</h3><div class="toolbar"><button v-for="id in selected.result_ids" :key="id" @click="readResult(id)">{{ id.slice(0,12) }}</button><span v-if="!selected.result_ids.length" class="muted">尚无资料</span></div>
      <div v-if="resource"><p class="muted">{{ resource.status }} · {{ new Date(resource.fetched_at * 1000).toLocaleString() }}</p><p v-for="source in resource.sources" :key="source.url">{{ source.title }} {{ source.url }}</p><pre>{{ resourceText }}</pre><button v-if="resource.next_offset != null" @click="readResult(resource.result_id, resource.next_offset)">继续读取</button></div>
    </div>
  </div>
</template>
<style scoped>label { display:block; margin:12px 0 } textarea { display:block; width:100%; margin-top:6px } pre { white-space:pre-wrap; overflow-wrap:anywhere; max-height:480px; overflow:auto } td:first-child { max-width:360px; overflow-wrap:anywhere }</style>
