<script setup>
import { computed, ref, watch } from 'vue'
import { resourceLabel } from '../../resourceLabels.js'
import NewTask from './NewTask.vue'

defineProps({ scene: { type: String, required: true }, operator: { type: String, required: true } })
const emit = defineEmits(['dirty', 'created'])
const selections = ref([]), creating = ref(false), formDirty = ref(false)
const dirty = computed(() => selections.value.length > 0 || formDirty.value)
watch(dirty, value => emit('dirty', value), { immediate: true })
function select(entry) {
  const ref = entry.reference
  if (selections.value.some(item => item.reference.scope === ref.scope && item.reference.task_id === ref.task_id
    && item.reference.path === ref.path && item.reference.file_id === ref.file_id)) return
  selections.value.push({ reference: { ...ref }, name: entry.name })
}
function created(task) {
  selections.value = []; formDirty.value = false; creating.value = false
  emit('dirty', false)
  emit('created', task)
}
</script>

<template>
  <slot :select="select" />
  <section v-if="selections.length" class="surface selected-inputs">
    <div class="heading"><h3>下一项任务的资料 · {{ selections.length }}</h3>
      <v-btn color="primary" variant="tonal" @click="creating = true">带这些资料新建任务</v-btn></div>
    <ul><li v-for="(item, index) in selections" :key="index">
      <div><strong>{{ item.name }}</strong><small>{{ resourceLabel(item.reference) }}</small></div>
      <v-btn size="small" variant="text" @click="selections.splice(index, 1)">移除</v-btn>
    </li></ul>
    <p class="muted">可以继续选择本场景的其他文件，在新任务表单中修改输入文件名。</p>
  </section>
  <v-dialog v-model="creating" max-width="720" scrollable persistent>
    <NewTask v-if="creating" :scene="scene" :operator="operator" :initial-resources="selections"
      @dirty="value => formDirty = value" @created="created" @close="creating = false; formDirty = false" />
  </v-dialog>
</template>

<style scoped>
.selected-inputs{display:grid;gap:10px}.heading,li{display:flex;justify-content:space-between;align-items:center;gap:12px}.heading{flex-wrap:wrap}ul{list-style:none;margin:0;padding:0}li{padding:6px 0}li div{min-width:0;overflow-wrap:anywhere}small{display:block;color:var(--muted)}p{margin:0}
</style>
