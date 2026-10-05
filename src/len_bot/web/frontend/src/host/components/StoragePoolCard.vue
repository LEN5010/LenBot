<script setup>
import { watch } from 'vue'
import { useResource } from '../../composables/useResource.js'
import { taskStorageApi } from '../api/taskStorage.js'
import { fileSize } from '../spaceLabels.js'
import Panel from '../ui/Panel.vue'
import ResourceState from '../ui/ResourceState.vue'
import StatGrid from '../ui/StatGrid.vue'

const props = defineProps({ version: { type: Number, default: 0 } })
const pool = useResource(taskStorageApi.pool)
watch(() => props.version, () => pool.reload())
</script>

<template>
  <Panel title="任务存储池">
    <template #actions><v-btn size="small" variant="text" :loading="pool.loading.value" @click="pool.reload()">刷新</v-btn></template>
    <ResourceState :resource="pool" error-title="存储池读取失败" v-slot="{ data }">
      <template v-if="data.configured">
        <StatGrid :items="[{ label: '容量上限', value: fileSize(data.usage.limit_bytes) }, { label: '已用', value: fileSize(data.usage.used_bytes) },
          { label: '还能写入', value: fileSize(data.usage.available_bytes) }]" />
        <p class="muted small">{{ data.usage.kind === 'apfs' ? 'APFS 卷配额' : 'ext4 文件系统' }} · {{ data.usage.mount }}。工作区、运行目录和共享资料共用这个上限，交付文件不占用。</p>
      </template>
      <p v-else class="muted">没有设置存储池容量上限。</p>
    </ResourceState>
  </Panel>
</template>

<style scoped>
p{margin:0;overflow-wrap:anywhere}
</style>
