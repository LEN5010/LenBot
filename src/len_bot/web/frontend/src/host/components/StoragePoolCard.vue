<script setup>
import { watch } from 'vue'
import { useResource } from '../../composables/useResource.js'
import { taskStorageApi } from '../api/taskStorage.js'
import { fileSize } from '../spaceLabels.js'
import ErrorNote from './ErrorNote.vue'

const props = defineProps({ version: { type: Number, default: 0 } })
const pool = useResource(taskStorageApi.pool)
watch(() => props.version, () => pool.reload())
</script>

<template>
  <section class="surface storage-pool">
    <div class="heading"><h2>实例任务存储池</h2><v-btn size="small" variant="text" :loading="pool.loading.value" @click="pool.reload()">刷新容量</v-btn></div>
    <ErrorNote v-if="pool.error.value" title="存储池读取失败" :error="pool.error.value" />
    <template v-else-if="pool.data.value?.configured">
      <div class="figures"><div><small>池硬上限</small><strong>{{ fileSize(pool.data.value.usage.limit_bytes) }}</strong></div>
        <div><small>文件系统已用</small><strong>{{ fileSize(pool.data.value.usage.used_bytes) }}</strong></div>
        <div><small>当前可写空间</small><strong>{{ fileSize(pool.data.value.usage.available_bytes) }}</strong></div></div>
      <p>{{ pool.data.value.usage.kind === 'apfs' ? 'APFS 卷配额' : 'ext4 文件系统容量' }} · <code>{{ pool.data.value.usage.mount }}</code></p>
      <p class="muted">工作区、运行目录和共享资料共用这一个上限；独立交付位于池外。每项任务没有独立硬配额。</p>
    </template>
    <p v-else-if="pool.data.value" class="muted">未配置存储池硬上限。任务目录的用量统计不限制写入；停机后初始化存储池并移交原文件。</p>
  </section>
</template>

<style scoped>
.storage-pool{display:grid;gap:12px;margin-bottom:16px}.heading,.figures{display:flex;justify-content:space-between;align-items:center;gap:20px;flex-wrap:wrap}.figures{justify-content:flex-start}.figures div{display:grid;gap:3px;min-width:140px}.figures small{color:var(--muted)}strong{font-size:22px}p{margin:0;overflow-wrap:anywhere}h2{font-size:18px}
</style>
