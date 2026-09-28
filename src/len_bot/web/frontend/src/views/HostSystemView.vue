<script setup>
import HostRetention from '../components/HostRetention.vue'
import HostLimits from '../components/HostLimits.vue'
import HostProcessingSettings from '../components/HostProcessingSettings.vue'
import { computed, onMounted, ref } from 'vue'
import { api } from '../api.js'
import { developerMode } from '../composables/useDeveloperMode.js'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'

const snapshot = ref(null), connection = ref(null), panel = ref(null)
const token = ref(''), password = ref('')
const loading = ref(false), saving = ref('')
const readError = ref(''), saveError = ref(''), savedNotice = ref('')
const beginRead = useRequestGuard(), beginSave = useRequestGuard()
const defaults = { host: '', port: 0, username: '', cookie_secure: false }
function copy(value) { return JSON.parse(JSON.stringify(value)) }
function numeric(value) { return value === '' ? '' : Number(value) }
function onebotBody(value) {
  const common = {
    mode: value.mode, action_transport: value.action_transport,
    http_url: value.action_transport === 'http' ? value.http_url : null,
    request_timeout_seconds: value.request_timeout_seconds,
    ping_interval_seconds: value.ping_interval_seconds,
    ping_timeout_seconds: value.ping_timeout_seconds,
    max_frame_bytes: value.max_frame_bytes,
    upload_visible_root: value.upload_visible_root === '' ? null : value.upload_visible_root,
  }
  return value.mode === 'forward_ws'
    ? { ...common, ws_url: value.ws_url }
    : { ...common, listen_host: value.listen_host, listen_port: value.listen_port }
}
function connectionBody(value) {
  return {
    onebot: onebotBody(value.onebot), timezone: value.timezone,
    owner_qq: value.owner_qq,
    delivery: value.delivery, max_steps: value.max_steps,
    turn_timeout_seconds: value.turn_timeout_seconds,
    max_model_requests: value.max_model_requests,
    text_delivery: copy(value.text_delivery),
  }
}
function panelBody(value) {
  return { host: value.host, port: value.port, username: value.username,
    cookie_secure: value.cookie_secure }
}
function adoptConnection(value) {
  connection.value = copy(value.saved.connection)
  token.value = ''
}
function adoptPanel(value) {
  panel.value = copy(value.saved.panel ?? defaults)
  password.value = ''
}
function adoptAll(value) {
  snapshot.value = value
  adoptConnection(value)
  adoptPanel(value)
  savedNotice.value = ''
}
const connectionDirty = computed(() => snapshot.value !== null && (
  token.value !== '' || JSON.stringify(connectionBody(connection.value)) !== JSON.stringify(connectionBody(snapshot.value.saved.connection))
))
const panelDirty = computed(() => snapshot.value !== null && (
  password.value !== '' || JSON.stringify(panelBody(panel.value)) !== JSON.stringify(panelBody(snapshot.value.saved.panel ?? defaults))
))
const dirty = computed(() => connectionDirty.value || panelDirty.value)
useUnsavedChanges(dirty)
async function read(confirmDiscard = true) {
  if (confirmDiscard && dirty.value && !window.confirm('放弃未保存的连接与面板草稿，重读根配置？')) return
  const fresh = beginRead()
  loading.value = true
  try {
    const value = await api('/api/host/settings')
    if (!fresh()) return
    adoptAll(value)
    readError.value = ''
    saveError.value = ''
  } catch (error) { if (fresh()) readError.value = error.message }
  finally { if (fresh()) loading.value = false }
}
function changeMode(value) {
  const previous = connection.value.onebot
  const common = onebotBody(previous)
  connection.value.onebot = value === 'forward_ws'
    ? { ...common, mode: value, ws_url: previous.ws_url ?? '' }
    : { ...common, mode: value, listen_host: previous.listen_host ?? '', listen_port: previous.listen_port ?? 0 }
}
function changeAction(value) {
  connection.value.onebot.action_transport = value
  connection.value.onebot.http_url = value === 'http' ? (connection.value.onebot.http_url ?? '') : null
}
function errorMessage(error) {
  return error.status >= 400 && error.status < 500
    ? `保存未被接受：${error.message}`
    : `保存结果未确认：${error.message} 草稿已保留；请重读根配置核对，不会自动重试。`
}
async function saveConnection() {
  if (!connectionDirty.value || loading.value || saving.value) return
  const fresh = beginSave()
  const payload = { ...connectionBody(connection.value), access_token: token.value === '' ? null : token.value }
  saving.value = 'connection'; saveError.value = ''; savedNotice.value = ''
  try {
    const value = await api('/api/host/settings/connection', { method: 'PUT', body: JSON.stringify(payload) })
    if (!fresh()) return
    snapshot.value = value
    adoptConnection(value)
    savedNotice.value = value.restart_required.connection
      ? '连接与运行设置已保存；现有连接、轮次参数和消息出口不变，重启宿主后生效。'
      : '连接与运行设置已保存；与当前运行值一致。'
  } catch (error) { if (fresh()) saveError.value = errorMessage(error) }
  finally { if (fresh()) saving.value = '' }
}
async function savePanel() {
  if (!panelDirty.value || loading.value || saving.value) return
  const fresh = beginSave()
  const payload = { ...panelBody(panel.value), password: password.value === '' ? null : password.value }
  saving.value = 'panel'; saveError.value = ''; savedNotice.value = ''
  try {
    const value = await api('/api/host/settings/panel', { method: 'PUT', body: JSON.stringify(payload) })
    if (!fresh()) return
    snapshot.value = value
    adoptPanel(value)
    savedNotice.value = value.restart_required.panel
      ? '面板监听与账户已保存；当前监听地址和登录会话不变，重启宿主后生效。'
      : '面板监听与账户已保存；与当前运行值一致。'
  } catch (error) { if (fresh()) saveError.value = errorMessage(error) }
  finally { if (fresh()) saving.value = '' }
}
onMounted(() => read(false))
</script>

