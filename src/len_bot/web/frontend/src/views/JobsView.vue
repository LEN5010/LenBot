<script setup>
import { ref, onMounted } from 'vue'
import { api, fmtTime } from '../api.js'
const jobs = ref([]), error = ref(''), scene = ref(''), selected = ref(null)
const skillData = ref({ skills: [], candidates: [] }), selectedSkill = ref(null), skillVersion = ref(1), publishingSkill = ref(false), publishConfirming = ref(false)
const goal = ref(''), constraints = ref(''), resource = ref(null), resourceText = ref('')
const states = { pending: '等待执行', claimed: '正在启动', processing: '正在查询', result_ready: '结果待回应', awaiting_delivery: '等待送达', completed: '已送达', cancelled: '已取消', failed: '失败', review_required: '中断待核对', delivery_unknown: '送达不确定', shadow_observed: '已记录候选' }
const executionStates = { pending: '尚未开始', running: '执行中', completed: '执行完成', partial: '部分完成', failed: '执行失败', interrupted: '执行中断', cancelled: '执行取消', unknown: '待核对' }
const terminal = ['completed', 'cancelled', 'delivery_unknown', 'shadow_observed']
async function load() {
  try {
    const suffix = scene.value ? '?scene_id=' + encodeURIComponent(scene.value) : ''
    const [jobRows, skills] = await Promise.all([api('/api/cockpit/jobs' + suffix), api('/api/cockpit/skills' + suffix)])
    jobs.value = jobRows; skillData.value = skills; error.value = ''
    if (selected.value) selected.value = jobRows.find(job => job.id === selected.value.id) || selected.value
  }
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
async function readSkill(skill, version = skill.version) {
  try {
    selectedSkill.value = await api(`/api/cockpit/skills/${encodeURIComponent(skill.id)}?scene_id=${encodeURIComponent(skill.scene_id)}&version=${version}`)
    skillVersion.value = selectedSkill.value.version; publishConfirming.value = false
  } catch (e) { error.value = e.message }
}
async function publishSkill() {
  publishingSkill.value = true
  try {
    const skill = selectedSkill.value
    const result = await api(`/api/cockpit/skills/${encodeURIComponent(skill.id)}/publish`, { method: 'POST', body: JSON.stringify({ scene_id: skill.scene_id, expected_version: skill.version }) })
    selectedSkill.value = result.skill; publishConfirming.value = false; await load()
  } catch (e) { error.value = e.message } finally { publishingSkill.value = false }
}
function candidateStatus(status) { return { pending: '等待整理', processing: '正在整理', saved: '已保存技能', failed: '整理失败', interrupted: '维护中断', obsolete: '来源版本已过期' }[status] || status }
function compressionStatus(status) { return { completed: '已压缩', failed: '压缩失败', archived: '正文已改为引用', pending: '待压缩', idle: '尚未压缩' }[status] || status || '尚未压缩' }
onMounted(load)
</script>
<template>
  <div>
    <div class="toolbar"><h1>信息工作</h1><input v-model="scene" placeholder="按群聊筛选，例如 group:123" /><button @click="load">刷新</button></div>
    <p class="muted">查看正在查的资料，补充要求或停止工作。查询结束与回复送达分别记录。</p>
    <p v-if="error" class="tag bad" role="alert">{{ error }}</p>
    <div class="panel table-scroll"><table><thead><tr><th>目标</th><th>群聊</th><th>状态</th><th>使用量</th><th></th></tr></thead><tbody>
      <tr v-for="job in jobs" :key="job.id"><td>{{ job.goal }}</td><td>{{ job.scene_id }}</td><td><span :class="{ 'bad-text': job.execution_status === 'failed' }">{{ executionStates[job.execution_status] || job.execution_status }}</span><br /><span class="muted">{{ states[job.status] || job.status }}</span></td><td>{{ job.model_steps }} 次模型 / {{ job.tool_calls }} 次工具</td><td><button @click="select(job)">查看</button></td></tr>
      <tr v-if="!jobs.length"><td colspan="5" class="muted">当前没有信息工作</td></tr>
    </tbody></table></div>
    <div v-if="selected" class="panel" style="margin-top:20px">
      <div class="panel-header"><h2>工作详情</h2><button @click="selected = null">关闭</button></div><p class="muted">{{ selected.id }} · 版本 {{ selected.revision }} · 已用 {{ selected.elapsed_seconds.toFixed(1) }} 秒</p>
      <p>{{ executionStates[selected.execution_status] || selected.execution_status }} · {{ states[selected.status] || selected.status }}</p>
      <div class="work-grid">
        <section><h3>持久进度</h3><p v-if="!selected.work_state" class="muted">尚无已保存的进度</p><template v-else><p class="muted">进度依据目标版本 {{ selected.work_state.goal_revision }}<span v-if="selected.work_state.goal_revision !== selected.revision" class="tag warn">旧版本，需重新判断</span></p><h4>计划</h4><ol><li v-for="item in selected.work_state.plan" :key="item">{{ item }}</li></ol><h4>已完成步骤与依据</h4><ul><li v-for="item in selected.work_state.completed_steps" :key="item.step">{{ item.step }}<div><button v-for="id in item.result_ids" :key="id" class="small-btn" @click="readResult(id)">{{ id.slice(0,12) }}</button></div></li></ul><h4>待解决</h4><ul><li v-for="item in selected.work_state.unresolved" :key="item">{{ item }}</li></ul><p>下一步：{{ selected.work_state.next_step || '未提供' }}</p><div class="toolbar"><span class="muted">关键资料</span><button v-for="id in selected.work_state.key_result_ids" :key="id" @click="readResult(id)">{{ id.slice(0,12) }}</button></div></template></section>
        <section><h3>预算与续接</h3><div class="kv"><span class="k">模型请求</span><span>{{ selected.model_steps }} / {{ selected.budget.max_model_steps }}</span></div><div class="kv"><span class="k">工具调用</span><span>{{ selected.tool_calls }} / {{ selected.budget.max_tool_calls }}</span></div><div class="kv"><span class="k">运行秒数</span><span>{{ selected.elapsed_seconds.toFixed(1) }} / {{ selected.budget.max_seconds }}</span></div><p class="muted">维护压缩占用同一工作预算；暂停与恢复保留已用量。</p><p>绑定模型：{{ selected.model_binding ? `${selected.model_binding.provider_id} / ${selected.model_binding.model}` : '尚未绑定' }}</p><p v-if="selected.model_binding" class="muted">推理：{{ selected.model_binding.reasoning_effort || '模型默认' }}</p><p v-if="selected.checkpoint">最近完整检查点：{{ selected.checkpoint.exchange_count }} 组工具交换 · 目标版本 {{ selected.checkpoint.goal_revision }} · {{ fmtTime(selected.checkpoint.updated_at) }}</p><p v-else class="muted">尚无完整工具交换检查点</p><h4>工作上下文压缩</h4><p>{{ compressionStatus(selected.compression?.status) }} · 有效输入预算 {{ selected.budget.effective_input_tokens.toLocaleString() }} token</p><p class="muted">总窗口 {{ selected.budget.context_tokens.toLocaleString() }}，输出预留 {{ selected.budget.output_tokens.toLocaleString() }}；{{ Math.round(selected.budget.compression_trigger * 100) }}% 触发，目标 {{ Math.round(selected.budget.compression_target * 100) }}%。</p><p v-if="selected.compression?.error" class="bad-text">{{ selected.compression.error }}</p><details v-for="(segment,index) in selected.compression?.segments || []" :key="index"><summary>已压缩工具交换 {{ segment.start_exchange }}—{{ segment.end_exchange }}</summary><p>{{ segment.summary }}</p><button v-for="id in segment.result_ids" :key="id" @click="readResult(id)">{{ id.slice(0,12) }}</button></details><h4>本工作已读取技能</h4><p v-for="(version,id) in selected.skill_versions" :key="id"><code>{{ id }}</code> · v{{ version }}</p><p v-if="!Object.keys(selected.skill_versions || {}).length" class="muted">尚未读取技能正文</p></section>
      </div>
      <label>目标<textarea v-model="goal" rows="2" /></label>
      <label>要求（每行一条）<textarea v-model="constraints" rows="3" /></label>
      <div class="toolbar"><button :disabled="terminal.includes(selected.status)" @click="control('revise')">保存修订</button><button :disabled="!selected.can_resume" @click="control('resume')">核对后恢复</button><button class="danger" :disabled="terminal.includes(selected.status)" @click="control('cancel')">停止工作</button></div>
      <p v-if="selected.result">{{ selected.result.summary }}</p>
      <ul v-if="selected.result?.unresolved?.length"><li v-for="item in selected.result.unresolved" :key="item">{{ item }}</li></ul>
      <h3>已取得资料</h3><div class="toolbar"><button v-for="id in selected.result_ids" :key="id" @click="readResult(id)">{{ id.slice(0,12) }}</button><span v-if="!selected.result_ids.length" class="muted">尚无资料</span></div>
      <div v-if="resource"><p class="muted">{{ resource.status }} · {{ new Date(resource.fetched_at * 1000).toLocaleString() }}</p><p v-for="source in resource.sources" :key="source.url">{{ source.title }} {{ source.url }}</p><pre>{{ resourceText }}</pre><button v-if="resource.next_offset != null" @click="readResult(resource.result_id, resource.next_offset)">继续读取</button></div>
    </div>
    <section class="panel skills-panel">
      <h2>可复用的方法技能</h2><p class="muted">自动整理的技能在来源场景使用，保留版本和依据；工作按需读取正文。</p>
      <div class="table-scroll"><table><thead><tr><th>技能 / 适用条件</th><th>来源场景 / 维护者</th><th>版本</th><th>来源</th><th></th></tr></thead><tbody><tr v-for="skill in skillData.skills" :key="skill.id"><td>{{ skill.name }}<p class="muted">{{ skill.applicability }}</p></td><td>{{ skill.scene_id }}<p class="muted">{{ skill.author === 'human' ? '人工维护' : '工作经验' }} · {{ skill.scope === 'global-safe' ? '已公开' : '仅本场景' }}</p></td><td>v{{ skill.version }}</td><td><code>{{ skill.source?.job_id || skill.source?.authored_source || '—' }}</code><small v-if="skill.source?.job_revision">工作版本 {{ skill.source.job_revision }}</small></td><td><button @click="readSkill(skill)">查看正文</button></td></tr><tr v-if="!skillData.skills.length"><td colspan="5" class="muted">暂无可用技能</td></tr></tbody></table></div>
      <details v-if="skillData.candidates.length"><summary>经验候选与整理状态</summary><article v-for="item in skillData.candidates" :key="item.id"><p><span class="tag" :class="item.status === 'saved' ? 'ok' : 'warn'">{{ candidateStatus(item.status) }}</span> {{ item.job_id }} · 工作版本 {{ item.job_revision }}</p><p>{{ item.candidate.name }} · {{ item.candidate.lesson }}</p><p v-if="item.error" class="bad-text">{{ item.error }}</p></article></details>
    </section>
    <section v-if="selectedSkill" class="panel skills-panel">
      <div class="panel-header"><div><h2>{{ selectedSkill.name }} · v{{ selectedSkill.version }}</h2><p class="muted">{{ selectedSkill.scene_id }} · {{ fmtTime(selectedSkill.updated_at) }}</p></div><button @click="selectedSkill = null">关闭</button></div>
      <form class="toolbar" @submit.prevent="readSkill(selectedSkill,skillVersion)"><label>读取历史版本<input v-model.number="skillVersion" type="number" min="1" required /></label><button>读取版本</button></form>
      <p>{{ selectedSkill.applicability }}</p><h3>步骤</h3><ol><li v-for="step in selectedSkill.steps" :key="step">{{ step }}</li></ol><h3>验证要求</h3><ul><li v-for="item in selectedSkill.verification" :key="item">{{ item }}</li></ul><h3>不适用条件</h3><ul><li v-for="item in selectedSkill.exclusions" :key="item">{{ item }}</li></ul><h3>来源工作与纠正</h3><pre>{{ JSON.stringify(selectedSkill.source, null, 2) }}</pre>
      <template v-if="selectedSkill.scope !== 'global-safe'"><button v-if="!publishConfirming" @click="publishConfirming = true">公开此版本</button><div v-else class="notice"><p>公开后所有场景的工作都可以读取当前技能正文和来源信息。请核对当前版本内容。</p><button :disabled="publishingSkill" @click="publishSkill">{{ publishingSkill ? '正在公开…' : '确认公开当前版本' }}</button><button @click="publishConfirming = false">取消</button></div></template>
    </section>
  </div>
</template>
<style scoped>.work-grid { display:grid;grid-template-columns:1fr 1fr;gap:26px;margin:20px 0 }.work-grid section { min-width:0 }.work-grid li,.skills-panel li { margin:8px 0;line-height:1.6 }.work-grid p { overflow-wrap:anywhere }.table-scroll { overflow-x:auto }.skills-panel { margin-top:20px }.skills-panel td { vertical-align:top;overflow-wrap:anywhere }.skills-panel table { min-width:760px }.skills-panel button { white-space:nowrap }.skills-panel td:last-child { min-width:100px }.skills-panel small { display:block;margin-top:6px;color:var(--muted) }@media(max-width:1000px){.work-grid{grid-template-columns:1fr}}label { display:block; margin:12px 0 } textarea { display:block; width:100%; margin-top:6px } pre { white-space:pre-wrap; overflow-wrap:anywhere; max-height:480px; overflow:auto } td:first-child { max-width:360px; overflow-wrap:anywhere }</style>
