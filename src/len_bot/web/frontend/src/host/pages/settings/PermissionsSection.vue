<script setup>
import { computed, ref, watch } from 'vue'
import { api, sceneName } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { roleOptions } from '../../labels.js'
import { clone, same } from '../../forms.js'
import SettingSection from '../../ui/SettingSection.vue'
import AdvancedFields from '../../ui/AdvancedFields.vue'
import ResourceState from '../../ui/ResourceState.vue'

const props = defineProps({ scene: { type: String, required: true } })
const emit = defineEmits(['saved', 'dirty'])
const lists = [['admins', '管理员'], ['whitelist', '白名单'], ['blacklist', '黑名单']]
const listHints = { admins: '可以使用管理类功能，具体见下方', whitelist: '比普通群友多一些权限，具体见下方', blacklist: '这些人的消息会保存，但不会叫醒 Bot' }
const abilities = [
  ['delegate', '委托 Bot 做任务'], ['long_running', '让任务执行超过 30 分钟'], ['task_manage', '管理别人的任务'],
  ['own_reminder', '给自己定提醒'], ['other_reminder', '替别人定提醒'], ['reminder_manage', '管理别人的提醒'],
  ['chat_control', '在群里让 Bot 暂时安静'],
]
const permissions = useResource(() => api(`/api/host/permissions?${new URLSearchParams({ scene: props.scene })}`))
const draft = ref(null)
const save = useAction()
watch(permissions.data, value => { if (value) draft.value = clone(value.saved) })
const dirty = computed(() => Boolean(draft.value && permissions.data.value && !same(draft.value, permissions.data.value.saved)))
watch(dirty, value => emit('dirty', value), { immediate: true })

async function submit() {
  const result = await save.run(() => api(`/api/host/permissions?${new URLSearchParams({ scene: props.scene })}`, {
    method: 'PUT', body: JSON.stringify(draft.value),
  }))
  if (result) {
    permissions.data.value = result
    emit('saved')
  }
}
</script>

<template>
  <ResourceState :resource="permissions" error-title="读取权限失败">
    <SettingSection v-if="draft" :title="`权限 · ${sceneName(scene)}`" description="本群名单和谁可以做什么只对这个群生效，在顶栏切换群。"
      :dirty="dirty" :saving="save.busy.value" :error="save.error.value" @save="submit">
      <p>主人：<strong>{{ permissions.data.value.owners.length ? permissions.data.value.owners.join('、') : '未设置' }}</strong>，在连接设置里修改。主人拥有全部权限。</p>
      <h3>所有群通用的名单</h3>
      <v-combobox v-for="[field, label] in lists" :key="field" v-model="draft.global_identities[field]" :label="`${label}账号`"
        :hint="listHints[field]" persistent-hint multiple chips closable-chips />
      <v-switch :model-value="draft.scene_identities !== null" label="本群另外加名单"
        @update:model-value="value => draft.scene_identities = value ? { admins: [], whitelist: [], blacklist: [] } : null" />
      <template v-if="draft.scene_identities">
        <v-combobox v-for="[field, label] in lists" :key="field" v-model="draft.scene_identities[field]" :label="`本群${label}账号`"
          multiple chips closable-chips />
      </template>
      <h3>本群谁可以做什么</h3>
      <div class="form-grid">
        <v-select v-for="[field, label] in abilities" :key="field" v-model="draft.matrix[field]" :label="label"
          :items="roleOptions" multiple chips closable-chips />
      </div>
      <AdvancedFields label="只对任务或提醒生效的名单">
        <template v-for="[key, label] in [['task_identities', '任务'], ['schedule_identities', '提醒']]" :key="key">
          <v-text-field :model-value="draft[key].owner ?? ''" :label="`${label}主人账号`"
            :hint="`在${label}权限里按主人对待，其他功能不受影响`" persistent-hint
            @update:model-value="value => draft[key].owner = value.trim() || null" />
          <v-combobox v-model="draft[key].admins" :label="`${label}管理员账号`" multiple chips closable-chips />
          <v-combobox v-model="draft[key].whitelist" :label="`${label}白名单账号`" multiple chips closable-chips />
        </template>
      </AdvancedFields>
    </SettingSection>
  </ResourceState>
</template>

<style scoped>
p{margin:0}
h3{margin-top:var(--sp-2)}
</style>
