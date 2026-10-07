<script setup>
import { ref } from 'vue'
import { api } from '../../../api.js'
import { useAction } from '../../../composables/useResource.js'
import { notify } from '../../store.js'
import ErrorNote from '../../ui/ErrorNote.vue'
import FormDialog from '../../ui/FormDialog.vue'
import Fold from '../../ui/Fold.vue'

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
  <v-alert type="info">
    这是从旧版导入的资料，Bot 还不会用到。整理成正式记忆后才会生效。
    <template #append><v-btn size="small" variant="outlined" @click="open = true">整理成正式记忆</v-btn></template>
  </v-alert>
  <FormDialog v-model="open" title="整理成正式记忆" size="md" :busy="adopt.busy.value">
    <Fold label="查看原资料" code>{{ original }}</Fold>
    <v-text-field v-model="target" label="保存为" placeholder="people/小明.md" />
    <v-textarea v-model="content" label="整理后的内容" rows="8" auto-grow />
    <v-text-field v-model="reason" label="原因" />
    <v-checkbox v-model="removeSource" label="保存后删除这份旧资料" />
    <ErrorNote v-if="adopt.error.value" title="没有保存成功" :error="adopt.error.value" />
    <template #actions>
      <v-btn color="primary" :loading="adopt.busy.value" :disabled="!target.trim().endsWith('.md') || !content.trim() || !reason.trim()" @click="submit">保存</v-btn>
    </template>
  </FormDialog>
</template>
