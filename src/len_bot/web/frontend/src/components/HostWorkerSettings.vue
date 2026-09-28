<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api, sceneName } from '../api.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'

const props = defineProps({ scene: { type: String, required: true } })
const emit = defineEmits(['dirty', 'scene-dirty', 'saving'])
const snapshot = ref(null), workerDraft = ref(null), workerPrevious = ref(null), taskDraft = ref(null)
const admins = ref([]), whitelist = ref([])
const loading = ref(false), saving = ref('')
const readError = ref(''), workerError = ref(''), taskError = ref('')
const workerNotice = ref(''), taskNotice = ref('')
const beginRead = useRequestGuard()
const beginWorkerSave = useRequestGuard()
const beginTaskSave = useRequestGuard(() => props.scene)
const roles = [
  { title: '主人', value: 'owner' }, { title: '管理员', value: 'admin' },
  { title: '群管理员', value: 'group_manager' }, { title: '白名单', value: 'whitelist' },
  { title: '成员', value: 'member' },
]
const workerFields = {
  resources: [['cpus', 'CPU 数'], ['memory', '内存限制'], ['pids_limit', '进程上限'],
    ['command_timeout_seconds', '容器命令超时（秒）'], ['max_running', '全局同时运行任务'],
    ['max_containers', '全局任务容器上限'], ['max_scene_containers', '每场景容器上限']],
  calls: [['max_calls', '每任务最多模型请求'], ['max_request_bytes', '单次请求字节上限'],
    ['max_response_bytes', '单次响应字节上限']],
  waiting: [['active_timeout_seconds', '活动执行超时（秒）'],
    ['input_timeout_seconds', '等待人工输入超时（秒）']],
  files: [['max_file_bytes', '单个交付文件字节上限']],
  compaction: [['compaction_reserve_tokens', '压缩预留 token'],
    ['compaction_keep_recent_tokens', '压缩保留近期 token']],
  egress: [['max_task_bytes', '每任务累计代理传输字节上限'],
    ['max_scene_daily_bytes', '每场景每日累计代理传输字节上限'],
    ['max_connections', '每任务最多并发连接'],
    ['bytes_per_second', '代理传输字节/秒上限'],
    ['connect_timeout_seconds', '连接超时（秒）'],
    ['header_timeout_seconds', '请求头超时（秒）']],
}

function copy(value) { return JSON.parse(JSON.stringify(value)) }
function numeric(value) { return value === '' ? '' : Number(value) }
function freshWorker() {
  return {
    docker_binary: '', docker_host: '', image: '', workspace_root: '', runtime_root: '',
    delivery_root: '', uid: '', gid: '', cpus: 2, memory: '2g', pids_limit: 512,
    command_timeout_seconds: 30, max_running: 4, max_containers: 8,
    max_scene_containers: 4, max_calls: 40, max_request_bytes: 8 * 1024 * 1024,
    max_response_bytes: 64 * 1024 * 1024, max_cost: null,
    compaction_reserve_tokens: 16384, compaction_keep_recent_tokens: 20000,
    active_timeout_seconds: 1800, input_timeout_seconds: 1800,
    max_file_bytes: 25 * 1024 * 1024, input_support: 'text', model_reasoning: null,
    egress: { enabled: true, max_task_bytes: 524288000, max_scene_daily_bytes: 2147483648,
      max_connections: 16, bytes_per_second: 8388608,
      connect_timeout_seconds: 30, header_timeout_seconds: 30 },
  }
}
function taskBody() {
  if (!taskDraft.value) return null
  return { ...copy(taskDraft.value), admins: admins.value.map(row => row.value),
    whitelist: whitelist.value.map(row => row.value) }
}
function workerBody() {
  if (workerDraft.value === null) return null
  return { ...copy(workerDraft.value), max_cost: workerDraft.value.max_cost === '' ? null : workerDraft.value.max_cost }
}
const workerDirty = computed(() => snapshot.value !== null &&
  JSON.stringify(workerBody()) !== JSON.stringify(snapshot.value.saved.worker))
