<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../api.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.js'

const saved = ref(null)
const restartRequired = ref(false)
const draft = ref({ persona_aliases: [], relationships: [], behavior_addendum: '' })
const reading = ref(false)
const saving = ref(false)
const readError = ref('')
const saveError = ref('')
const localError = ref('')
const saveNotice = ref('')
const beginRead = useRequestGuard()
const beginSave = useRequestGuard()

const dirty = computed(() => saved.value !== null && (
  JSON.stringify(draft.value.persona_aliases) !== JSON.stringify(saved.value.persona_aliases) ||
  JSON.stringify(draft.value.relationships.map(row => [row.qq, row.description])) !==
    JSON.stringify(Object.entries(saved.value.relationships)) ||
  (draft.value.behavior_addendum === '' ? null : draft.value.behavior_addendum) !== saved.value.behavior_addendum
))
useUnsavedChanges(dirty)
watch(draft, () => { localError.value = '' }, { deep: true, flush: 'sync' })

function adopt(result) {
  saved.value = result.saved
  restartRequired.value = result.restart_required
  draft.value = {
    persona_aliases: [...result.saved.persona_aliases],
    relationships: Object.entries(result.saved.relationships).map(([qq, description]) => ({ qq, description })),
    behavior_addendum: result.saved.behavior_addendum ?? '',
  }
  localError.value = ''
}

async function readSaved(confirmDiscard = true) {
  if (reading.value || saving.value) return
  if (confirmDiscard && dirty.value && !window.confirm('放弃尚未保存的场景补充草稿，重新读取根配置中的保存值？')) return
  const fresh = beginRead()
  reading.value = true
  readError.value = ''
  try {
    const result = await api('/api/chat-test/scene-persona')
    if (!fresh()) return
    adopt(result)
    saveError.value = ''
    saveNotice.value = ''
  } catch (problem) {
    if (fresh()) readError.value = problem.message
  } finally {
    if (fresh()) reading.value = false
  }
}

function draftError() {
  for (const [index, alias] of draft.value.persona_aliases.entries()) {
    if (!alias.trim()) return `补充称呼第 ${index + 1} 项不能为空；若要清除，请删除该项。`
  }
  const seen = new Set()
  for (const [index, row] of draft.value.relationships.entries()) {
    if (!/^[1-9][0-9]*$/.test(row.qq)) return `关系第 ${index + 1} 行的 QQ 须为正十进制文本。`
    if (seen.has(row.qq)) return `关系中 QQ ${row.qq} 重复；请合并或删除重复行。`
    seen.add(row.qq)
    if (!row.description.trim()) return `关系第 ${index + 1} 行说明不能为空；若要清除，请删除该行。`
  }
  const addendum = draft.value.behavior_addendum
  if (addendum !== '' && !addendum.trim()) return '行为风格补充不能只有空白；清空输入框可提交 null。'
  return ''
}

async function save() {
  if (!saved.value || !dirty.value || reading.value || saving.value) return
  saveError.value = ''
  localError.value = draftError()
  if (localError.value) return
  const body = {
    persona_aliases: [...draft.value.persona_aliases],
    relationships: Object.fromEntries(draft.value.relationships.map(row => [row.qq, row.description])),
    behavior_addendum: draft.value.behavior_addendum === '' ? null : draft.value.behavior_addendum,
  }
  const fresh = beginSave()
  saving.value = true
  saveError.value = ''
  saveNotice.value = ''
  try {
    const result = await api('/api/chat-test/scene-persona', { method: 'PUT', body: JSON.stringify(body) })
    if (!fresh()) return
    adopt(result)
    saveNotice.value = result.restart_required
      ? '已保存，重启后生效。'
      : '已保存。'
  } catch (problem) {
    if (fresh()) saveError.value = problem.status >= 400 && problem.status < 500
      ? `未保存：${problem.message}`
      : `不确定有没有保存成功：${problem.message} 你的修改还在，刷新看看。`
  } finally {
    if (fresh()) saving.value = false
  }
}

onMounted(() => readSaved(false))
</script>

