<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, sceneName } from '../../../api.js'
import { useResource } from '../../../composables/useResource.js'
import { host } from '../../store.js'
import { useHostEvents } from '../../events.js'
import { turnFailed, turnLabel } from '../../labels.js'
import { formatTime } from '../../time.js'
import HostPage from '../../components/HostPage.vue'
import ErrorNote from '../../components/ErrorNote.vue'
import LiveStatus from '../../components/LiveStatus.vue'
import TurnDetail from '../../components/TurnDetail.vue'
import DevOnly from '../../components/DevOnly.vue'

const route = useRoute(), router = useRouter()
const tab = computed(() => route.query.tab === 'system' ? 'system' : 'turns')
const scene = computed(() => typeof route.query.scene === 'string' ? route.query.scene : host.state?.scenes[0]?.scene || '')
const selected = computed(() => typeof route.query.turn === 'string' ? route.query.turn : '')
const options = computed(() => (host.state?.scenes || []).map(item => ({ title: sceneName(item.scene), value: item.scene })))
const base = () => `/api/host/scenes/${encodeURIComponent(scene.value)}`

const sceneState = useResource(() => api(base()), { immediate: false })
const detail = useResource(() => api(`${base()}/turns/${encodeURIComponent(selected.value)}`), { immediate: false })
const events = useHostEvents(async () => {
  if (tab.value !== 'turns' || !scene.value) return
  await sceneState.reload()
  if (selected.value) await detail.reload()
})
watch(scene, value => { if (value) sceneState.reload() }, { immediate: true })
watch([scene, selected], ([value, turn]) => { detail.data.value = null; if (value && turn) detail.reload() }, { immediate: true })
const turns = computed(() => [...(sceneState.data.value?.turns || [])].sort((a, b) => b.started - a.started))
const timezone = computed(() => sceneState.data.value?.timezone || host.state?.timezone)
function open(turn) {
  router.replace({ query: { ...route.query, turn: selected.value === turn.id ? undefined : turn.id } })
}

const filter = ref('all')
const system = useResource(() => Promise.all([api('/api/host/logs'), api('/api/host/log-files')]), { immediate: false })
watch(tab, value => { if (value === 'system' && !system.data.value) system.reload() }, { immediate: true })
const logLabels = { runtime: '运行状态', receipt: '收到消息', turn: '回复结束', platform_error: 'QQ 连接出错', platform_event: '平台事件' }
const logItems = computed(() => (system.data.value?.[0].items || [])
  .filter(item => filter.value === 'all' || item.record.error))
</script>

<template>
  <HostPage title="日志">
    <template #actions>
      <v-btn-toggle :model-value="tab" mandatory density="comfortable" color="primary"
        @update:model-value="value => router.replace({ query: { ...route.query, tab: value === 'system' ? 'system' : undefined } })">
        <v-btn value="turns">回复记录</v-btn><v-btn value="system">系统日志</v-btn></v-btn-toggle>
    </template>

    <template v-if="tab === 'turns'">
      <div class="toolbar">
        <v-select :model-value="scene" :items="options" label="群聊" class="scene-select"
          @update:model-value="value => router.replace({ query: { scene: value } })" />
        <LiveStatus :status="events.status.value" @reconnect="events.reconnect" />
      </div>
      <ErrorNote v-if="sceneState.error.value" title="读取回复记录失败" :error="sceneState.error.value" />
      <div class="turns-layout">
        <section class="surface">
          <h2>最近 20 次回复</h2>
          <p v-if="sceneState.data.value && !turns.length" class="empty-state">还没有回复记录</p>
          <ul class="turn-list">
            <li v-for="turn in turns" :key="turn.id">
              <button type="button" :class="{ active: selected === turn.id, failed: turnFailed(turn.status) }" @click="open(turn)">
                <span>{{ formatTime(turn.started, timezone) }}</span><strong>{{ turnLabel(turn.status) }}</strong></button>
            </li>
          </ul>
        </section>
        <section class="surface">
          <h2>经过</h2>
          <p v-if="!selected" class="muted">在左边选一次回复。</p>
          <ErrorNote v-if="detail.error.value" title="读取这次回复失败" :error="detail.error.value" />
          <TurnDetail v-if="detail.data.value" :detail="detail.data.value" :timezone="timezone" />
          <DevOnly v-if="detail.data.value">
            <a :href="`${base()}/turns/${encodeURIComponent(selected)}/export`">下载诊断包</a>
          </DevOnly>
        </section>
      </div>
    </template>

    <template v-else>
      <ErrorNote v-if="system.error.value" title="读取系统日志失败" :error="system.error.value" />
      <section v-if="system.data.value" class="surface">
        <div class="toolbar">
          <h2>本次启动以来</h2>
          <v-btn-toggle v-model="filter" mandatory density="compact"><v-btn value="all">全部</v-btn><v-btn value="errors">只看错误</v-btn></v-btn-toggle>
          <v-btn variant="text" :loading="system.loading.value" @click="system.reload()">刷新</v-btn>
        </div>
        <p v-if="!logItems.length" class="empty-state">没有记录</p>
        <ol class="log-list">
          <li v-for="(item, index) in logItems" :key="index">
            <div><strong>{{ logLabels[item.record.type] || item.record.type }}</strong>
              <span class="muted">{{ formatTime(item.time, host.state?.timezone) }}<template v-if="item.record.scene"> · {{ sceneName(item.record.scene) }}</template></span></div>
            <ErrorNote v-if="item.record.error || item.record.reason" title="错误" :error="item.record.error || item.record.reason" />
            <DevOnly label="原始记录"><pre>{{ JSON.stringify(item.record, null, 2) }}</pre></DevOnly>
          </li>
        </ol>
      </section>
      <section v-if="system.data.value" class="surface">
        <h2>日志文件</h2>
        <p v-if="!system.data.value[1].enabled" class="muted">没有开启日志文件，可以在设置的高级标签里打开。</p>
        <ul class="file-list"><li v-for="file in system.data.value[1].items" :key="file.name">
          <a :href="`/api/host/log-files/${encodeURIComponent(file.name)}`">{{ file.name }}</a>
          <span class="muted">{{ Math.ceil(file.bytes / 1024) }} KB</span></li></ul>
      </section>
    </template>
  </HostPage>
</template>

<style scoped>
.toolbar{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.toolbar h2{margin:0 auto 0 0 !important}
.scene-select{max-width:280px}
.turns-layout{display:grid;grid-template-columns:minmax(220px,300px) minmax(0,1fr);gap:16px;align-items:start}
.turn-list{list-style:none;margin:8px 0 0;padding:0;display:grid;gap:4px}
.turn-list button{width:100%;display:flex;justify-content:space-between;gap:8px;padding:8px 10px;border-radius:8px;border:1px solid transparent;background:none;cursor:pointer;color:inherit;font:inherit}
.turn-list button:hover{background:var(--list-heading-bg)}
.turn-list button.active{border-color:var(--primary);background:var(--selected-bg)}
.turn-list button.failed strong{color:var(--error-text)}
.log-list,.file-list{list-style:none;margin:8px 0 0;padding:0}
.log-list li{border-top:1px solid var(--line);padding:10px 0;display:grid;gap:6px}
.log-list li div{display:flex;gap:10px;align-items:baseline}
.file-list li{display:flex;gap:12px;padding:6px 0}
@media(max-width:900px){.turns-layout{grid-template-columns:1fr}}
</style>