const taskDirty = computed(() => snapshot.value !== null && taskDraft.value !== null &&
  JSON.stringify(taskBody()) !== JSON.stringify(snapshot.value.saved.scenes[props.scene]?.tasks))
const taskRestart = computed(() => snapshot.value !== null &&
  JSON.stringify(snapshot.value.saved.scenes[props.scene]?.tasks) !==
  JSON.stringify(snapshot.value.running.scenes[props.scene]?.tasks))
const dirty = computed(() => workerDirty.value || taskDirty.value)
watch(dirty, value => emit('dirty', value), { immediate: true })
watch(taskDirty, value => emit('scene-dirty', value), { immediate: true })
watch(saving, value => emit('saving', Boolean(value)), { immediate: true })
onBeforeUnmount(() => { emit('dirty', false); emit('scene-dirty', false); emit('saving', false) })

function adoptTask(value, scene = props.scene) {
  const item = value.saved.scenes[scene]
  if (!item) { taskDraft.value = null; admins.value = []; whitelist.value = []; return }
  taskDraft.value = copy(item.tasks)
  admins.value = item.tasks.admins.map(value => ({ value }))
  whitelist.value = item.tasks.whitelist.map(value => ({ value }))
}
function adoptAll(value) {
  snapshot.value = value
  workerDraft.value = copy(value.saved.worker)
  workerPrevious.value = null
  adoptTask(value)
  workerNotice.value = ''; taskNotice.value = ''
}
watch(() => props.scene, scene => {
  if (snapshot.value) adoptTask(snapshot.value, scene)
  taskError.value = ''; taskNotice.value = ''
})
async function read(confirmDiscard = true) {
  if (confirmDiscard && dirty.value && !window.confirm('放弃任务执行与当前场景的未保存草稿，重读根配置？')) return
  const fresh = beginRead()
  loading.value = true
  try {
    const value = await api('/api/host/settings')
    if (!fresh()) return
    adoptAll(value)
    readError.value = ''; workerError.value = ''; taskError.value = ''
  } catch (error) { if (fresh()) readError.value = error.message }
  finally { if (fresh()) loading.value = false }
}
function toggleWorker(value) {
  if (value) workerDraft.value = workerPrevious.value === null ? freshWorker() : copy(workerPrevious.value)
  else { workerPrevious.value = copy(workerDraft.value); workerDraft.value = null }
  workerError.value = ''; workerNotice.value = ''
}
function errorMessage(error) {
  return error.status >= 400 && error.status < 500
    ? `保存未被接受：${error.message}`
    : `保存结果未确认：${error.message} 草稿已保留；请重读根配置核对，不会自动重试。`
}
async function saveWorker() {
  if (!workerDirty.value || loading.value || saving.value) return
  if (workerDraft.value !== null) {
    if (workerDraft.value.model_reasoning === null) {
      workerError.value = '请明确选择任务模型是否实际支持推理；此处不从模型名推断。'; return
    }
    if ([workerDraft.value.docker_binary, workerDraft.value.docker_host, workerDraft.value.image,
      workerDraft.value.workspace_root, workerDraft.value.runtime_root,
      workerDraft.value.delivery_root].some(value => !String(value).trim()) ||
      workerDraft.value.uid === '' || workerDraft.value.gid === '') {
      workerError.value = 'Docker 程序、socket、镜像、三个目录及 UID/GID 都须明确填写。'; return
    }
  }
  const fresh = beginWorkerSave(), payload = { worker: workerBody() }
  const preserveTask = taskDirty.value
  saving.value = 'worker'; workerError.value = ''; workerNotice.value = ''
  try {
    const value = await api('/api/host/settings/worker', { method: 'PUT', body: JSON.stringify(payload) })
    if (!fresh()) return
    snapshot.value = value
    workerDraft.value = copy(value.saved.worker)
    if (!preserveTask) adoptTask(value)
    workerNotice.value = value.restart_required.worker
      ? '任务执行配置已写入根文件；当前运行设置不变，重启后生效。'
      : '任务执行配置已写入根文件；与当前运行值一致。'
  } catch (error) { if (fresh()) workerError.value = errorMessage(error) }
  finally { if (fresh()) saving.value = '' }
}
async function saveTasks() {
  if (!taskDirty.value || loading.value || saving.value || !props.scene) return
  const target = props.scene, fresh = beginTaskSave(), payload = { tasks: taskBody() }
  const preserveWorker = workerDirty.value
  saving.value = 'tasks'; taskError.value = ''; taskNotice.value = ''
  try {
    const value = await api(`/api/host/settings/scenes/${encodeURIComponent(target)}/tasks`, {
      method: 'PUT', body: JSON.stringify(payload),
    })
    if (!fresh()) return
    snapshot.value = value
    adoptTask(value, target)
    if (!preserveWorker) workerDraft.value = copy(value.saved.worker)
    taskNotice.value = taskRestart.value
      ? '场景任务权限已写入根文件；当前运行权限不变，重启后生效。'
      : '场景任务权限已写入根文件；与当前运行值一致。'
  } catch (error) { if (fresh()) taskError.value = errorMessage(error) }
  finally { if (fresh()) saving.value = '' }
}
onMounted(() => read(false))
</script>