<template>
  <section class="surface scene-persona-editor" aria-labelledby="scene-editor-title">
    <div class="section-heading">
      <div>
        <h2 id="scene-editor-title">编辑本场景补充</h2>
        <p class="muted">这里只改这个场景的称呼、关系和行为补充。</p>
      </div>
      <v-btn variant="outlined" :loading="reading" :disabled="saving" @click="readSaved(true)">重读根配置保存值</v-btn>
    </div>

    <v-alert v-if="readError" type="error" variant="tonal" role="alert" title="读取根配置失败">
      {{ readError }}<span v-if="saved"> 当前草稿与上次读取的保存值仍保留。</span>
    </v-alert>
    <p v-if="reading && !saved" role="status">正在读取根配置中的本场景补充…</p>

    <template v-if="saved">
      <v-alert :type="restartRequired ? 'warning' : 'info'" variant="tonal" class="status-note">
        <strong>{{ restartRequired ? '有修改等待重启' : '已经生效' }}</strong>
        <span v-if="restartRequired">：重启后生效。</span>
        <span v-else></span>
      </v-alert>
      <p v-if="dirty" class="dirty-note" role="status">草稿有未保存的修改；离开本页会提示确认。</p>

      <form @submit.prevent="save" novalidate>
        <fieldset :disabled="saving || reading" class="editor-group">
          <legend>补充称呼</legend>
          <p class="muted">每项是一个称呼。</p>
          <div v-for="(alias, index) in draft.persona_aliases" :key="index" class="editor-row">
            <v-textarea v-model="draft.persona_aliases[index]" :label="`补充称呼 ${index + 1}`" rows="2" auto-grow hide-details="auto" />
            <v-btn variant="outlined" :aria-label="`删除补充称呼 ${index + 1}`" @click="draft.persona_aliases.splice(index, 1)">删除</v-btn>
          </div>
          <p v-if="!draft.persona_aliases.length" class="muted">尚无补充称呼。</p>
          <v-btn variant="outlined" @click="draft.persona_aliases.push('')">添加称呼</v-btn>
        </fieldset>

        <fieldset :disabled="saving || reading" class="editor-group">
          <legend>本场景关系说明</legend>
          <p class="muted">每行是一位实际 QQ 与一段完整说明；相同 QQ 不能出现两次。要清空关系，请删除对应行。</p>
          <div v-for="(row, index) in draft.relationships" :key="index" class="editor-row relationship-row">
            <v-text-field v-model="row.qq" :label="`关系 ${index + 1} · QQ`" inputmode="numeric" hide-details="auto" />
            <v-textarea v-model="row.description" :label="`关系 ${index + 1} · 说明`" rows="2" auto-grow hide-details="auto" />
            <v-btn variant="outlined" :aria-label="`删除关系 ${index + 1}`" @click="draft.relationships.splice(index, 1)">删除</v-btn>
          </div>
          <p v-if="!draft.relationships.length" class="muted">尚无关系说明。</p>
          <v-btn variant="outlined" @click="draft.relationships.push({qq:'',description:''})">添加关系</v-btn>
        </fieldset>

        <fieldset :disabled="saving || reading" class="editor-group">
          <legend>行为风格补充</legend>
          <p class="muted">这个场景里的额外行为要求，清空就是不补充。</p>
          <v-textarea v-model="draft.behavior_addendum" label="本场景行为风格补充" rows="5" auto-grow hide-details="auto" />
        </fieldset>

        <v-alert v-if="localError" type="warning" variant="tonal" role="alert">{{ localError }}</v-alert>
        <v-alert v-if="saveError" type="error" variant="tonal" role="alert">{{ saveError }}</v-alert>
        <v-alert v-if="saveNotice && !dirty" type="success" variant="tonal" role="status">{{ saveNotice }}</v-alert>
        <div class="editor-actions">
          <v-btn type="submit" color="primary" :loading="saving" :disabled="!dirty || reading">保存到根配置</v-btn>
          <span class="muted">保存不是热加载；本页不提供重启或真实发送操作。</span>
        </div>
      </form>
    </template>
  </section>
</template>

<style scoped>
.scene-persona-editor{min-width:0}
.section-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap}
.section-heading>div{min-width:0;flex:1 1 390px}
.section-heading h2{margin:0 0 6px}
.section-heading p{margin:0}
.status-note{margin-top:18px}
.dirty-note{border-left:3px solid var(--primary);background:var(--selected-bg);padding:8px 12px;margin-top:16px}
.editor-group{min-width:0;border:0;border-top:1px solid var(--line);padding:18px 0 0;margin:22px 0 0}
.editor-group legend{padding:0 8px 0 0;font-weight:650;font-size:16px}
.editor-group>p{margin:0 0 14px}
.editor-group>.v-btn{margin-top:8px}
.editor-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;align-items:start;min-width:0;margin:12px 0}
.relationship-row{grid-template-columns:minmax(120px,180px) minmax(0,1fr) auto}
.editor-row>*{min-width:0}
.editor-row>.v-btn{margin-top:6px}
.editor-actions{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-top:22px}
.editor-actions>.v-btn{min-width:160px}
.scene-persona-editor :deep(.v-btn){min-height:44px}
.scene-persona-editor :deep(.v-alert),.scene-persona-editor .muted{overflow-wrap:anywhere}
@media(max-width:600px){.section-heading>.v-btn{width:100%}.editor-row,.relationship-row{grid-template-columns:minmax(0,1fr)}.editor-row>.v-btn{justify-self:start;margin-top:0}.editor-actions>.v-btn{width:100%}}
</style>
