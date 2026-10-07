<script setup>
import { computed, ref } from 'vue'
import { api } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import Panel from '../../ui/Panel.vue'
import ResourceState from '../../ui/ResourceState.vue'

const current = useResource(() => api('/api/host/updates'))
const releases = useResource(() => api('/api/host/updates/releases'), { immediate: false })
const action = useAction()
const prerelease = ref(false)
const visible = computed(() => (releases.data.value || []).filter(item => prerelease.value || !item.prerelease))
async function openUpdater() {
  const value = await action.run(() => api('/api/host/updates/session', { method: 'POST' }))
  if (value) window.location.assign(value.url)
}
</script>

<template>
  <ResourceState :resource="current" error-title="读取版本失败" v-slot="{ data }">
    <Panel title="版本与更新">
      <p>当前版本 <strong>{{ data.version }}</strong></p>
      <p v-if="data.managed">更新器会在主面板停机后继续运行。准备完成并确认后，才会停止聊天和任务、备份数据并升级。</p>
      <p v-else>当前从源码运行。请在终端更新 Git、准备依赖并停机迁移；面板不会覆盖开发目录。</p>
      <v-alert v-if="data.status?.error" type="error" variant="tonal">{{ data.status.error }}</v-alert>
      <v-alert v-if="action.error.value" type="error" variant="tonal">{{ action.error.value.message }}</v-alert>
      <v-btn v-if="data.managed" color="primary" :loading="action.busy.value" @click="openUpdater">打开更新与恢复页</v-btn>
      <v-btn variant="text" :loading="releases.loading.value" @click="releases.reload">检查发行版本</v-btn>
      <v-switch v-model="prerelease" label="显示预发布版本" hide-details />
      <v-alert v-if="releases.error.value" type="error" variant="tonal">{{ releases.error.value.message }}</v-alert>
      <p v-if="releases.data.value && !visible.length">目前没有可显示的公开发行版本。</p>
      <div v-for="item in visible" :key="item.tag" class="mt-6">
        <h3>{{ item.tag }} <span v-if="item.prerelease">· 预发布</span></h3>
        <pre style="white-space: pre-wrap; font: inherit">{{ item.notes }}</pre>
      </div>
    </Panel>
  </ResourceState>
</template>