<template>
  <div class="page-stack worker-settings">
    <section class="surface" aria-labelledby="worker-settings-title">
      <header class="section-heading"><div><p class="eyebrow">根配置 · 重启后生效</p><h2 id="worker-settings-title">任务执行环境</h2></div>
        <v-btn variant="outlined" :loading="loading" :disabled="Boolean(saving)" @click="read()">重读任务配置</v-btn></header>
      <p class="muted">这里只保存独立任务的容器与模型代理限额，不构建镜像、不启动容器或发起任务。当前任务模型须先在模型配置页明确绑定 worker 用途；保存不会改变在途任务。运行中的 Docker socket、工作区根与运行目录不能经面板迁移，后端会明确拒绝。</p>
      <v-alert v-if="readError" type="error" variant="tonal" role="alert">{{ readError }}</v-alert>
      <v-alert v-if="workerError" type="error" variant="tonal" role="alert">{{ workerError }}</v-alert>
      <v-alert v-if="workerNotice && !workerDirty" type="success" variant="tonal" role="status">{{ workerNotice }}</v-alert>
      <p v-if="loading && !snapshot" role="status">正在读取任务配置…</p>
      <template v-if="snapshot">
        <div class="status-row"><strong>当前运行：{{ snapshot.running.worker===null?'未配置任务执行器':snapshot.running.worker.image }}</strong>
          <v-chip variant="tonal" :color="snapshot.restart_required.worker?'warning':'info'">{{ snapshot.restart_required.worker?'保存值待重启':'保存值与运行值一致' }}</v-chip></div>
        <p class="muted">公共联网代理：运行值 {{ snapshot.running.worker?.egress.enabled?'配置启用':'未启用' }}；保存值 {{ snapshot.saved.worker?.egress.enabled?'计划启用':'未启用' }}。这里不表示域名已实际联网。</p>
        <p class="muted">保存值中的任务模型：{{ snapshot.saved.models.roles.worker===null?'未绑定':`${snapshot.saved.models.roles.worker.provider} / ${snapshot.saved.models.roles.worker.model}` }}。配置费用上限时，须在模型页为此提供方与精确模型设置价格。</p>
        <form @submit.prevent="saveWorker"><fieldset :disabled="loading || Boolean(saving)">
          <v-switch :model-value="workerDraft!==null" label="在根配置中启用任务执行环境" :disabled="loading || Boolean(saving)" hide-details @update:model-value="toggleWorker" />
          <p v-if="workerDraft===null" class="muted">未配置执行环境；场景任务不能启用。若已有场景启用任务，须先分别停用，完整配置校验才会接受移除此环境。</p>
          <template v-else>
            <p class="muted">请填本机真实 Docker 程序与 Unix socket；不会从当前机器环境、进程变量或大脑模型推断。三个任务目录必须在实例根内且互不嵌套。</p>
            <div class="form-grid">
              <v-text-field v-model="workerDraft.docker_binary" label="Docker 可执行文件绝对路径" hide-details="auto" />
              <v-text-field v-model="workerDraft.docker_host" label="本机 Docker socket（unix:///…）" hide-details="auto" />
              <v-text-field v-model="workerDraft.image" label="任务镜像名" hide-details="auto" />
              <v-text-field :model-value="workerDraft.uid" type="number" step="1" label="容器 UID" hide-details="auto" @update:model-value="value=>workerDraft.uid=numeric(value)" />
              <v-text-field :model-value="workerDraft.gid" type="number" step="1" label="容器 GID" hide-details="auto" @update:model-value="value=>workerDraft.gid=numeric(value)" />
              <v-text-field v-model="workerDraft.workspace_root" label="任务工作区目录" hint="示例：data/tasks/workspaces；需自行确认权限" persistent-hint />
              <v-text-field v-model="workerDraft.runtime_root" label="任务运行目录" hint="示例：data/tasks/runtime；不能在工作区内" persistent-hint />
              <v-text-field v-model="workerDraft.delivery_root" label="任务交付目录" hint="示例：data/tasks/deliveries；复制成功不等于 QQ 上传" persistent-hint />
              <v-select v-model="workerDraft.model_reasoning" label="所选任务模型实际支持推理吗？" :items="[{title:'不支持',value:false},{title:'支持',value:true}]" :disabled="loading || Boolean(saving)" hint="必须人工选择；不从模型名推断，实际请求参数仍以根绑定为准" persistent-hint />
              <v-select v-model="workerDraft.input_support" label="所选任务模型输入能力" :items="[{title:'仅文本',value:'text'},{title:'文本与图片',value:'text-image'}]" :disabled="loading || Boolean(saving)" hide-details="auto" />
            </div>
            <details><summary>执行资源与并发上限</summary><div class="form-grid">
              <v-text-field v-for="[key,label] in workerFields.resources" :key="key" :model-value="workerDraft[key]" :type="key==='memory'?'text':'number'" :step="key==='cpus'||key==='command_timeout_seconds'?'any':'1'" :label="label" hide-details="auto" @update:model-value="value=>workerDraft[key]=key==='memory'?value:numeric(value)" />
            </div></details>
            <details><summary>公共联网回环代理与限额</summary>
              <p class="muted">任务容器保持 network-none，经宿主回环代理才可出网。启用配置不代表任何域名已实际连通；DNS 若解析到保留地址会保留原错拒绝，不自动换 DNS、地址或参数。保存不立即生效，也不会自动构建镜像。</p>
              <v-switch v-model="workerDraft.egress.enabled" label="允许任务使用公共联网回环代理" :disabled="loading || Boolean(saving)" hide-details />
              <div class="form-grid"><v-text-field v-for="[key,label] in workerFields.egress" :key="key"
                :model-value="workerDraft.egress[key]" type="number"
                :step="key.endsWith('_seconds')?'any':'1'" :label="label" hide-details="auto"
                @update:model-value="value=>workerDraft.egress[key]=numeric(value)" /></div>
            </details>
            <details><summary>模型请求、字节与金额上限</summary><div class="form-grid">
              <v-text-field v-for="[key,label] in workerFields.calls" :key="key" :model-value="workerDraft[key]" type="number" step="1" :label="label" hide-details="auto" @update:model-value="value=>workerDraft[key]=numeric(value)" />
              <v-text-field :model-value="workerDraft.max_cost ?? ''" label="每任务费用上限（可留空）" inputmode="decimal" hint="按精确模型价目与已上报用量估算；未知费用不按零计算" persistent-hint @update:model-value="value=>workerDraft.max_cost=value===''?null:value" />
            </div></details>
            <details><summary>等待与交付文件上限</summary><div class="form-grid">
              <v-text-field v-for="[key,label] in [...workerFields.waiting,...workerFields.files]" :key="key" :model-value="workerDraft[key]" type="number" :step="key==='max_file_bytes'?'1':'any'" :label="label" hide-details="auto" @update:model-value="value=>workerDraft[key]=numeric(value)" />
            </div></details>
            <details><summary>Pi 原生会话压缩预算</summary><p class="muted">两项之和须小于任务模型上下文窗口；小窗口须显式填写，不自动缩小。</p><div class="form-grid">
              <v-text-field v-for="[key,label] in workerFields.compaction" :key="key" :model-value="workerDraft[key]" type="number" step="1" :label="label" hide-details="auto" @update:model-value="value=>workerDraft[key]=numeric(value)" />
            </div></details>
          </template>
        </fieldset>
        <p v-if="workerDirty" class="dirty-note" role="status">执行环境草稿尚未保存。</p>
        <div class="form-actions"><v-btn type="submit" color="primary" :loading="saving==='worker'" :disabled="!workerDirty || loading || Boolean(saving)">保存执行环境</v-btn>
          <span class="muted">保存只改根文件；实际容器与模型代理须重启宿主后按任务启动。</span></div>
        </form>
      </template>
    </section>

    <section v-if="snapshot && taskDraft" class="surface" aria-labelledby="scene-tasks-title">
      <header class="section-heading"><div><p class="eyebrow">{{ sceneName(scene) }} · 重启后生效</p><h2 id="scene-tasks-title">此场景的任务权限</h2></div>
        <v-chip variant="tonal" :color="taskRestart?'warning':'info'">{{ taskRestart?'任务权限待重启':'任务权限与运行值一致' }}</v-chip></header>
      <p class="muted">只保存此场景的 tasks 块，不覆盖上方参与、提醒或角色草稿。运行中状态：{{ snapshot.running.scenes[scene]?.tasks?.enabled?'已开放':'未开放' }}；当前保存值：{{ taskDraft.enabled?'计划开放':'不开放' }}。</p>
      <v-alert v-if="taskError" type="error" variant="tonal" role="alert">{{ taskError }}</v-alert>
      <v-alert v-if="taskNotice && !taskDirty" type="success" variant="tonal" role="status">{{ taskNotice }}</v-alert>
      <v-alert v-if="taskDraft.enabled && (snapshot.saved.worker===null || snapshot.saved.models.roles.worker===null)" type="warning" variant="tonal">启用任务须先保存全局执行环境和 worker 模型绑定；此保存请求将由完整配置校验拒绝，不能自动借用大脑模型。</v-alert>
      <form @submit.prevent="saveTasks"><fieldset :disabled="loading || Boolean(saving)">
        <div class="form-grid"><v-switch v-model="taskDraft.enabled" label="启用此场景任务" :disabled="loading || Boolean(saving)" hide-details />
          <v-text-field :model-value="taskDraft.owner ?? ''" label="主人 QQ（留空表示无主人）" inputmode="numeric" hide-details="auto" @update:model-value="value=>taskDraft.owner=value===''?null:value" />
          <v-text-field :model-value="taskDraft.max_running" type="number" step="1" label="此场景同时执行上限" hide-details="auto" @update:model-value="value=>taskDraft.max_running=numeric(value)" />
          <v-text-field :model-value="taskDraft.max_daily_tasks" type="number" step="1" label="每人每日新任务上限" hide-details="auto" @update:model-value="value=>taskDraft.max_daily_tasks=numeric(value)" /></div>
        <div class="list-block"><h3>管理员 QQ</h3><div v-for="(row,index) in admins" :key="index" class="list-row"><v-text-field v-model="row.value" :label="`管理员 QQ ${index+1}`" inputmode="numeric" hide-details="auto" /><v-btn variant="outlined" :aria-label="`删除管理员 QQ ${index+1}`" @click="admins.splice(index,1)">删除</v-btn></div><v-btn variant="outlined" @click="admins.push({value:''})">添加管理员</v-btn></div>
        <div class="list-block"><h3>白名单 QQ</h3><div v-for="(row,index) in whitelist" :key="index" class="list-row"><v-text-field v-model="row.value" :label="`白名单 QQ ${index+1}`" inputmode="numeric" hide-details="auto" /><v-btn variant="outlined" :aria-label="`删除白名单 QQ ${index+1}`" @click="whitelist.splice(index,1)">删除</v-btn></div><v-btn variant="outlined" @click="whitelist.push({value:''})">添加白名单</v-btn></div>
        <div class="form-grid"><v-select v-model="taskDraft.delegate_roles" label="允许委托任务的身份" :items="roles" :disabled="loading || Boolean(saving)" multiple chips closable-chips hide-details="auto" />
          <v-select v-model="taskDraft.manage_roles" label="允许管理他人任务的身份" :items="roles" :disabled="loading || Boolean(saving)" multiple chips closable-chips hide-details="auto" /></div>
        <h3>此场景公共联网限额覆盖</h3>
        <p class="muted">三项留空表示沿用上方全局限额，不表示 0；不会自动增减或重试限制。</p>
        <div class="form-grid">
          <v-text-field v-for="[key,label] in [['egress_max_task_bytes','每任务字节上限覆盖'],['egress_max_daily_bytes','本场景每日字节上限覆盖'],['egress_bytes_per_second','每秒字节限速覆盖']]"
            :key="key" :model-value="taskDraft[key] ?? ''" type="number" step="1" :label="label" hide-details="auto"
            @update:model-value="value=>taskDraft[key]=value===''||value===null?null:numeric(value)" />
        </div>
      </fieldset>
        <p v-if="taskDirty" class="dirty-note" role="status">此场景任务权限草稿尚未保存。</p>
        <div class="form-actions"><v-btn type="submit" color="primary" :loading="saving==='tasks'" :disabled="!taskDirty || loading || Boolean(saving)">保存此场景任务权限</v-btn>
          <span class="muted">仅保存当前场景 tasks 块，不能启动容器，也不会改写其他场景。</span></div>
      </form>
    </section>
  </div>
