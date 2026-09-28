<script setup>
import { onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { api, queryString } from '../api.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'

const props = defineProps({ scene: { type:String, required:true } })
const OUTCOMES = {
  silent:'被叫醒后没有开口', answered:'回复效果判断：有人回应', ignored:'观察窗口内未发现回应',
  unobserved:'样本缺失、观察有缺口或无法确定回应关系',
}
const data = ref(null), loading = ref(false), error = ref('')
const begin = useRequestGuard(() => props.scene)

function time(value) {
  if (value === null || value === undefined) return '—'
  return new Date(value * 1000).toLocaleString('zh-CN', { timeZone:data.value.timezone, timeZoneName:'short', hour12:false })
}
function clock(value) { return value.slice(0, 5) }
function hours(seconds) {
  const minutes = Math.round(seconds / 60)
  return minutes % 60 ? `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分钟` : `${minutes / 60} 小时`
}
function outcome(item) {
  if (item.assessment === 'arrival_count') return '历史消息活跃统计（非回应判断，不参与暂停）：' + (item.outcome || '未结束')
  if (item.outcome) return OUTCOMES[item.outcome] || item.outcome
  if (item.turn_ended === null) return '本轮仍在进行'
  if (item.spoke_at === null) return '轮次已结束，等待宿主记录结果'
  if (item.observe_until === null || item.observe_until <= data.value.now) return '等待回复效果判断；失败批次可在学习页重做'
  return `观察中，到 ${time(item.observe_until)}`
}
async function read(more = false) {
  if (loading.value) return
  const fresh = begin()
  loading.value = true; error.value = ''
  try {
    const offset = more ? data.value.next_offset : 0
    const value = await api('/api/host/schedules/proactive' + queryString({ scene:props.scene, offset }))
    if (!fresh()) return
    data.value = more ? { ...value, items:[...data.value.items, ...value.items] } : value
  } catch (caught) {
    if (fresh()) error.value = caught.message
  } finally {
    if (fresh()) loading.value = false
  }
}
watch(() => props.scene, () => { data.value = null; loading.value = false; read() })
onMounted(read)
</script>

<template>
  <section class="surface" aria-labelledby="proactive-title">
    <div class="section-heading"><h2 id="proactive-title">主动开话题</h2>
      <v-btn variant="outlined" :loading="loading" :disabled="loading" @click="read()">刷新</v-btn></div>
    <v-alert v-if="error" type="error" variant="tonal" role="alert" :title="data?'读取失败 · 保留上次结果':'读取失败'">{{ error }}</v-alert>
    <p v-if="loading && !data" role="status">正在读取主动开话题记录…</p>
    <template v-if="data">
      <p v-if="!data.settings" class="muted">本群未启用主动开话题（运行配置）。可在设置页的场景设置里开启，保存后重启宿主生效。下面仍列出已有记录。</p>
      <template v-else>
        <p class="muted">群里安静 {{ hours(data.settings.idle_seconds) }}后，在 {{ clock(data.settings.start) }}–{{ clock(data.settings.end) }}（{{ data.timezone }}）内叫醒一次；每天最多一次，安静时段不叫醒。</p>
        <dl class="facts"><div><dt>最后一条消息</dt><dd>{{ data.idle_since===null?'本群还没有消息':time(data.idle_since) }}</dd></div>
          <div><dt>最早可叫醒</dt><dd>{{ data.next_at===null?'—':time(data.next_at) }}<span v-if="data.next_reason" class="muted"> · {{ data.next_reason }}</span></dd></div></dl>
        <p class="muted">最早可叫醒时间按读取时的数据计算；有新消息时会后移，宿主正在处理其他轮次时会顺延。</p>
      </template>
      <v-alert v-if="data.pause" type="warning" variant="tonal" role="status" title="已暂停一周">
        最近两次可判断的开话题（记录 #{{ data.pause.wakes[0] }}、#{{ data.pause.wakes[1] }}）在回复效果窗口内都未发现回应，暂停到 {{ time(data.pause.until) }}，之后自动恢复。
      </v-alert>
      <p v-if="!data.items.length" class="muted">还没有主动叫醒记录。</p>
      <ul v-else class="wake-list"><li v-for="item in data.items" :key="item.id" class="wake-card">
        <div class="card-heading"><strong>#{{ item.id }} · {{ item.local_date }}</strong><span>{{ outcome(item) }}</span></div>
        <dl class="facts"><div><dt>叫醒时间</dt><dd>{{ time(item.woke_at) }}</dd></div>
          <div><dt>此前最后一条消息</dt><dd>{{ time(item.idle_since) }}</dd></div>
          <div><dt>首次确认发出</dt><dd>{{ item.spoke_at===null?'没有已确认发出的消息':time(item.spoke_at) }}</dd></div>
          <div v-if="item.closed_at!==null"><dt>结果记录于</dt><dd>{{ time(item.closed_at) }}</dd></div></dl>
        <RouterLink :to="{name:'host', query:{scene, turn:item.turn_id}}">查看所属轮次</RouterLink>
      </li></ul>
      <v-btn v-if="data.next_offset!==null" variant="outlined" :loading="loading" :disabled="loading" @click="read(true)">读取更多记录</v-btn>
    </template>
  </section>
</template>

<style scoped>
.section-heading,.card-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap}
.surface h2{font-size:18px;margin:0 0 12px}
.facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,230px),1fr));gap:10px;margin:12px 0}
.facts dt{font-size:13px;color:var(--muted);font-weight:650}.facts dd{margin:3px 0 0}
.wake-list{list-style:none;padding:0;display:grid;gap:12px;margin:14px 0}
.wake-card{border:1px solid var(--line);border-radius:10px;padding:14px;overflow-wrap:anywhere}
.card-heading{font-weight:650}
</style>
