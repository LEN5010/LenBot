<script setup>
import { ref, onMounted } from 'vue'
import { api, fmtTime } from '../api.js'

const tasks = ref([])
const loops = ref([])
const error = ref('')

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
  await api(`/api/cockpit/tasks/${id}/cancel`, { method: 'POST' })
  await load()
}

async function triggerTask(id) {
  await api(`/api/cockpit/tasks/${id}/trigger_now`, { method: 'POST' })
  await load()
}

async function promoteTask(id) {
  await api(`/api/cockpit/tasks/${id}/promote`, { method: 'POST' })
  await load()
}

async function resolveLoop(id) {
  if (!confirm('确认手动闭环该社交等待？')) return
  await api(`/api/cockpit/loops/${id}/resolve`, { method: 'POST' })
  await load()
}

function taskStatus(value) {
  return value === 'pending' ? '等待中' : value === 'claimed' ? '执行中' : value === 'completed' ? '已完成' : value === 'cancelled' ? '已取消' : value
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
            <td class="highlight">{{ t.description }}</td>
            <td>
              <span v-if="t.wake_event_type" class="tag warn">
                {{ t.wake_event_type }}
              </span>
              <span v-else class="muted">仅定时到期</span>
            </td>
            <td>
              <span class="tag" :class="t.status === 'pending' ? 'ok' : t.status === 'claimed' ? 'warn' : ''">
                {{ taskStatus(t.status) }}
              </span>
            </td>
            <td>
              <div class="action-btn-group" v-if="t.status === 'pending'">
                <button class="small-btn primary" @click="triggerTask(t.id)">立即触发</button>
                <button class="small-btn" v-if="t.wake_event_type" @click="promoteTask(t.id)">解除条件</button>
                <button class="small-btn danger" @click="cancelTask(t.id)">取消</button>
              </div>
              <span v-else class="muted">已经结束</span>
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
