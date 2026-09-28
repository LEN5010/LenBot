<script setup>
import { computed, ref, watch } from 'vue'
import { api, sceneName } from '../api.js'
const props = defineProps({ snapshot: { type: Object, required: true }, disabled: Boolean })
const emit = defineEmits(['saved', 'dirty', 'saving'])
const kind = ref('group'), qq = ref(''), persona = ref(''), userId = ref(''), apiKey = ref('')
const selected = ref(''), replacement = ref(''), busy = ref(false), error = ref(''), notice = ref('')
const rows = computed(() => [...new Set([...Object.keys(props.snapshot.running.scenes), ...Object.keys(props.snapshot.saved.scenes)])])
const options = computed(() => Object.keys(props.snapshot.saved.scenes).map(value => ({ title: sceneName(value), value })))
const remoteMemory = computed(() => props.snapshot.saved.memory?.backend === 'openviking')
const dirty = computed(() => Boolean(qq.value || persona.value || replacement.value || userId.value || apiKey.value))
watch(dirty, value => emit('dirty', value))
async function save(operation) {
  if (props.disabled || busy.value) return
  const otherDraft = operation === 'create' ? Boolean(replacement.value) : Boolean(qq.value || persona.value || userId.value || apiKey.value)
  if (otherDraft && !window.confirm('另一区还有未保存草稿。完成本次操作后放弃那些草稿？')) return
  if (operation === 'delete' && !window.confirm('仅从保存配置移除此场景，重启后生效；当前进程继续处理，历史、记忆、安排和文件不会删除。继续？')) return
  busy.value = true; emit('saving', true); error.value = ''; notice.value = ''
  try {
    let path = '/api/host/settings/scenes', method = 'POST', body
    if (operation === 'create') {
      body = { scene: `${kind.value}:${qq.value}`, persona: persona.value }
      if (remoteMemory.value) body.memory_identity = { user_id: userId.value, api_key: apiKey.value }
    } else {
      path += `/${encodeURIComponent(selected.value)}`
      method = operation === 'delete' ? 'DELETE' : 'PUT'
      if (operation === 'bind') { path += '/persona'; body = { persona: replacement.value } }
    }
    const value = await api(path, { method, ...(body ? { body: JSON.stringify(body) } : {}) })
    qq.value = ''; persona.value = ''; replacement.value = ''; userId.value = ''; apiKey.value = ''; selected.value = ''
    emit('dirty', false); emit('saved', value)
    notice.value = '保存完成。当前运行场景和角色不变，重启后生效；未删除业务数据。'
  } catch (failure) {
    error.value = `${failure.message}；没有自动重试。若响应丢失，请重读配置核对保存结果。`
  } finally { busy.value = false; emit('saving', false) }
}
</script>
<template>
  <section class="surface" aria-labelledby="scene-bindings-title">
    <h2 id="scene-bindings-title">场景与角色绑定</h2>
    <p>编辑唯一根配置，重启后生效。角色路径指向已有角色目录，不创建或改写角色内容。新增场景默认仅直接呼唤回应。</p>
    <p v-if="disabled" role="status">请先保存或放弃其他设置草稿。</p>
    <ul><li v-for="key in rows" :key="key"><strong>{{ sceneName(key) }}</strong>
      <p>保存角色：{{ snapshot.saved.scenes[key]?.persona || '已移除，待重启' }}</p>
      <p>运行角色：{{ snapshot.running.scenes[key]?.persona || '尚未运行，待重启后编辑详细设置' }}</p>
    </li></ul>
    <v-alert v-if="error" type="error" role="alert">{{ error }}</v-alert>
    <v-alert v-if="notice" type="success" role="status">{{ notice }}</v-alert>
    <fieldset :disabled="disabled || busy" style="border:0;padding:0">
      <legend>新增场景</legend>
      <v-select v-model="kind" label="新增场景类型" :items="[{title:'群聊',value:'group'},{title:'私聊',value:'private'}]" />
      <v-text-field v-model="qq" label="新增群号或私聊 QQ" inputmode="numeric" />
      <v-text-field v-model="persona" label="新场景角色目录" hint="项目根相对路径或绝对路径，例如 personas/my-bot" persistent-hint />
      <template v-if="remoteMemory"><v-text-field v-model="userId" label="新场景远端记忆 user_id" /><v-text-field v-model="apiKey" label="新场景远端记忆 API Key" type="password" autocomplete="off" /></template>
      <v-btn :disabled="!qq || !persona || (remoteMemory && (!userId || !apiKey)) || disabled || busy" :loading="busy" @click="save('create')">保存新增场景</v-btn>
      <h3 class="mt-4">更换角色或移除场景</h3>
      <v-select v-model="selected" label="选择保存配置中的场景" :items="options" />
      <v-text-field v-model="replacement" label="改绑到已有角色目录" />
      <v-btn :disabled="!selected || !replacement || disabled || busy" @click="save('bind')">保存角色绑定</v-btn>
      <v-btn class="ml-2" color="warning" variant="outlined" :disabled="!selected || disabled || busy" @click="save('delete')">移除保存场景</v-btn>
    </fieldset>
  </section>
</template>