<template>
  <div class="page-stack host-system">
    <header class="page-intro"><div><p class="eyebrow">独立多场景宿主</p><h1>连接与运行设置</h1>
      <p class="muted">只编辑当前宿主根配置的真实连接、运行节奏与面板监听。保存不会重连、重启或改变正在执行的轮次；Bot QQ 与数据库路径不在此修改。</p></div>
      <v-btn variant="outlined" :loading="loading" :disabled="Boolean(saving)" @click="read()">重读根配置</v-btn></header>
    <v-alert v-if="readError" type="error" variant="tonal" role="alert" :title="snapshot?'读取失败 · 保留上次草稿':'读取宿主设置失败'">{{ readError }}</v-alert>
    <v-alert v-if="saveError" type="error" variant="tonal" role="alert">{{ saveError }}</v-alert>
    <v-alert v-if="savedNotice" type="success" variant="tonal" role="status">{{ savedNotice }}</v-alert>
    <div v-if="loading && !snapshot" class="surface empty-state" role="status">正在读取运行值与根配置保存值…</div>
    <template v-if="snapshot && connection && panel">
      <section class="surface">
      <h2>显示偏好</h2><v-switch v-model="developerMode" label="开发者模式：显示原始模型请求、用量及事件结构" color="primary" hide-details />
      <p class="muted">仅改变当前浏览器页签的显示，不写入运行配置、不更改工具权限；刷新后关闭。</p>
    </section>
    <section class="surface"><div class="section-heading"><h2>当前运行的连接与轮次</h2><v-chip variant="tonal" :color="snapshot.restart_required.connection?'warning':'info'">{{ snapshot.restart_required.connection?'保存值待重启':'保存值与运行值一致' }}</v-chip></div>
        <dl class="facts"><div><dt>Bot QQ（只读）</dt><dd>{{ snapshot.running.connection.bot_qq }}</dd></div>
          <div><dt>主人 QQ（运行中）</dt><dd>{{ snapshot.running.connection.owner_qq || "未配置" }}</dd></div>
          <div><dt>OneBot 模式</dt><dd>{{ snapshot.running.connection.onebot.mode }}</dd></div>
          <div><dt>平台入口</dt><dd>{{ snapshot.running.connection.onebot.mode==='forward_ws'?snapshot.running.connection.onebot.ws_url:`${snapshot.running.connection.onebot.listen_host}:${snapshot.running.connection.onebot.listen_port}` }}</dd></div>
          <div><dt>动作出口</dt><dd>{{ snapshot.running.connection.onebot.action_transport }}{{ snapshot.running.connection.onebot.http_url?` · ${snapshot.running.connection.onebot.http_url}`:'' }}</dd></div>
          <div><dt>NapCat 可见交付目录</dt><dd>{{ snapshot.running.connection.onebot.upload_visible_root ?? '未开放文件上传路径' }}</dd></div>
          <div><dt>时区 / 消息出口</dt><dd>{{ snapshot.running.connection.timezone }} / {{ snapshot.running.connection.delivery==='onebot'?'OneBot':'模拟发送' }}</dd></div>
          <div><dt>轮次 / 模型并发</dt><dd>最多 {{ snapshot.running.connection.max_steps }} 步 · {{ snapshot.running.connection.turn_timeout_seconds }} 秒 · {{ snapshot.running.connection.max_model_requests }} 个模型请求</dd></div></dl>
        <details><summary>查看当前运行的完整节奏与传输参数</summary><dl class="facts"><div v-for="(value,key) in snapshot.running.connection.onebot" :key="key"><dt>{{ key }}</dt><dd>{{ key==='access_token_configured'?(value?'已配置':'未配置'):value===null?'未设置':value }}</dd></div>
          <div v-for="(value,key) in snapshot.running.connection.text_delivery" :key="key"><dt>{{ key }}</dt><dd>{{ value }}</dd></div></dl></details>
      </section>
      <form class="surface editor" @submit.prevent="saveConnection"><div class="section-heading"><h2>连接与运行 · 根配置保存值</h2><span class="muted">只写文件，重启生效</span></div>
        <fieldset :disabled="Boolean(saving)||loading">
          <p class="muted">Bot QQ {{ snapshot.saved.connection.bot_qq }} 仅展示，不接受修改。改变 OneBot 模式时请填入对应入口；不会主动测试地址。</p>
          <div class="form-grid"><v-select :model-value="connection.onebot.mode" label="OneBot 模式" :items="[{title:'正向 WebSocket',value:'forward_ws'},{title:'反向 WebSocket',value:'reverse_ws'}]" hide-details="auto" @update:model-value="changeMode" />
            <v-select :model-value="connection.onebot.action_transport" label="动作出口" :items="[{title:'WebSocket',value:'websocket'},{title:'HTTP',value:'http'}]" hide-details="auto" @update:model-value="changeAction" />
            <v-text-field v-if="connection.onebot.mode==='forward_ws'" v-model="connection.onebot.ws_url" label="正向 WebSocket 地址" hide-details="auto" />
            <template v-else><v-text-field v-model="connection.onebot.listen_host" label="反向监听地址" hide-details="auto" />
              <v-text-field :model-value="connection.onebot.listen_port" type="number" step="1" label="反向监听端口" hide-details="auto" @update:model-value="value=>connection.onebot.listen_port=numeric(value)" /></template>
            <v-text-field v-if="connection.onebot.action_transport==='http'" v-model="connection.onebot.http_url" label="HTTP 动作出口地址" hide-details="auto" />
            <v-text-field :model-value="connection.onebot.request_timeout_seconds" type="number" label="动作请求超时（秒）" hide-details="auto" @update:model-value="value=>connection.onebot.request_timeout_seconds=numeric(value)" />
            <v-text-field :model-value="connection.onebot.ping_interval_seconds" type="number" label="心跳间隔（秒）" hide-details="auto" @update:model-value="value=>connection.onebot.ping_interval_seconds=numeric(value)" />
            <v-text-field :model-value="connection.onebot.ping_timeout_seconds" type="number" label="心跳超时（秒）" hide-details="auto" @update:model-value="value=>connection.onebot.ping_timeout_seconds=numeric(value)" />
            <v-text-field :model-value="connection.onebot.max_frame_bytes" type="number" step="1" label="最大传输帧字节" hide-details="auto" @update:model-value="value=>connection.onebot.max_frame_bytes=numeric(value)" />
            <v-text-field v-model="connection.onebot.upload_visible_root" label="NapCat 可见交付目录（绝对 POSIX 路径，可留空）" hide-details="auto" />
            <v-text-field v-model="token" type="password" autocomplete="new-password" label="替换 OneBot 访问令牌（留空保留）" hide-details="auto" />
          </div><p class="muted">保存值中的令牌：{{ snapshot.saved.connection.onebot.access_token_configured?'已配置，原文不显示':'未配置' }}；空输入仅保留现值，不代表清除。</p>
          <p class="muted">文件上传前须实际把同一交付副本目录挂载给 NapCat，并在任务配置中另填 worker.delivery_root。保存这个可见路径不代表挂载已完成、任务文件已上传或客户端已收到。</p>
          <div class="form-grid"><v-text-field :model-value="connection.owner_qq || ''" label="主人 QQ（owner_qq，可留空）"
              hint="主人账号类能力的实际身份；留空不开放。不从局部任务/安排权限推断，重启后生效。" persistent-hint
              @update:model-value="value=>connection.owner_qq=value.trim() || null" />
            <v-text-field v-model="connection.timezone" label="业务时区（IANA）" hide-details="auto" />
            <v-select v-model="connection.delivery" label="消息出口" :items="[{title:'模拟发送',value:'simulated'},{title:'OneBot 实际发送',value:'onebot'}]" hide-details="auto" />
            <v-text-field :model-value="connection.max_steps" type="number" step="1" label="单轮最多步数" hide-details="auto" @update:model-value="value=>connection.max_steps=numeric(value)" />
            <v-text-field :model-value="connection.turn_timeout_seconds" type="number" label="整轮超时（秒）" hide-details="auto" @update:model-value="value=>connection.turn_timeout_seconds=numeric(value)" />
            <v-text-field :model-value="connection.max_model_requests" type="number" step="1" label="并发模型请求上限" hide-details="auto" @update:model-value="value=>connection.max_model_requests=numeric(value)" /></div>
          <h3>文字分条与节奏</h3><div class="form-grid"><v-text-field :model-value="connection.text_delivery.max_chars" type="number" step="1" label="单条最大字素簇数" hide-details="auto" @update:model-value="value=>connection.text_delivery.max_chars=numeric(value)" />
            <v-text-field :model-value="connection.text_delivery.min_interval_seconds" type="number" label="最短分条间隔（秒）" hide-details="auto" @update:model-value="value=>connection.text_delivery.min_interval_seconds=numeric(value)" />
            <v-text-field :model-value="connection.text_delivery.max_interval_seconds" type="number" label="最长分条间隔（秒）" hide-details="auto" @update:model-value="value=>connection.text_delivery.max_interval_seconds=numeric(value)" />
            <v-text-field :model-value="connection.text_delivery.chars_per_second" type="number" label="目标字素簇每秒" hide-details="auto" @update:model-value="value=>connection.text_delivery.chars_per_second=numeric(value)" /></div>
        </fieldset>
        <p v-if="connectionDirty" class="dirty-note" role="status">连接与运行草稿尚未保存。</p>
        <div class="form-actions"><v-btn type="submit" color="primary" :loading="saving==='connection'" :disabled="!connectionDirty||Boolean(saving)||loading">保存连接与运行设置</v-btn><span class="muted">保存后的网络连接、消息出口和并发上限不会热变更。</span></div>
      </form>
      <section class="surface"><div class="section-heading"><h2>当前运行的面板</h2><v-chip variant="tonal" :color="snapshot.restart_required.panel?'warning':'info'">{{ snapshot.restart_required.panel?'保存值待重启':'保存值与运行值一致' }}</v-chip></div>
        <p v-if="snapshot.running.panel">{{ snapshot.running.panel.host }}:{{ snapshot.running.panel.port }} · 账户 {{ snapshot.running.panel.username }} · Cookie Secure {{ snapshot.running.panel.cookie_secure?'开启':'关闭' }}。密码哈希和原文均不在页面返回。</p>
        <p v-else class="muted">当前运行实例未配置面板。</p>
      </section>
      <form class="surface editor" @submit.prevent="savePanel"><h2>面板监听与账户 · 根配置保存值</h2>
        <p class="muted">{{ snapshot.saved.panel===null?'根配置中没有面板设置；要保存须填写完整账户与新密码。':'已配置面板密码；新密码留空表示保留当前已保存的密码。' }}面板资源路径不在此修改。</p>
        <fieldset :disabled="Boolean(saving)||loading" class="form-grid"><v-text-field v-model="panel.host" label="面板监听地址" hide-details="auto" />
          <v-text-field :model-value="panel.port" type="number" step="1" label="面板监听端口" hide-details="auto" @update:model-value="value=>panel.port=numeric(value)" />
          <v-text-field v-model="panel.username" label="登录用户名" hide-details="auto" />
          <v-switch v-model="panel.cookie_secure" label="Secure Cookie" hide-details />
          <v-text-field v-model="password" type="password" autocomplete="new-password" label="替换面板密码（留空保留）" hide-details="auto" /></fieldset>
        <p v-if="panelDirty" class="dirty-note" role="status">面板草稿尚未保存。</p>
        <div class="form-actions"><v-btn type="submit" color="primary" :loading="saving==='panel'" :disabled="!panelDirty||Boolean(saving)||loading">保存面板设置</v-btn><span class="muted">当前监听与登录会话不变；重启后按保存值运行。</span></div>
      </form>
    </template>
    <HostProcessingSettings />
    <HostLimits />
    <HostRetention />
  </div>
