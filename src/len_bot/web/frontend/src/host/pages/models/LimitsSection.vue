<script setup>
import { computed, ref, watch } from 'vue'
import { api, sceneName } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { host } from '../../store.js'
import { formatTime } from '../../time.js'
import { clone, numberOrNull, same } from '../../forms.js'
import SettingSection from '../../ui/SettingSection.vue'
import AdvancedFields from '../../ui/AdvancedFields.vue'
import SceneRows from '../../components/SceneRows.vue'
import ErrorNote from '../../ui/ErrorNote.vue'

const props = defineProps({ snapshot: { type: Object, required: true } })
const emit = defineEmits(['saved', 'dirty'])
const pick = value => ({ daily_tokens: value.daily_tokens, scene_daily_tokens: value.scene_daily_tokens })
const saved = computed(() => pick(props.snapshot.saved.limits))
const scenes = computed(() => Object.keys(props.snapshot.saved.scenes))
const status = useResource(() => api('/api/host/limits'))
// Speech limits are shown on each group's settings; only token limits stop a group here.
const blocked = computed(() => (status.data.value?.scenes || []).filter(item => item.blocked && item.speech.blocked_until === null))
const draft = ref(null)
const save = useAction()
watch(() => JSON.stringify(saved.value), () => { draft.value = clone(saved.value) }, { immediate: true })
const dirty = computed(() => !same(draft.value, saved.value))
watch(dirty, value => emit('dirty', value), { immediate: true })

async function submit() {
  const body = { ...props.snapshot.saved.limits, ...draft.value }
  const result = await save.run(() => api('/api/host/settings/limits', { method: 'PUT', body: JSON.stringify(body) }))
  if (result) emit('saved', result)
}
</script>

<template>
    <ErrorNote v-if="status.error.value" title="读取当前额度状态失败" :error="status.error.value" @retry="status.reload()" />
    <v-alert v-for="item in blocked" :key="item.scene" type="warning">
      {{ sceneName(item.scene) }} 已到上限，{{ item.until ? `${formatTime(item.until, host.state?.timezone)} 恢复` : '暂停中' }}：{{ item.reason }}
    </v-alert>
    <SettingSection v-if="draft" title="每天 token 上限"
      :dirty="dirty" :saving="save.busy.value" :error="save.error.value" @save="submit">
      <div class="form-grid">
        <v-text-field :model-value="draft.daily_tokens ?? ''" type="number" label="每天模型 token 上限"
          placeholder="不限"
          @update:model-value="value => draft.daily_tokens = numberOrNull(value)" />
      </div>
      <AdvancedFields label="按群单独设置">
        <SceneRows v-model="draft.scene_daily_tokens" :scenes="scenes" :empty-value="1000000" value-label="token 数" />
      </AdvancedFields>
    </SettingSection>
</template>
