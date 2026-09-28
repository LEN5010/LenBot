<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api, fmtTime, sceneName } from '../api.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'

const props = defineProps({ scene: { type: String, required: true } })
const emit = defineEmits(['dirty', 'busy'])
const data = ref(null), selected = ref(''), name = ref(''), text = ref(''), baseline = ref('')
const busy = ref(false), loading = ref(false), error = ref(''), notice = ref('')
const guard = useRequestGuard()
const dirty = computed(() => `${name.value}\n${text.value}` !== baseline.value)
const entries = computed(() => [...new Set([...Object.keys(data.value?.saved || {}), ...(data.value?.running || []).map(item => item.name)])].sort())
const status = { disabled: '运行配置未启用', connecting: '连接中', running: '已连接', failed: '连接失败', stopped: '已断开' }
const running = computed(() => data.value?.running.find(item => item.name === selected.value))
watch(dirty, value => emit('dirty', value))
watch(busy, value => emit('busy', value))
onBeforeUnmount(() => { emit('dirty', false); emit('busy', false) })

function select(value, ask = true) {
  if (ask && dirty.value && !window.confirm('放弃未保存的 MCP 服务草稿？')) return
  selected.value = value
  name.value = value
  const saved = data.value?.saved[value] || {
    enabled: false, scenes: [props.scene], timeout_seconds: 30, max_response_bytes: 1048576,
    transport: { type: 'stdio', command: '', args: [], cwd: '.', env: {} }
  }
  text.value = JSON.stringify(saved, null, 2)
  baseline.value = `${name.value}\n${text.value}`
  notice.value = ''
}
async function read() {
  if (busy.value || (dirty.value && !window.confirm('放弃 MCP 草稿并重读？'))) return
  const fresh = guard(); loading.value = true
  try {
    const value = await api('/api/host/mcp')
    if (!fresh()) return
    data.value = value; error.value = ''; select(selected.value, false)
  } catch (caught) { if (fresh()) error.value = caught.message }
  finally { if (fresh()) loading.value = false }
}
function transport(kind) {
  try {
    const config = JSON.parse(text.value)
    config.transport = kind === 'stdio'
      ? { type: 'stdio', command: '', args: [], cwd: '.', env: {} }
      : { type: 'http', url: 'http://127.0.0.1:8000/mcp', headers: {} }
    text.value = JSON.stringify(config, null, 2)
    error.value = ''
  } catch (caught) { error.value = `草稿不是合法 JSON：${caught.message}` }
}
async function request(path, options, message, refreshDraft = false) {
  if (busy.value) return
  const fresh = guard(); loading.value = false; busy.value = true; error.value = ''; notice.value = ''
  try {
    const value = await api(path, options)
    if (!fresh()) return
    data.value = value
    if (refreshDraft) select(options.method === 'DELETE' ? '' : name.value, false)
    if (value.result?.status === 'failed') error.value = value.result.error
    else notice.value = message
  } catch (caught) { if (fresh()) error.value = `${caught.message} 草稿保留；未自动重试。` }
  finally { if (fresh()) busy.value = false }
}
function save() {
  let settings
  try { settings = JSON.parse(text.value) }
  catch (caught) { error.value = `草稿不是合法 JSON：${caught.message}`; return }
  request(`/api/host/mcp/${encodeURIComponent(name.value)}`, { method: 'PUT', body: JSON.stringify({ settings }) },
    '根配置已保存；当前连接不变，重启宿主后使用新绑定。', true)
}
function remove() {
  if (!window.confirm(`从根配置删除 ${selected.value}？当前连接不变，如需立即停止请另点“断开”。`)) return
  request(`/api/host/mcp/${encodeURIComponent(selected.value)}`, { method: 'DELETE' }, '根配置已移除；当前运行绑定不变。', true)
}
function act(action) {
  if (action === 'connect' && !window.confirm('按当前运行绑定重新连接并刷新工具目录？这会关闭旧连接，不会采用尚未重启的保存值。')) return
  request(`/api/host/mcp/${encodeURIComponent(selected.value)}/${action}`, { method: 'POST' },
    action === 'connect' ? '当前运行绑定已连接，工具目录已刷新。' : '当前连接已关闭，工具已撤下。')
}
baseline.value = '\n'
onMounted(read)
</script>

