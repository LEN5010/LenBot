<script setup>
// Files under legacy-import/ came from the old core and are not recalled until a person rewrites them as a real memory.
import { ref } from 'vue'
import { api } from '../../../api.js'
import { useAction } from '../../../composables/useResource.js'
import { notify } from '../../store.js'
import ErrorNote from '../../components/ErrorNote.vue'

const props = defineProps({
  scene: { type: String, required: true }, source: { type: String, required: true },
  original: { type: String, required: true }, personaIds: { type: Array, required: true },
})
const emit = defineEmits(['adopted'])
const open = ref(false), target = ref(''), content = ref(''), reason = ref('从旧版导入的资料整理'), removeSource = ref(true)
const adopt = useAction()
async function submit() {
  const result = await adopt.run(() => api('/api/host/memory/adopt-pending', { method: 'POST', body: JSON.stringify({
    scene: props.scene, source: props.source, original: props.original, target: target.value.trim(),
    content: content.value, reason: reason.value, remove_source: removeSource.value }) }))
  if (!result) return
  open.value = false
  notify(`已保存为 ${result.target}`)
  emit('adopted', result)
}
</script>

<template>
  <v-alert type="info" variant="tonal">
    这是从旧版导入的资料，Bot 还不会用到。整理成正式记忆后才会生效。
    <template #append><v-btn size="small" variant="outlined" @click="open = true">整理成正式记忆</v-btn></template>
  </v-alert>
  <v-dialog v-model="open" max-width="720" scrollable>
    <v-card title="整理成正式记忆">
      <v-card-text class="adopt">
        <details><summary>查看原资料</summary><pre>{{ original }}</pre></details>
        <v-text-field v-model="target" label="保存为" hint="新文件路径，例如 people/小明.md" persistent-hint />
        <p v-if="personaIds.length" class="muted">关于 Bot 自己的资料放在 bot/{{ personaIds[0] }}/ 下。</p>
        <v-textarea v-model="content" label="整理后的内容" rows="8" auto-grow hint="只写核对过、确实要记住的内容" persistent-hint />
        <v-text-field v-model="reason" label="原因" />
        <v-checkbox v-model="removeSource" label="保存后删除这份旧资料" hide-details />
        <ErrorNote v-if="adopt.error.value" title="没有保存成功" :error="adopt.error.value" />
      </v-card-text>
      <v-card-actions><v-spacer /><v-btn @click="open = false">取消</v-btn>
        <v-btn color="primary" :loading="adopt.busy.value" :disabled="!target.trim().endsWith('.md') || !content.trim() || !reason.trim()" @click="submit">保存</v-btn></v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.adopt{display:grid;gap:12px}
.adopt pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}
.adopt summary{cursor:pointer}
</style>