</template>

<style scoped>
.worker-settings{min-width:0;overflow-wrap:anywhere}.section-heading,.status-row{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap}
.section-heading h2{font-size:18px;margin:0 0 12px}.eyebrow{font-size:12px;letter-spacing:.08em;color:var(--primary);font-weight:700;margin:0 0 5px}
.worker-settings fieldset{border:0;padding:0;min-width:0;margin:18px 0}.form-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,230px),1fr));gap:12px;margin:12px 0}
.worker-settings details{border-top:1px solid var(--line);margin:14px 0;padding:12px 0}.worker-settings summary{cursor:pointer;min-height:44px;font-weight:700}
.list-block{border-top:1px solid var(--line);padding:14px 0;margin:10px 0}.list-block h3{font-size:15px;margin:0 0 8px}.list-row{display:flex;align-items:flex-start;gap:10px;margin:10px 0;min-width:0}.list-row>:first-child{flex:1;min-width:0}
.dirty-note{border-left:3px solid var(--primary);background:var(--selected-bg);padding:8px 12px}.worker-settings :deep(.v-btn){min-height:44px}.worker-settings :deep(.v-alert),.worker-settings .muted{overflow-wrap:anywhere}
@media(max-width:600px){.section-heading>.v-btn{width:100%}.list-row{flex-wrap:wrap}.list-row>.v-btn{width:100%}}
</style>