</template>

<style scoped>
.host-system{max-width:1280px;margin-inline:auto}
.page-intro,.section-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:14px;flex-wrap:wrap}
.page-intro>div{min-width:0;flex:1 1 460px}.page-intro h1{margin:0 0 10px}
.eyebrow{font-size:12px;letter-spacing:.08em;color:var(--primary);font-weight:700;margin:0 0 5px}
.surface{min-width:0;overflow-wrap:anywhere}.surface h2{font-size:18px;margin:0 0 14px}.surface h3{font-size:15px;margin:16px 0 8px}
.editor fieldset{border:0;padding:0;min-width:0}.form-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,230px),1fr));gap:12px;margin:14px 0}
.facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr));gap:12px;margin:0 0 12px}.facts>div{min-width:0;overflow-wrap:anywhere}.facts dt{font-size:12px;color:var(--muted)}.facts dd{font-weight:650;margin:3px 0 0}
.host-system details{border-top:1px solid var(--line);padding:12px 0}.host-system summary{cursor:pointer;min-height:44px;font-weight:700}
.dirty-note{border-left:3px solid var(--primary);background:var(--selected-bg);padding:8px 12px}
.host-system :deep(.v-btn){min-height:44px}.host-system :deep(.v-alert),.host-system .muted{overflow-wrap:anywhere}
@media(max-width:600px){.page-intro{display:grid}.page-intro>.v-btn{width:100%}.surface{padding:16px}}
</style>
