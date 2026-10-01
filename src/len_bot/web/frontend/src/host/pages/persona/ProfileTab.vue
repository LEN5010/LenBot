<script setup>
import { computed, ref, watch } from 'vue'
import { api, sceneName } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { clone, same } from '../../forms.js'
import { notify, readPendingRestart } from '../../store.js'
import ErrorNote from '../../components/ErrorNote.vue'
import SaveBar from '../../components/SaveBar.vue'
import AvatarCard from './AvatarCard.vue'
import StylesEditor from './StylesEditor.vue'
import ExamplesEditor from './ExamplesEditor.vue'
import DraftTrial from './DraftTrial.vue'

const props = defineProps({ scene: { type: String, required: true } })
const emit = defineEmits(['dirty'])
const base = computed(() => `/api/host/scenes/${encodeURIComponent(props.scene)}/persona-profile`)
const saved = useResource(() => api(base.value))
const draft = ref(null)
watch(saved.data, value => { draft.value = value ? clone(value.profile) : null })
const dirty = computed(() => Boolean(draft.value && saved.data.value && !same(draft.value, saved.data.value.profile)))
watch(dirty, value => emit('dirty', value), { immediate: true })
const shared = computed(() => (saved.data.value?.affected_scenes || []).filter(item => item !== props.scene))

const problem = computed(() => {
  const value = draft.value
  if (!value) return ''
  if (!value.name.trim()) return '名字不能为空'
  if (value.styles.some(style => !style.name.trim())) return '每种风格都要有名字'
  if (value.styles.length) {
    const total = value.styles.reduce((sum, style) => sum + style.weight, 0)
    if (Math.abs(total - 1) > 1e-9) return `风格比例加起来是 ${Math.round(total * 10000) / 100}%，要正好 100%`
  }
  if (value.examples.some(example => !example.context.trim() || !example.line.trim())) return '每条样例都要写场合和它说的话'
  return ''
})

const save = useAction()
async function submit() {
  const result = await save.run(() => api(base.value, { method: 'PUT',
    body: JSON.stringify({ directory: saved.data.value.directory, profile: draft.value }) }))
  if (!result) return
  saved.data.value = result
  readPendingRestart()
  notify('已保存，重启后生效')
}
const discard = () => { draft.value = clone(saved.data.value.profile) }
const draftFiles = () => api(`${base.value}/draft`, { method: 'POST',
  body: JSON.stringify({ directory: saved.data.value.directory, profile: draft.value }) })
</script>

<template>
  <ErrorNote v-if="saved.error.value" title="读取角色失败" :error="saved.error.value" />
  <template v-if="draft">
    <p v-if="shared.length" class="muted">这个角色也用在 {{ shared.map(sceneName).join('、') }}，改动会一起生效。</p>
    <form class="profile" @submit.prevent="submit">
      <section class="surface">
        <h2>它是谁</h2>
        <div class="who">
          <AvatarCard :scene="scene" :directory="saved.data.value.directory" :name="draft.name" />
          <div class="fields">
            <v-text-field v-model="draft.name" label="名字" />
            <div class="form-grid">
              <v-combobox v-model="draft.aliases" label="别名" multiple chips closable-chips
                hint="群友这样叫时，它知道是在叫自己" persistent-hint />
              <v-combobox v-model="draft.self_reference" label="自称" multiple chips closable-chips
                hint="它说话时怎么称呼自己，比如 我、本喵" persistent-hint />
            </div>
          </div>
        </div>
        <v-textarea v-model="draft.brief" label="简介" rows="3" auto-grow hint="身份、背景和性格" persistent-hint />
        <v-textarea v-model="draft.behavior" label="做事方式" rows="3" auto-grow hint="它在群里什么时候开口、怎么回应别人" persistent-hint />
        <v-textarea v-model="draft.boundaries" label="底线" rows="3" auto-grow hint="它不会做、不会说的事" persistent-hint />
      </section>
      <section class="surface">
        <h2>怎么说话</h2>
        <v-textarea v-model="draft.voice" label="说话方式" rows="4" auto-grow hint="语气、句子长短、口头禅、用不用表情符号" persistent-hint />
        <StylesEditor v-model="draft.styles" />
      </section>
      <ExamplesEditor v-model:examples="draft.examples" v-model:tags="draft.example_tags" />
      <SaveBar :dirty="dirty" :saving="save.busy.value" :error="save.error.value" :problem="problem" @discard="discard" />
    </form>
    <DraftTrial :scene="scene" :disabled="Boolean(problem)" :draft="draftFiles" />
  </template>
</template>

<style scoped>
.profile{display:grid;gap:20px}
.profile .surface{display:grid;gap:16px}
.who{display:grid;grid-template-columns:auto minmax(0,1fr);gap:20px;align-items:start}
.fields{display:grid;gap:16px}
@media(max-width:600px){.who{grid-template-columns:1fr}}
</style>
