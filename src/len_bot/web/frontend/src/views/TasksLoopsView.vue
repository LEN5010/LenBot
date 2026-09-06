<script setup>
import { ref, onMounted } from 'vue'
import { api, fmtTime } from '../api.js'

const tasks = ref([])
const loops = ref([])
const error = ref('')
const editing = ref(null)
const editTime = ref('')
const editDescription = ref('')
const editable = status => ['pending', 'claimed', 'processing', 'result_ready', 'review_required'].includes(status)

onMounted(load)
async function load() {
  try {
    tasks.value = await api('/api/cockpit/tasks')
    loops.value = await api('/api/cockpit/loops?status=active')
  } catch (e) {
    error.value = e.message
  }
}

async function cancelTask(id) {
  if (!confirm('确认取消该任务？')) return
  try {
    await api(`/api/cockpit/tasks/${id}/cancel`, { method: 'POST' })
    await load()
  } catch (e) { error.value = e.message }
}

async function triggerTask(id) {
  try {
    await api(`/api/cockpit/tasks/${id}/trigger_now`, { method: 'POST' })
    await load()
  } catch (e) { error.value = e.message }
}

async function resolveLoop(id) {
  if (!confirm('确认手动闭环该社交等待？')) return
  await api(`/api/cockpit/loops/${id}/resolve`, { method: 'POST' })
  await load()
}

function startEdit(task) {
  editing.value = task
  editDescription.value = task.description
  const date = new Date(task.due_at * 1000)
  editTime.value = new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16)
}
async function saveEdit() {
  try {
    await api('/api/cockpit/tasks/' + editing.value.id + '/update', {method: 'POST', body: JSON.stringify({
      due_at: new Date(editTime.value).getTime()/1000, description: editDescription.value,
    })})
    editing.value = null
    await load()
  } catch (e) { error.value = e.message }
}
function taskStatus(value) {
  return ({pending: '等待开始', claimed: '已领取', processing: '正在处理', result_ready: '已有结果',
    awaiting_delivery: '等待发送确认', completed: '已兑现', cancelled: '已取消', failed: '失败',
    delivery_unknown: '发送结果不确定，不会自动重发', review_required: '需要重新核对', shadow_observed: '仅观察已记录，未真实履约'})[value] || '需要核对'
}
</script>

<template>
  <div class="tasks-loops-view">
    <div class="toolbar">
      <div class="page-title">
        <h1>计划与等待</h1>
        <p class="muted">查看机器人准备稍后处理的事情，以及正在等待谁回复。</p>
      </div>
      <button class="primary" @click="load">
        <span>刷新</span>
      </button>
    </div>

    <p v-if="error" class="tag bad">{{ error }}</p>

    <form v-if="editing" class="panel" @submit.prevent="saveEdit">
      <h2>修改要做的事</h2>
      <label>事情内容<input v-model="editDescription" required /></label>
      <label>执行时间（当前浏览器时区）<input type="datetime-local" v-model="editTime" required /></label>
      <button class="primary">保存修改</button>
      <button type="button" @click="editing = null">返回</button>
    </form>
    <!-- Scheduled Tasks Panel -->
    <div class="panel">
      <div class="panel-header">
        <h2>稍后要做的事</h2>
        <span class="tag">共 {{ tasks.length }} 项任务</span>
      </div>
      <table>
        <thead>
          <tr>
            <th>预定执行时间</th>
            <th>会话场景</th>
            <th>要做什么</th>
            <th>何时触发</th>
            <th>当前状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="t in tasks" :key="t.id">
            <td>{{ fmtTime(t.due_at) }}</td>
            <td><code>{{ t.scene_id }}</code></td>
            <td class="highlight">{{ t.description }}<p class="muted" v-if="t.payload?.result">结果：{{ t.payload.result }}</p><p class="tag bad" v-if="t.payload?.error">{{ t.payload.error }}</p></td>
            <td>
              <span v-if="t.wake_event_type" class="tag warn">
                {{ ({LIVE_STARTED: '直播开始', LIVE_ENDED: '直播结束', TOOL_COMPLETED: '工具返回'})[t.wake_event_type] || '指定事件发生' }}
              </span>
              <span v-else class="muted">仅定时到期</span>
            </td>
            <td>
              <span class="tag" :class="t.status === 'pending' ? 'ok' : t.status === 'claimed' ? 'warn' : ''">
                {{ taskStatus(t.status) }}
              </span>
            </td>
            <td>
              <div class="action-btn-group" v-if="editable(t.status) && t.payload?.kind !== 'agent_job'">
                <button v-if="t.status === 'pending'" class="small-btn primary" @click="triggerTask(t.id)">立即触发</button>
                <button class="small-btn" @click="startEdit(t)">修改</button>
                <button class="small-btn danger" @click="cancelTask(t.id)">取消</button>
              </div>
              <span v-else-if="t.payload?.kind === 'agent_job'" class="muted">请在信息工作中管理</span>
              <span v-else class="muted">—</span>
            </td>
          </tr>
          <tr v-if="!tasks.length">
            <td colspan="6" class="muted" style="text-align: center; padding: 24px;">当前无待调度任务</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Active Open Loops Panel -->
    <div class="panel" style="margin-top: 24px;">
      <div class="panel-header">
        <h2>正在等待的回复</h2>
        <span class="tag ok">共 {{ loops.length }} 条等待</span>
      </div>
      <table>
        <thead>
          <tr>
            <th>编号</th>
            <th>群聊</th>
            <th>正在等谁</th>
            <th>在等什么</th>
            <th>发起时间</th>
            <th>等待截止时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="l in loops" :key="l.id">
            <td class="muted"><code>{{ l.id }}</code></td>
            <td><code>{{ l.scene_id }}</code></td>
            <td class="highlight">{{ l.target_actor_id }}</td>
            <td>{{ l.intent }}</td>
            <td>{{ fmtTime(l.created_at) }}</td>
            <td>{{ fmtTime(l.expires_at) }}</td>
            <td>
              <button class="small-btn danger" @click="resolveLoop(l.id)">结束等待</button>
            </td>
          </tr>
          <tr v-if="!loops.length">
            <td colspan="7" class="muted" style="text-align: center; padding: 24px;">当前没有正在等待的回复</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.page-title h1 {
  margin: 0;
  font-size: 1.4rem;
}
.page-title p {
  margin: 4px 0 0;
  font-size: 0.85rem;
}

.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 14px;
}
.panel-header h2 {
  margin: 0;
}

.action-btn-group {
  display: flex;
  gap: 6px;
  align-items: center;
}

.small-btn {
  padding: 3px 8px;
  font-size: 0.78rem;
}

.highlight {
  color: var(--text);
  font-weight: 500;
}
</style>
