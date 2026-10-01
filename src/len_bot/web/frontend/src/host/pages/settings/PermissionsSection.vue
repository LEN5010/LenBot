<script setup>
import { computed, ref, watch } from 'vue'
import { api, sceneName } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { host } from '../../store.js'
import { roleOptions } from '../../labels.js'
import { clone, same } from '../../forms.js'
import SettingSection from '../../components/SettingSection.vue'
import AdvancedFields from '../../components/AdvancedFields.vue'
import ErrorNote from '../../components/ErrorNote.vue'

const props = defineProps({ scene: { type: String, default: '' } })
const emit = defineEmits(['saved', 'dirty', 'scene'])
const lists = [['admins', '管理员'], ['whitelist', '白名单'], ['blacklist', '黑名单']]
const listHints = { admins: '可以使用管理类功能，具体见下方', whitelist: '比普通群友多一些权限，具体见下方', blacklist: '这些人的消息会保存，但不会叫醒 Bot' }
const abilities = [
  ['delegate', '委托 Bot 做任务'], ['long_running', '让任务执行超过 30 分钟'], ['task_manage', '管理别人的任务'],
  ['own_reminder', '给自己定提醒'], ['other_reminder', '替别人定提醒'], ['reminder_manage', '管理别人的提醒'],
  ['chat_control', '在群里让 Bot 暂时安静'],
]
const scenes = computed(() => (host.state?.scenes || []).map(item => ({ title: sceneName(item.scene), value: item.scene })))
const current = computed(() => props.scene || scenes.value[0]?.value || '')
const permissions = useResource(scene => api(`/api/host/permissions?${new URLSearchParams({ scene })}`), { immediate: false })
const draft = ref(null)
const save = useAction()
watch(current, scene => { if (scene) permissions.reload(scene) }, { immediate: true })
watch(permissions.data, value => { if (value) draft.value = clone(value.saved) })
const dirty = computed(() => Boolean(draft.value && permissions.data.value && !same(draft.value, permissions.data.value.saved)))
watch(dirty, value => emit('dirty', value), { immediate: true })

function changeScene(scene) {
  if (dirty.value && !window.confirm('权限有未保存的修改，切换群会丢掉这些修改。继续？')) return
  emit('scene', scene)
}
async function submit() {
  const scene = current.value
  const result = await save.run(() => api(`/api/host/permissions?${new URLSearchParams({ scene })}`, {
    method: 'PUT', body: JSON.stringify(draft.value),
  }))
  if (result) {
    permissions.data.value = result
    emit('saved')
  }
}
</script>

<template>
  <div class="page-stack">
    <ErrorNote v-if="permissions.error.value" title="读取权限失败" :error="permissions.error.value" />
    <SettingSection v-if="draft" title="权限" :dirty="dirty" :saving="save.busy.value || permissions.loading.value"
      :error="save.error.value" @save="submit">
      <v-select :model-value="current" :items="scenes" label="群聊" hint="本群名单和下方的权限只对这个群生效" persistent-hint
        @update:model-value="changeScene" />
      <p>主人：<strong>{{ permissions.data.value.owner_qq || '未设置' }}</strong>，在连接设置里修改。主人拥有全部权限。</p>
      <h3>所有群通用的名单</h3>
      <v-combobox v-for="[field, label] in lists" :key="field" v-model="draft.global_identities[field]" :label="`${label} QQ`"
        :hint="listHints[field]" persistent-hint multiple chips closable-chips />
      <v-switch :model-value="draft.scene_identities !== null" label="本群另外加名单"
        @update:model-value="value => draft.scene_identities = value ? { admins: [], whitelist: [], blacklist: [] } : null" />
      <template v-if="draft.scene_identities">
        <v-combobox v-for="[field, label] in lists" :key="field" v-model="draft.scene_identities[field]" :label="`本群${label} QQ`"
          multiple chips closable-chips />
      </template>
      <h3>本群谁可以做什么</h3>
      <div class="form-grid">
        <v-select v-for="[field, label] in abilities" :key="field" v-model="draft.matrix[field]" :label="label"
          :items="roleOptions" multiple chips closable-chips />
      </div>
      <AdvancedFields label="只对任务或提醒生效的名单">
        <template v-for="[key, label] in [['task_identities', '任务'], ['schedule_identities', '提醒']]" :key="key">
          <v-text-field :model-value="draft[key].owner ?? ''" :label="`${label}主人 QQ`" inputmode="numeric"
            :hint="`在${label}权限里按主人对待，其他功能不受影响`" persistent-hint
            @update:model-value="value => draft[key].owner = value.trim() || null" />
          <v-combobox v-model="draft[key].admins" :label="`${label}管理员 QQ`" multiple chips closable-chips />
          <v-combobox v-model="draft[key].whitelist" :label="`${label}白名单 QQ`" multiple chips closable-chips />
        </template>
      </AdvancedFields>
    </SettingSection>
  </div>
</template>

<style scoped>
h3{font-size:15px;margin:8px 0 0}
</style>
