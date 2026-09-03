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
  } catch (e) { error.value = e.message }
}

async function cancelTask(id) {
  await api(`/api/cockpit/tasks/${id}/cancel`, { method: 'POST' })
  await load()
}
async function triggerTask(id) {
  await api(`/api/cockpit/tasks/${id}/trigger_now`, { method: 'POST' })
  await load()
}
async function resolveLoop(id) {
  await api(`/api/cockpit/loops/${id}/resolve`, { method: 'POST' })
  await load()
}
</script>

<template>
  <div>
    <h1>Tasks & Open Loops</h1>
    <p v-if="error" class="tag bad">{{ error }}</p>

    <h2>Scheduled Tasks</h2>
    <div class="panel">
      <table>
        <thead><tr><th>到期</th><th>Scene</th><th>描述</th><th>条件事件</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="t in tasks" :key="t.id">
            <td>{{ fmtTime(t.due_at) }}</td>
            <td>{{ t.scene_id }}</td>
            <td>{{ t.description }}</td>
            <td><span v-if="t.wake_event_type" class="tag warn">{{ t.wake_event_type }}</span><span v-else class="muted">定时</span></td>
            <td>{{ t.status }}</td>
            <td>
              <button v-if="t.status === 'pending'" @click="triggerTask(t.id)">立即触发</button>
              <button v-if="t.status === 'pending'" class="danger" @click="cancelTask(t.id)">取消</button>
            </td>
          </tr>
          <tr v-if="!tasks.length"><td colspan="6" class="muted">暂无任务</td></tr>
        </tbody>
      </table>
    </div>

    <h2>Active Open Loops</h2>
    <div class="panel">
      <table>
        <thead><tr><th>ID</th><th>Scene</th><th>等待对象</th><th>意图</th><th>创建</th><th>TTL</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="l in loops" :key="l.id">
            <td class="muted">{{ l.id }}</td>
            <td>{{ l.scene_id }}</td>
            <td>{{ l.target_actor_id }}</td>
            <td>{{ l.intent }}</td>
            <td>{{ fmtTime(l.created_at) }}</td>
            <td>{{ fmtTime(l.expires_at) }}</td>
            <td><button class="danger" @click="resolveLoop(l.id)">手动 resolve</button></td>
          </tr>
          <tr v-if="!loops.length"><td colspan="7" class="muted">暂无活跃 open loop</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
