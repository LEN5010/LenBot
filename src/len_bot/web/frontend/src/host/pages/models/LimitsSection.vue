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
const saved = computed(() => props.snapshot.saved.limits)
const scenes = computed(() => Object.keys(props.snapshot.saved.scenes))
const status = useResource(() => api('/api/host/limits'))
const blocked = computed(() => (status.data.value?.scenes || []).filter(item => item.blocked))
const draft = ref(null)
const save = useAction()
watch(() => JSON.stringify(saved.value), () => { draft.value = clone(saved.value) }, { immediate: true })
const dirty = computed(() => !same(draft.value, saved.value))
watch(dirty, value => emit('dirty', value), { immediate: true })

async function submit() {
  const result = await save.run(() => api('/api/host/settings/limits', { method: 'PUT', body: JSON.stringify(draft.value) }))
  if (result) emit('saved', result)
}
</script>

<template>
    <ErrorNote v-if="status.error.value" title="读取当前额度状态失败" :error="status.error.value" @retry="status.reload()" />
    <v-alert v-for="item in blocked" :key="item.scene" type="warning">
      {{ sceneName(item.scene) }} 已到上限，{{ item.until ? `${formatTime(item.until, host.state?.timezone)} 恢复` : '暂停中' }}：{{ item.reason }}
    </v-alert>
    <SettingSection v-if="draft" title="花费与发言上限" description="到上限后 Bot 暂停调用模型或暂停发言，到时间自动恢复。"
      :dirty="dirty" :saving="save.busy.value" :error="save.error.value" @save="submit">
      <div class="form-grid">
        <v-text-field :model-value="draft.daily_model_cost ?? ''" :label="`每天模型花费上限（${draft.currency}）`"
          hint="所有群加起来；留空不限，填 0 暂停所有模型调用" persistent-hint
          @update:model-value="value => draft.daily_model_cost = value === '' ? null : value" />
        <v-text-field :model-value="draft.messages_per_hour ?? ''" type="number" label="每个群每小时最多发言条数"
          hint="留空不限" persistent-hint @update:model-value="value => draft.messages_per_hour = numberOrNull(value)" />
      </div>
      <AdvancedFields label="按群单独设置">
        <div>
          <h3>每天模型花费上限</h3>
          <SceneRows v-model="draft.scene_daily_model_cost" :scenes="scenes" type="text" empty-value="1.00" :value-label="`金额（${draft.currency}）`" />
        </div>
        <div>
          <h3>每小时最多发言条数</h3>
          <SceneRows v-model="draft.scene_messages_per_hour" :scenes="scenes" value-label="条数（留空不限）" />
        </div>
        <v-text-field v-model="draft.currency" label="计价币种" hint="三位大写代码，例如 USD、CNY；需要和模型价格的币种一致" persistent-hint />
      </AdvancedFields>
    </SettingSection>
</template>

<style scoped>
h3{margin-bottom:var(--sp-2)}
</style>
