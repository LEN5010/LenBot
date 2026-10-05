<script setup>
import { computed, ref, watch } from 'vue'
import { api } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { notify, readPendingRestart } from '../../store.js'
import { clone, numberOrBlank, same } from '../../forms.js'
import SettingSection from '../../ui/SettingSection.vue'
import ResourceState from '../../ui/ResourceState.vue'
import AdvancedFields from '../../ui/AdvancedFields.vue'

const emit = defineEmits(['dirty'])
const settings = useResource(() => api('/api/host/settings'))
const save = useAction()
const draft = ref(null), parked = ref(null)
const saved = computed(() => settings.data.value?.saved)

const fresh = () => ({
  docker_binary: '', docker_host: '', image: '', workspace_root: '', runtime_root: '',
  delivery_root: '', storage_pool: null, uid: '', gid: '', cpus: 2, memory: '2g', tmpfs_size: '256m', pids_limit: 512,
  command_timeout_seconds: 30, max_running: 4, max_containers: 8, max_scene_containers: 4,
  max_calls: 40, max_request_bytes: 8 * 1024 * 1024, max_response_bytes: 64 * 1024 * 1024, max_cost: null,
  compaction_reserve_tokens: 16384, compaction_keep_recent_tokens: 20000,
  active_timeout_seconds: 1800, input_timeout_seconds: 1800, max_file_bytes: 25 * 1024 * 1024,
  input_support: 'text', model_reasoning: null, skills_directory: null, public_browser: false, mcp: false,
  egress: { enabled: true, max_task_bytes: 524288000, max_scene_daily_bytes: 2147483648, max_connections: 16,
    bytes_per_second: 8388608, connect_timeout_seconds: 30, header_timeout_seconds: 30 },
})
const advanced = [
  ['cpus', 'CPU 核数'], ['memory', '内存上限', '例如 2g'], ['tmpfs_size', '临时文件空间', '例如 256m'], ['pids_limit', '进程数上限'],
  ['command_timeout_seconds', '单条命令超时（秒）'], ['max_running', '同时运行的任务数'],
  ['max_containers', '任务容器总数上限'], ['max_scene_containers', '每个群的任务容器上限'],
  ['max_calls', '每次执行最多调用模型几次'], ['max_request_bytes', '单次模型请求大小上限（字节）'],
  ['max_response_bytes', '单次模型回复大小上限（字节）'], ['active_timeout_seconds', '任务运行超时（秒）'],
  ['input_timeout_seconds', '等待回答追问的时限（秒）'], ['max_file_bytes', '单个交付文件大小上限（字节）'],
  ['compaction_reserve_tokens', '上下文压缩预留 token'], ['compaction_keep_recent_tokens', '压缩时保留的近期 token'],
]
const network = [
  ['max_task_bytes', '每个任务的上网流量上限（字节）'], ['max_scene_daily_bytes', '每个群每天的上网流量上限（字节）'],
  ['max_connections', '每个任务同时打开的连接数'], ['bytes_per_second', '上网速度上限（字节/秒）'],
  ['connect_timeout_seconds', '连接超时（秒）'], ['header_timeout_seconds', '等待响应超时（秒）'],
]
const textKeys = ['memory', 'tmpfs_size']

watch(() => JSON.stringify(saved.value?.worker), () => { if (saved.value) draft.value = clone(saved.value.worker) }, { immediate: true })
const dirty = computed(() => Boolean(saved.value) && !same(draft.value, saved.value.worker))
watch(dirty, value => emit('dirty', value), { immediate: true })

function toggle(on) {
  if (on) draft.value = parked.value || fresh()
  else { parked.value = draft.value; draft.value = null }
}
const problem = computed(() => {
  const value = draft.value
  if (value === null) return ''
  if (['docker_binary', 'docker_host', 'image', 'workspace_root', 'runtime_root', 'delivery_root'].some(key => !String(value[key]).trim())
    || value.uid === '' || value.gid === '') return '运行环境的几项都要填写'
  if (value.model_reasoning === null) return '请选择任务模型是否支持推理'
  if (value.public_browser && !value.egress.enabled) return '用浏览器需要先允许任务上网'
  return ''
})
const workerModel = computed(() => saved.value?.models.roles.worker)

async function submit() {
  const result = await save.run(() => api('/api/host/settings/worker', { method: 'PUT', body: JSON.stringify({ worker: draft.value }) }))
  if (result) {
    settings.data.value = result
    readPendingRestart()
    notify('已保存，重启后生效')
  }
}
</script>