<template>
  <section class="surface mcp" aria-labelledby="mcp-title">
    <div class="heading"><h2 id="mcp-title">MCP 服务</h2>
      <v-btn variant="outlined" :loading="loading" :disabled="busy || loading" @click="read">重读</v-btn></div>
    <p class="muted">配置登记与实际连接分开；工具受启用场景和角色许可限制，经 tool_search 发现。连接失败不自动重连，调用失败不重做。stdio 是可信宿主进程，不是任务沙箱。</p>
    <v-alert v-if="error" type="error" variant="tonal" role="alert">{{ error }}</v-alert>
    <v-alert v-if="notice" type="success" variant="tonal" role="status">{{ notice }}</v-alert>
    <template v-if="data">
      <v-chip variant="tonal" :color="data.restart_required ? 'warning' : 'info'">{{ data.restart_required ? '保存绑定待重启' : '保存绑定与启动配置一致' }}</v-chip>
      <div class="choices">
        <v-btn v-for="item in entries" :key="item" :variant="selected===item ? 'tonal' : 'text'" :disabled="busy || loading" @click="select(item)">{{ item }}</v-btn>
        <v-btn variant="outlined" :disabled="busy || loading" @click="select('')">新增服务</v-btn>
      </div>
      <div v-if="running" class="runtime">
        <h3>{{ running.name }} · {{ status[running.status] }}</h3>
        <p class="muted">运行传输：{{ running.transport }}；协议：{{ running.protocol || '尚未取得' }}；启用场景：{{ running.scenes.map(sceneName).join('、') || '无' }}</p>
        <pre v-if="running.error" role="alert">{{ running.error }}</pre>
        <div class="actions">
          <v-btn variant="outlined" :disabled="busy || running.status==='disabled'" @click="act('connect')">按运行绑定重新连接</v-btn>
          <v-btn variant="outlined" :disabled="busy || !['running','connecting'].includes(running.status)" @click="act('disconnect')">断开</v-btn>
        </div>
        <details><summary>实际工具（{{ running.tools.length }}）</summary>
          <article v-for="tool in running.tools" :key="tool.name"><strong>{{ tool.name }}</strong><p>{{ tool.description }}</p><pre>{{ JSON.stringify(tool.parameters,null,2) }}</pre></article>
        </details>
        <details v-if="running.errors.length"><summary>本次进程的最近错误</summary>
          <article v-for="(item,index) in running.errors" :key="index"><span>{{ fmtTime(item.at) }} · {{ item.where }}</span><pre>{{ item.error }}</pre></article>
        </details>
      </div>
      <form @submit.prevent="save">
        <v-text-field v-model="name" label="服务名" hint="小写字母开头，最多24位；使用字母、数字、单下划线。" persistent-hint :disabled="busy || loading || Boolean(selected)" />
        <div class="actions"><v-btn variant="text" :disabled="busy || loading" @click="transport('stdio')">填入 stdio 传输模板</v-btn><v-btn variant="text" :disabled="busy || loading" @click="transport('http')">填入 HTTP 传输模板</v-btn></div>
        <v-textarea v-model="text" label="服务配置 JSON" rows="14" auto-grow :disabled="busy || loading" spellcheck="false" />
        <p class="muted">可用场景：{{ data.scenes.join('、') }}。enabled 控制下次启动是否连接；scenes 指定可用场景。env / headers 的已保存值显示 null，保存 null 保持原值；输入新字符串替换，删除键即移除。密钥不回显。</p>
        <p class="muted">二进制图片／音频结果暂不处理，会明确报错；资源 URI 只表示服务提供的引用，不表示已下载或已上传 QQ。</p>
        <div class="actions"><v-btn type="submit" color="primary" :disabled="busy || !name || !dirty" :loading="busy">保存根配置</v-btn>
          <v-btn v-if="data.saved[selected]" variant="outlined" color="error" :disabled="busy || loading" @click="remove">删除保存配置</v-btn></div>
      </form>
    </template>
  </section>
</template>

<style scoped>
.heading,.actions,.choices{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.heading{justify-content:space-between}
h2{font-size:18px}h3{font-size:15px}.mcp{display:grid;gap:12px}.runtime{border:1px solid var(--line);padding:14px;border-radius:10px;display:grid;gap:10px}
form{display:grid;gap:10px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}article{margin:10px 0}.mcp :deep(.v-btn){min-height:44px}
</style>
