<script setup>
import { computed, ref, watch } from 'vue'
import { api, sceneName } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { notify, readPendingRestart } from '../../store.js'
import { clone, same } from '../../forms.js'
import SettingSection from '../../ui/SettingSection.vue'
import ResourceState from '../../ui/ResourceState.vue'
import FormDialog from '../../ui/FormDialog.vue'
import AllowList from '../../components/AllowList.vue'
import DevOnly from '../../ui/DevOnly.vue'
import SkillInspector from '../../components/SkillInspector.vue'

const props = defineProps({ scene: { type: String, required: true } })
const emit = defineEmits(['dirty'])
const skills = useResource(() => api(`/api/host/skills?scene=${encodeURIComponent(props.scene)}`))
const draft = ref(null), inspected = ref(null)
const save = useAction()
watch(() => skills.data.value, value => { if (value) draft.value = clone(value.role_skills.saved) })
const sorted = value => value === 'all' ? value : [...value].sort()
const dirty = computed(() => Boolean(skills.data.value) && !same(sorted(draft.value), sorted(skills.data.value.role_skills.saved)))
watch(dirty, value => emit('dirty', value), { immediate: true })

const sources = { builtin: '内置', plugin: '插件附带', shared: '共享', scene: '本群' }
const items = computed(() => (skills.data.value?.catalog || []).map(skill => ({
  name: skill.name, label: `${skill.name} · ${sources[skill.source]}`, note: skill.description, skill,
})))
const shared = computed(() => (skills.data.value?.role_skills.affected_scenes || []).filter(item => item !== props.scene))
const inspecting = computed({ get: () => inspected.value !== null, set: value => { if (!value) inspected.value = null } })

async function submit() {
  const result = await save.run(() => api(`/api/host/scenes/${encodeURIComponent(props.scene)}/role-skills`, {
    method: 'PUT', body: JSON.stringify({ directory: skills.data.value.role_skills.directory, skills: draft.value }),
  }))
  if (result) {
    skills.data.value = { ...skills.data.value, role_skills: result }
    readPendingRestart()
    notify('已保存')
  }
}
function changed() {
  inspected.value = null
  skills.reload()
}
</script>

<template>
  <ResourceState :resource="skills" error-title="读取技能失败">
  <SettingSection v-if="draft !== null" title="技能"
    :description="shared.length ? `这个角色也用在 ${shared.map(sceneName).join('、')}，修改会一起生效。` : ''"
    :dirty="dirty" :saving="save.busy.value" :error="save.error.value" @save="submit">
    <p v-if="skills.data.value.directory === null && !items.length" class="muted">还没有可用技能，可启用附带技能的插件或在任务环境设置技能目录。</p>
    <template v-else>
      <AllowList v-model="draft" :items="items" all-label="全部技能">
        <template #item="{ item }">
          <v-btn size="small" variant="text" class="skill-open" @click="inspected = item.skill">查看文件</v-btn>
        </template>
      </AllowList>
      <p v-if="!items.length" class="muted">技能目录里还没有技能。</p>
    </template>
    <DevOnly label="技能目录与运行中的技能"
      :json="{ directory: skills.data.value.directory, running_directory: skills.data.value.running_directory, running: skills.data.value.running }" />
  </SettingSection>
  </ResourceState>
  <FormDialog v-model="inspecting" :title="inspected?.name || ''" size="lg" cancel-label="关闭">
    <SkillInspector v-if="inspected" :key="`${inspected.source}:${inspected.name}`" :scene="scene" :source="inspected.source" :name="inspected.name" @changed="changed" />
  </FormDialog>
</template>

<style scoped>
.skill-open{margin-left:var(--sp-6)}
</style>
