<script setup>
import { ref, watch } from 'vue'
import { api } from '../api.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'

const props = defineProps({
  scene: { type: String, required: true },
  taskId: { type: Number, required: true },
  status: { type: String, required: true },
  container: { type: String, default: null },
  configured: { type: Boolean, required: true },
  timezone: { type: String, required: true },
})
const snapshot = ref(null), loading = ref(false), readError = ref(''), stale = ref(false)
const beginRead = useRequestGuard(() => `${props.scene}\u0000${props.taskId}\u0000${props.configured}`)
watch(() => [props.status, props.container], () => { if (snapshot.value) stale.value = true })
watch(() => props.configured, value => {
  if (!value) { beginRead(); loading.value = false; stale.value = snapshot.value !== null }
})
function localTime(value) {
  return new Date(value * 1000).toLocaleString('zh-CN', {
    timeZone: props.timezone, timeZoneName: 'short', hour12: false,
  })
}
function bytes(value) { return `${Number(value).toLocaleString('zh-CN')} 字节` }
function label(kind) {
  return { workspace:'工作区（含会话与 out）', runtime:'运行目录（含家目录和控制材料）', deliveries:'已复制出的交付目录' }[kind]
}
async function read() {
  if (!props.configured || loading.value) return
  const fresh = beginRead(), name = props.scene, id = props.taskId
  loading.value = true
  try {
    const value = await api(`/api/host/tasks/${encodeURIComponent(id)}/storage?${new URLSearchParams({scene:name})}`)
    if (!fresh()) return
    snapshot.value = value; readError.value = ''
    stale.value = props.status !== value.status_at_end || props.container !== value.container_at_end
  } catch (error) { if (fresh()) { readError.value = error.message; stale.value = snapshot.value !== null } }
  finally { if (fresh()) loading.value = false }
}
</script>

<template>
  <section class="task-storage" aria-label="任务存储用量">
    <div class="section-heading"><h3>任务存储用量</h3>
      <v-btn variant="outlined" :loading="loading" :disabled="!configured || loading" @click="read">读取目录用量</v-btn></div>
    <p class="muted">硬磁盘配额未实现；查看用量不会限制、清理或删除文件。只在点击时扫描，任务运行中可能继续变化。</p>
    <p v-if="!configured" class="muted">当前未配置 worker，无法定位原任务的三棵存储目录。</p>
    <v-alert v-if="readError" type="error" variant="tonal" role="alert" :title="snapshot?'读取失败 · 保留上次快照':'存储读取失败'">{{ readError }}</v-alert>
    <p v-if="loading" class="muted" role="status">正在读取目录元数据，不打开文件内容…</p>
    <template v-if="snapshot">
      <v-alert v-if="stale" type="info" variant="tonal" role="status">下方保留的是上次存储快照，或与当前详情记录不同；需要最新用量请手动重读。</v-alert>
      <p class="muted">读取开始 {{ localTime(snapshot.started_at) }} · 结束 {{ localTime(snapshot.ended_at) }}；不是原子快照。</p>
      <p class="muted">读取前后实际任务状态：{{ snapshot.status_at_start }} → {{ snapshot.status_at_end }}；容器定位：{{ snapshot.container_at_start ?? '无' }} → {{ snapshot.container_at_end ?? '无' }}。</p>
      <ul class="storage-roots"><li v-for="root in snapshot.roots" :key="root.kind">
        <strong>{{ label(root.kind) }}</strong><p class="path">{{ root.path }}</p>
        <p v-if="!root.exists" class="muted">读取时目录不存在，不按零字节推断任务已经清理或交付完成。</p>
        <dl v-else class="storage-facts">
          <div><dt>普通文件逻辑大小</dt><dd>{{ bytes(root.usage.file_bytes) }}</dd></div>
          <div><dt>文件系统报告的分配字节</dt><dd>{{ bytes(root.usage.allocated_bytes) }}</dd></div>
          <div><dt>普通文件 / 目录（含根）</dt><dd>{{ root.usage.files }} / {{ root.usage.directories }}</dd></div>
          <div><dt>不跟随的链接 / 特殊文件</dt><dd>{{ root.usage.links }} / {{ root.usage.special_files }}</dd></div>
          <div><dt>声明多个硬链接的文件项</dt><dd>{{ root.usage.hardlinked_files }}</dd></div>
        </dl>
      </li></ul>
      <p class="muted">{{ snapshot.notice }}</p>
    </template>
    <p v-else-if="!loading && !readError" class="muted">尚未读取存储用量；文件登记、上传回执与磁盘用量各自独立。</p>
  </section>
</template>

<style scoped>
.task-storage{border-top:1px solid var(--line);margin-top:20px;padding-top:12px;min-width:0;overflow-wrap:anywhere}.section-heading{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}.section-heading h3{margin:0}.storage-roots{list-style:none;padding:0;display:grid;gap:12px}.storage-roots li{border:1px solid var(--line);border-radius:10px;padding:12px;min-width:0}.path{font-size:13px;overflow-wrap:anywhere}.storage-facts{display:grid;gap:8px;margin:12px 0}.storage-facts div{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}.storage-facts dt{color:var(--muted)}.storage-facts dd{margin:0}.task-storage :deep(.v-btn){min-height:44px}
</style>