<template>
  <ResourceState :resource="settings" error-title="读取任务环境失败">
  <SettingSection title="独立任务" description="Bot 可以把耗时的活交给独立任务，在隔离的 Docker 容器里慢慢做完再交付。"
    :dirty="dirty" :problem="problem" :saving="save.busy.value" :error="save.error.value" @save="submit">
    <v-switch :model-value="draft !== null" color="primary" label="启用独立任务" hide-details @update:model-value="toggle" />
    <template v-if="draft">
      <v-alert v-if="!workerModel" type="info">
        还没有给任务分配模型，请到 <RouterLink :to="{ name: 'host-models', query: { tab: 'roles' } }">模型</RouterLink> 页设置任务用的模型。</v-alert>
      <div class="form-grid">
        <v-select v-model="draft.model_reasoning" label="任务模型支持推理吗" :items="[{ title: '支持', value: true }, { title: '不支持', value: false }]"
          hint="按模型服务商的说明选择" persistent-hint />
        <v-select v-model="draft.input_support" label="任务模型能看图吗" :items="[{ title: '只看文字', value: 'text' }, { title: '能看图片', value: 'text-image' }]" />
        <v-text-field :model-value="draft.max_cost ?? ''" label="整个任务累计最多花费" inputmode="decimal"
          hint="留空不限制；需要先在模型页给任务模型填价格" persistent-hint
          @update:model-value="value => draft.max_cost = value === '' ? null : value" />
      </div>
      <v-switch v-model="draft.egress.enabled" color="primary" label="任务可以上网" hide-details />
      <v-switch v-model="draft.public_browser" color="primary" label="任务可以用浏览器打开网页" hint="需要先允许任务上网" persistent-hint />
      <v-switch v-model="draft.mcp" color="primary" label="任务可以用本群的 MCP 工具" hide-details />

      <h3>运行环境</h3>
      <div class="form-grid">
        <v-text-field v-model="draft.image" label="任务镜像" />
        <v-text-field v-model="draft.docker_binary" label="Docker 程序路径" hint="完整路径，例如 /usr/local/bin/docker" persistent-hint />
        <v-text-field v-model="draft.docker_host" label="Docker 地址" hint="例如 unix:///var/run/docker.sock" persistent-hint />
        <v-text-field :model-value="draft.uid" type="number" label="容器用户 UID" @update:model-value="value => draft.uid = numberOrBlank(value)" />
        <v-text-field :model-value="draft.gid" type="number" label="容器用户 GID" @update:model-value="value => draft.gid = numberOrBlank(value)" />
        <v-text-field v-model="draft.workspace_root" label="任务工作目录" hint="完整路径，三个目录不能互相包含" persistent-hint />
        <v-text-field v-model="draft.runtime_root" label="任务运行目录" hint="完整路径" persistent-hint />
        <v-text-field v-model="draft.delivery_root" label="任务交付目录" hint="完整路径，任务做好的文件放在这里" persistent-hint />
        <v-text-field :model-value="draft.skills_directory ?? ''" label="技能目录" hint="留空不用技能，例如 data/skills" persistent-hint
          @update:model-value="value => draft.skills_directory = value ? value : null" />
      </div>

      <h3>任务存储池</h3>
      <p class="pool">{{ draft.storage_pool ? `${draft.storage_pool.kind === 'apfs' ? 'APFS 卷配额' : 'ext4 文件系统'} · ${draft.storage_pool.mount}` : '没有设置容量上限' }}。
        实际用量见 <RouterLink :to="{ name: 'host-resources' }">资源页</RouterLink>。</p>

      <AdvancedFields>
        <v-text-field v-for="[key, label, hint] in advanced" :key="key" :model-value="draft[key]" :label="label" :hint="hint" :persistent-hint="Boolean(hint)"
          :type="textKeys.includes(key) ? 'text' : 'number'"
          @update:model-value="value => draft[key] = textKeys.includes(key) ? value : numberOrBlank(value)" />
        <v-text-field v-for="[key, label] in network" :key="key" :model-value="draft.egress[key]" :label="label" type="number"
          @update:model-value="value => draft.egress[key] = numberOrBlank(value)" />
      </AdvancedFields>
    </template>
  </SettingSection>
  </ResourceState>
</template>

<style scoped>
h3{margin-top:var(--sp-2)}
.pool{margin:0}
</style>
