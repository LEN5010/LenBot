<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'

const props = defineProps({ scene: { type: String, required: true } })
const view = ref(null), rows = ref([]), error = ref(''), notice = ref(''), loading = ref(false), busy = ref(false)
const calls = ref(null), callsFor = ref('')
const beginRead = useRequestGuard(), beginAction = useRequestGuard(), beginCalls = useRequestGuard()
const labels = { idle:'已有音频', queued:'排队中', running:'处理中', complete:'已转写', failed:'失败', interrupted:'已中断' }
const root = () => `/api/host/scenes/${encodeURIComponent(props.scene)}/audio`
const path = row => `${root()}/${encodeURIComponent(row.platform_id)}/${row.audio_index}`
function clock(value) {
  return value === null ? '未知' : new Date(value * 1000).toLocaleString('zh-CN', {timeZone:view.value.timezone, hour12:false})
}
async function read(more = false) {
  const fresh = beginRead()
  loading.value = true
  try {
    const data = await api(`${root()}?offset=${more ? view.value.next_offset : 0}`)
    if (!fresh()) return
    rows.value = more ? [...rows.value, ...data.items] : data.items
    view.value = data
    error.value = ''
  } catch (e) { if (fresh()) error.value = e.message }
  finally { if (fresh()) loading.value = false }
}
async function details(row) {
  const fresh = beginCalls(), endpoint = path(row)
  callsFor.value = `${row.platform_id} / 语音${row.audio_index}`
  calls.value = null
  try {
    const data = await api(`${endpoint}/calls`)
    if (fresh()) calls.value = data
  } catch (e) { if (fresh()) error.value = e.message }
}
async function transcribe(row) {
  const refresh = row.wav_bytes !== null
  if (!window.confirm(refresh ? '使用已保存的 WAV 重新识别一次？将调用 ASR；此前进入会话的语音会把新结果交给大脑。' : '重新获取这段语音并识别一次？将调用平台与 ASR；此前进入会话的语音会把结果交给大脑。')) return
  const fresh = beginAction(), endpoint = path(row)
  busy.value = true
  notice.value = ''
  error.value = ''
  try {
    await api(`${endpoint}/transcribe`, { method:'POST', body:JSON.stringify({ refresh }) })
    if (!fresh()) return
    notice.value = '本次识别已完成；音频没有上传，后续是否回应由大脑决定。'
    await read()
  } catch (e) {
    if (fresh()) { await read(); error.value = `本次未确认成功：${e.message}。不会自动重试。` }
  } finally { if (fresh()) busy.value = false }
}
onMounted(() => read())
</script>

<template>
  <section class="surface audio-panel">
    <div class="section-heading"><h2>语音处理</h2><v-btn variant="outlined" :loading="loading" :disabled="busy || loading" @click="read()">重读语音状态</v-btn></div>
    <p class="muted">记录本场景实际取件和识别结果。播放来自宿主已保存的 WAV，不代表音频已上传。离开页面不会自动取消已提交的识别。</p>
    <v-alert v-if="error" type="error" variant="tonal" role="alert">{{ error }}</v-alert>
    <v-alert v-if="notice" type="success" variant="tonal" role="status">{{ notice }}</v-alert>
    <p v-if="loading && !view" role="status">正在读取语音状态…</p>
    <template v-if="view">
      <p class="muted">运行中的自动转写：{{ view.automatic ? '开启' : '关闭' }}；ASR 与平台：{{ view.available ? '已配置' : '未配置完整' }}。在场景设置页保存开关后重启生效。</p>
      <p class="muted">WAV 上限 {{ (view.limits.max_bytes / 1048576).toFixed(1) }} MiB / {{ view.limits.max_seconds }} 秒；输入最多等待 {{ view.limits.wait_seconds }} 秒，慢识别不堵后续聊天。OneBot WS 帧上限 {{ (view.onebot_ws_frame_bytes / 1048576).toFixed(1) }} MiB，base64 取件仍受此限制，不自动放大。</p>
      <v-alert v-if="view.worker_error" type="error" variant="tonal">后台已停止：{{ view.worker_error }}</v-alert>
      <p v-if="!rows.length" class="muted">暂无语音处理记录；未扫描历史语音。</p>
      <article v-for="row in rows" :key="`${row.platform_id}:${row.audio_index}`" class="audio-item">
        <h3>消息 {{ row.platform_id }} · 语音 {{ row.audio_index }} · {{ labels[row.status] }}</h3>
        <p class="muted">更新 {{ clock(row.updated) }}<span v-if="row.duration !== null"> · {{ row.duration.toFixed(2) }} 秒</span></p>
        <audio v-if="row.wav_bytes !== null" :src="`${path(row)}/wav`" controls preload="none" :aria-label="`消息${row.platform_id}第${row.audio_index}段语音`" />
        <p v-if="row.transcript !== null" class="transcript">ASR：{{ row.transcript || '未识别出文字' }}</p>
        <p v-if="row.transcript !== null" class="muted">{{ row.provider }} / {{ row.model }} · {{ clock(row.transcribed_at) }}，未经人工校对；后续重做失败时保留此前结果。</p>
        <pre v-if="row.error" class="error-text">{{ row.error }}</pre>
        <div class="actions">
          <v-btn variant="outlined" :disabled="busy || loading || !view.available || view.stopping || ['queued','running'].includes(row.status)" @click="transcribe(row)">{{ row.wav_bytes !== null ? '重新识别已存音频' : '重新获取并识别' }}</v-btn>
          <v-btn variant="text" :disabled="busy" @click="details(row)">查看实际模型调用</v-btn>
        </div>
      </article>
      <v-btn v-if="view.next_offset !== null" variant="outlined" :disabled="loading || busy" @click="read(true)">读取更早的语音</v-btn>
      <details v-if="callsFor" open><summary>{{ callsFor }} 的最近调用</summary>
        <p class="muted">off_turn 为后台/面板调用，in_turn 为真实对话轮次内调用；每类最多20条；费用按调用时的 ASR 价格与服务计量估算，缺少必要用量则未知。</p>
        <template v-if="calls !== null"><section v-for="(items,kind) in calls" :key="kind"><h3>{{ kind==='in_turn'?'轮次内调用':'轮次外调用' }}</h3>
          <article v-for="item in items" :key="item.id"><p>{{ clock(item.started) }} · {{ item.ended===null?'未结束':item.error?'失败':'已结束' }} · {{ item.cost ? `${item.cost.amount} ${item.cost.currency}（估算）` : '费用未知' }}</p>
            <p v-if="item.request.snapshot_expired_at" class="muted">输入与响应快照已过保留期，计量和结果状态保留。</p>
            <details><summary>本次调用原文</summary><pre>{{ JSON.stringify(item,null,2) }}</pre></details></article>
        </section></template><p v-else>正在读取…</p>
      </details>
    </template>
  </section>
</template>

<style scoped>
.section-heading,.actions{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
.audio-item{border-top:1px solid var(--line);padding:16px 0;min-width:0}
.audio-item h3{font-size:16px;overflow-wrap:anywhere}.transcript,pre{white-space:pre-wrap;overflow-wrap:anywhere}
.error-text{color:var(--error)}audio{width:min(100%,480px)}pre{max-height:400px;overflow:auto}
</style>
