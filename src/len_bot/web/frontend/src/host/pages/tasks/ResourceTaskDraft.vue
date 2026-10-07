<script setup>
import { computed, ref, watch } from 'vue'
import { resourceLabel } from '../../resourceLabels.js'
import NewTask from './NewTask.vue'
import Panel from '../../ui/Panel.vue'
import ObjectList from '../../ui/ObjectList.vue'
import ObjectRow from '../../ui/ObjectRow.vue'

defineProps({ scene: { type: String, required: true } })
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
  <Panel v-if="selections.length" :title="`下一个任务的资料 · ${selections.length}`">
    <template #actions><v-btn variant="outlined" @click="creating = true">带这些资料新建任务</v-btn></template>
    <ObjectList divided>
      <ObjectRow v-for="(item, index) in selections" :key="index" :title="item.name" :subtitle="resourceLabel(item.reference)">
        <template #actions><v-btn size="small" variant="text" @click="selections.splice(index, 1)">移除</v-btn></template>
      </ObjectRow>
    </ObjectList>
  </Panel>
  <NewTask v-if="creating" :scene="scene" :initial-resources="selections"
    @dirty="value => formDirty = value" @created="created" @close="creating = false; formDirty = false" />
</template>
