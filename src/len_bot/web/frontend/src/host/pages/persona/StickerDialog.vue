<script setup>
import { computed, ref, watch } from 'vue'
import ErrorNote from '../../ui/ErrorNote.vue'
import FormDialog from '../../ui/FormDialog.vue'

const props = defineProps({ entry: { type: Object, default: null }, image: { type: String, default: null },
  emotions: { type: Array, default: () => [] }, tags: { type: Array, default: () => [] },
  busy: Boolean, error: { type: [Object, String], default: null } })
const open = defineModel({ type: Boolean, required: true })
const emit = defineEmits(['save'])
const file = ref(null), preview = ref(null), draft = ref(null)
watch(open, value => {
  if (!value) return
  file.value = null
  draft.value = { description: props.entry?.description || '', emotions: [...(props.entry?.emotions || [])], tags: [...(props.entry?.tags || [])] }
}, { immediate: true })
watch(file, (value, old) => {
  if (old && preview.value) URL.revokeObjectURL(preview.value)
  preview.value = value ? URL.createObjectURL(value) : null
})
const adding = computed(() => !props.entry)
const ready = computed(() => draft.value?.description.trim() && (!adding.value || file.value))
</script>

<template>
  <FormDialog v-model="open" :title="adding ? '添加表情' : '编辑表情'" :busy="busy">
    <template v-if="draft">
      <img v-if="preview || image" :src="preview || image" alt="表情图片" class="picture" />
      <v-file-input v-if="adding" v-model="file" label="图片" accept="image/png,image/jpeg,image/gif,image/webp"
        prepend-icon="" placeholder="PNG、JPG、GIF 或 WebP，最大 10 MB" />
      <v-textarea v-model="draft.description" label="描述" rows="2" auto-grow placeholder="图上是什么、适合什么时候发" />
      <v-combobox v-model="draft.emotions" :items="emotions" label="情绪（可不填）" multiple chips closable-chips placeholder="开心、无语" />
      <v-combobox v-model="draft.tags" :items="tags" label="标签（可不填）" multiple chips closable-chips />
      <ErrorNote v-if="error" title="没有保存成功" :error="error" />
    </template>
    <template #actions><v-btn color="primary" :loading="busy" :disabled="!ready" @click="emit('save', { file, ...draft })">保存</v-btn></template>
  </FormDialog>
</template>

<style scoped>
.picture{max-width:160px;max-height:160px;object-fit:contain;justify-self:center;border-radius:var(--radius)}
</style>
