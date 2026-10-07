<script setup>
import { computed, ref } from 'vue'
import { api } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { readUpdates } from '../../store.js'
import { formatTime } from '../../time.js'
import Panel from '../../ui/Panel.vue'
import ResourceState from '../../ui/ResourceState.vue'

const current = useResource(() => api('/api/host/updates'))
const releases = useResource(() => api('/api/host/updates/releases'), { immediate: false })
const action = useAction()
const prerelease = ref(false)
const visible = computed(() => (releases.data.value || []).filter(item => prerelease.value || !item.prerelease))
const check = computed(() => current.data.value?.check || null)
const summary = computed(() => {
  const value = check.value
  if (!value) return ''
  if (value.update_available) return `有新版本 ${value.latest}`
  if (value.error) return '上次检查没有成功'
  if (value.checked_at) return value.latest_prerelease ? `已是最新正式版，另有预发布 ${value.latest_prerelease}` : '已是最新版本'
  return value.enabled ? '每天自动检查一次，还没查过' : '自动检查已关闭'
})
async function checkNow() {
  await releases.reload()
  current.reload()
  readUpdates()
}
async function openUpdater() {
  const value = await action.run(() => api('/api/host/updates/session', { method: 'POST' }))
  if (value) window.location.assign(value.url)
}
</script>

<template>
  <ResourceState :resource="current" error-title="读取版本失败" v-slot="{ data }">
    <Panel title="版本与更新">
      <div class="version" :class="{ fresh: check?.update_available }">
        <div>
          <span class="muted">当前版本</span>
          <strong>{{ data.version }}</strong>
        </div>
        <div v-if="summary" class="version-state">
          <span class="dot" />{{ summary }}
          <span v-if="check?.checked_at" class="muted">· {{ formatTime(check.checked_at) }}</span>
        </div>
      </div>
      <v-alert v-if="check?.error" type="warning" variant="tonal">{{ check.error }}</v-alert>
      <p v-if="data.managed">更新器会在主面板停机后继续运行。准备完成并确认后，才会停止聊天和任务、备份数据并升级。</p>
      <p v-else>当前从源码运行，用 Git 更新、准备依赖后在终端停机迁移。</p>
      <v-alert v-if="data.status?.error" type="error" variant="tonal">{{ data.status.error }}</v-alert>
      <v-alert v-if="action.error.value" type="error" variant="tonal">{{ action.error.value.message }}</v-alert>
      <div class="actions">
        <v-btn v-if="data.managed" color="primary" :loading="action.busy.value" @click="openUpdater">打开更新与恢复页</v-btn>
        <v-btn variant="outlined" :loading="releases.loading.value" @click="checkNow">检查发行版本</v-btn>
        <v-switch v-model="prerelease" label="显示预发布版本" hide-details color="primary" />
      </div>
      <v-alert v-if="releases.error.value" type="error" variant="tonal">{{ releases.error.value.message }}</v-alert>
      <p v-if="releases.data.value && !visible.length" class="muted">没有可显示的发行版本。</p>
      <ol v-if="visible.length" class="releases">
        <li v-for="(item, index) in visible" :key="item.tag" :style="{ animationDelay: `${Math.min(index, 6) * 40}ms` }">
          <h3>{{ item.tag }}
            <span v-if="item.newer" class="tag new">新</span>
            <span v-if="item.version === data.version" class="tag">当前</span>
            <span v-if="item.prerelease" class="tag">预发布</span>
          </h3>
          <pre>{{ item.notes }}</pre>
        </li>
      </ol>
    </Panel>
  </ResourceState>
</template>

<style scoped>
.version{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:var(--sp-3);padding:var(--sp-4) var(--sp-5);margin-bottom:var(--sp-4);border-radius:var(--radius-lg);background:var(--fill);animation:rise var(--dur-3) var(--ease-out) both}
.version>div:first-child{display:grid;gap:2px}
.version strong{font-size:var(--fs-xl)}
.version-state{display:flex;align-items:center;gap:var(--sp-2);font-size:var(--fs-sm)}
.dot{width:8px;height:8px;border-radius:50%;background:var(--muted)}
.version.fresh{background:var(--brand-soft)}
.version.fresh .version-state{color:var(--primary);font-weight:600}
.version.fresh .dot{background:var(--brand)}
.actions{display:flex;flex-wrap:wrap;align-items:center;gap:var(--sp-3);margin:var(--sp-4) 0}
.actions .v-switch{flex:0 0 auto}
.releases{list-style:none;margin:0;padding:0}
.releases li{padding:var(--sp-4) 0;border-top:1px solid var(--line);animation:rise var(--dur-3) var(--ease-out) both}
.releases h3{display:flex;align-items:center;gap:var(--sp-2);font-size:var(--fs-md)}
.tag{padding:1px 8px;border-radius:999px;background:var(--fill);font-size:var(--fs-xs);font-weight:500;color:var(--muted)}
.tag.new{background:var(--brand-soft);color:var(--primary)}
.releases pre{margin:var(--sp-2) 0 0;white-space:pre-wrap;font:inherit;font-size:var(--fs-sm);color:var(--muted)}
</style>
