<script setup>
import { ref, watch } from 'vue'
import { schedulesApi } from '../../api/schedules.js'
import { useResource } from '../../../composables/useResource.js'
import { formatTime } from '../../time.js'
import Panel from '../../ui/Panel.vue'
import ResourceState from '../../ui/ResourceState.vue'
import ObjectList from '../../ui/ObjectList.vue'
import ObjectRow from '../../ui/ObjectRow.vue'
import LoadMore from '../../ui/LoadMore.vue'

const props = defineProps({ scene: { type: String, required: true } })
const rows = ref([])
const page = useResource(async more => ({ more: more === true, ...(await schedulesApi.proactive(props.scene, more === true ? page.data.value.next_offset : 0)) }))
watch(() => page.data.value, value => { if (value) rows.value = value.more ? [...rows.value, ...value.items] : value.items })
const outcomes = { silent: '叫醒后没有开口', answered: '有人接话', ignored: '没人接话', unobserved: '没法判断' }
const at = value => formatTime(value, page.data.value?.timezone)
const hours = seconds => {
  const minutes = Math.round(seconds / 60)
  return minutes % 60 ? `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分钟` : `${minutes / 60} 小时`
}
function outcome(item) {
  if (item.assessment === 'arrival_count') return item.outcome || '进行中'
  if (item.outcome) return outcomes[item.outcome] || item.outcome
  if (item.turn_ended === null) return '进行中'
  return '等待判断'
}
</script>

<template>
  <Panel title="主动开话题">
    <template #actions><v-btn size="small" variant="text" :loading="page.loading.value" @click="page.reload()">刷新</v-btn></template>
    <ResourceState :resource="page" error-title="读取主动开话题记录失败" v-slot="{ data }">
      <p v-if="!data.settings" class="muted">本群没有开启主动开话题，可以在
        <RouterLink :to="{ name: 'host-scenes', query: { scene, tab: 'settings' } }">群聊设置</RouterLink> 里打开。</p>
      <template v-else>
        <p>群里安静 {{ hours(data.settings.idle_seconds) }}后，Bot 会在
          {{ data.settings.start.slice(0, 5) }}–{{ data.settings.end.slice(0, 5) }} 之间找个话题聊聊，每天最多一次。</p>
        <p class="muted small">下次最早 {{ data.next_at === null ? '—' : at(data.next_at) }}{{ data.next_reason ? `（${data.next_reason}）` : '' }}</p>
      </template>
      <v-alert v-if="data.pause" type="info">最近两次开话题都没人接，暂停到 {{ at(data.pause.until) }}。</v-alert>
      <p v-if="!rows.length" class="muted">还没有主动开过话题。</p>
      <ObjectList divided>
        <ObjectRow v-for="item in rows" :key="item.id" :title="outcome(item)" :subtitle="at(item.woke_at)">
          <template #actions><v-btn size="small" variant="text" :to="{ name: 'host-logs', query: { scene, turn: item.turn_id } }">查看这一轮</v-btn></template>
        </ObjectRow>
      </ObjectList>
      <LoadMore v-if="data.next_offset !== null" :loading="page.loading.value" @more="page.reload(true)" />
    </ResourceState>
  </Panel>
</template>

<style scoped>
p{margin:0}
</style>
