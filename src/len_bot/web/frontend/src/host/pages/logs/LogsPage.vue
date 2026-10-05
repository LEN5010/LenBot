<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, sceneName } from '../../../api.js'
import { useResource } from '../../../composables/useResource.js'
import { host } from '../../store.js'
import { useCurrentScene } from '../../../composables/useCurrentScene.js'
import { useHostEvents } from '../../events.js'
import { formatTime } from '../../time.js'
import HostPage from '../../ui/HostPage.vue'
import PageTabs from '../../ui/PageTabs.vue'
import Panel from '../../ui/Panel.vue'
import ResourceState from '../../ui/ResourceState.vue'
import MasterDetail from '../../ui/MasterDetail.vue'
import ObjectList from '../../ui/ObjectList.vue'
import ObjectRow from '../../ui/ObjectRow.vue'
import StatusBadge from '../../ui/StatusBadge.vue'
import ErrorNote from '../../ui/ErrorNote.vue'
import LiveStatus from '../../ui/LiveStatus.vue'
import TurnDetail from '../../components/TurnDetail.vue'
import DevOnly from '../../ui/DevOnly.vue'

const route = useRoute(), router = useRouter()
const tab = computed(() => route.query.tab === 'system' ? 'system' : 'turns')
const { scene } = useCurrentScene()
const selected = computed(() => typeof route.query.turn === 'string' ? route.query.turn : '')
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
const open = turn => router.replace({ query: { ...route.query, turn: turn ? turn.id : undefined } })

const filter = ref('all')
const system = useResource(() => Promise.all([api('/api/host/logs'), api('/api/host/log-files')]), { immediate: false })
watch(tab, value => { if (value === 'system' && !system.data.value) system.reload() }, { immediate: true })
const logLabels = { runtime: '运行状态', receipt: '收到消息', turn: '回复结束', platform_error: '平台账号 连接出错', platform_event: '平台事件' }
const logItems = computed(() => (system.data.value?.[0].items || [])
  .filter(item => filter.value === 'all' || item.record.error))
</script>

<template>
  <HostPage title="日志" :wide="tab === 'turns'">
    <PageTabs :tabs="[['turns', '回复记录'], ['system', '系统日志']]" :model-value="tab" label="日志" />

    <MasterDetail v-if="tab === 'turns'" :selected="Boolean(selected)" list-width="300px" @back="open(null)">
      <template #list>
        <Panel title="最近 20 次回复" flush>
          <template #actions><LiveStatus :status="events.status.value" @reconnect="events.reconnect" /></template>
          <div class="list">
            <ResourceState :resource="sceneState" error-title="读取回复记录失败" :empty="!turns.length" empty-text="还没有回复记录" compact>
              <ObjectList>
                <ObjectRow v-for="turn in turns" :key="turn.id" :title="formatTime(turn.started, timezone)" clickable
                  :active="selected === turn.id" @click="open(turn)">
                  <template #meta><StatusBadge dot kind="turn" :value="turn.status" /></template>
                </ObjectRow>
              </ObjectList>
            </ResourceState>
          </div>
        </Panel>
      </template>
      <template #placeholder>从左边选一次回复，看看 Bot 当时怎么想的。</template>
      <Panel title="经过">
        <template #actions>
          <DevOnly><v-btn size="small" variant="text" :href="`${base()}/turns/${encodeURIComponent(selected)}/export`">下载诊断包</v-btn></DevOnly>
        </template>
        <ResourceState :resource="detail" error-title="读取这次回复失败" v-slot="{ data }">
          <TurnDetail :detail="data" :timezone="timezone" />
        </ResourceState>
      </Panel>
    </MasterDetail>

    <template v-else>
      <Panel title="本次启动以来">
        <template #actions>
          <v-btn-toggle v-model="filter" mandatory><v-btn value="all">全部</v-btn><v-btn value="errors">只看错误</v-btn></v-btn-toggle>
          <v-btn variant="text" size="small" :loading="system.loading.value" @click="system.reload()">刷新</v-btn>
        </template>
        <ResourceState :resource="system" error-title="读取系统日志失败" :empty="!logItems.length" empty-text="没有记录" compact>
          <ObjectList divided>
            <li v-for="(item, index) in logItems" :key="index" class="log-row">
              <div class="inline"><strong>{{ logLabels[item.record.type] || item.record.type }}</strong>
                <span class="muted small">{{ formatTime(item.time, host.state?.timezone) }}<template v-if="item.record.scene"> · {{ sceneName(item.record.scene) }}</template></span></div>
              <ErrorNote v-if="item.record.error || item.record.reason" title="错误" :error="item.record.error || item.record.reason" />
              <DevOnly label="原始记录" :json="item.record" />
            </li>
          </ObjectList>
        </ResourceState>
      </Panel>
      <Panel v-if="system.data.value" title="日志文件" :description="system.data.value[1].enabled ? '' : '没有开启日志文件，可以在设置的高级标签里打开。'">
        <ObjectList v-if="system.data.value[1].items.length" divided>
          <ObjectRow v-for="file in system.data.value[1].items" :key="file.name" :title="file.name" :subtitle="`${Math.ceil(file.bytes / 1024)} KB`">
            <template #actions><v-btn size="small" variant="text" :href="`/api/host/log-files/${encodeURIComponent(file.name)}`">下载</v-btn></template>
          </ObjectRow>
        </ObjectList>
      </Panel>
    </template>
  </HostPage>
</template>

<style scoped>
.list{padding:0 var(--sp-2) var(--sp-2)}
.log-row{padding:var(--sp-3) 0;display:grid;gap:var(--sp-2)}
</style>
