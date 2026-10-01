<script setup>
import { ref } from 'vue'
import { api } from '../api.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'

const props = defineProps({
  scene: { type: String, required: true },
  modelValue: { type: Array, required: true },
  configured: { type: Boolean, required: true },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue'])
const snapshot = ref(null), loading = ref(false), readError = ref('')
const beginRead = useRequestGuard(() => `${props.scene}\u0000${props.configured}`)
async function read() {
  if (loading.value || props.disabled || !props.configured) return
  const fresh = beginRead()
  loading.value = true
  try {
    const value = await api(`/api/host/materials?${new URLSearchParams({scene:props.scene})}`)
    if (!fresh()) return
    snapshot.value = value; readError.value = ''
  } catch (error) { if (fresh()) readError.value = error.message }
  finally { if (fresh()) loading.value = false }
}
</script>

<template>
  <section class="material-selection" aria-label="明确选择任务输入">
    <div class="selection-heading"><strong>任务输入（可不选）</strong>
      <v-btn variant="outlined" :loading="loading" :disabled="disabled || loading || !configured" @click="read">读取本场景共享资料</v-btn></div>
    <p class="muted">只复制明确选择的原件；登记前建立本任务私有快照，执行时在 /inputs 只读挂载。续接不重取共享目录，最多16份。</p>
    <v-alert v-if="readError" type="error" variant="tonal" role="alert">{{ readError }} 已选文件名保留，不自动重试或更换。</v-alert>
    <template v-if="snapshot">
      <p class="path">当前运行资料目录：{{ snapshot.directory }}</p>
      <v-select :model-value="modelValue" :items="snapshot.files.map(file=>({title:`${file.name} · ${file.size} 字节`,value:file.name}))"
        label="明确选定的实际文件名" multiple chips closable-chips :disabled="disabled || loading || !configured"
        hide-details="auto" @update:model-value="emit('update:modelValue',$event)" />
      <p v-if="!snapshot.files.length" class="muted">上次读取没有普通共享文件；不会因此清空已选名字或猜其他场景的文件。</p>
    </template>
    <p v-else class="muted">尚未读取可选资料；不选择时保持空选集，不自动挂入整个共享目录。</p>
    <p v-if="modelValue.length" class="original-text">实际选集：{{ modelValue.join('、') }}</p>
    <v-btn v-if="modelValue.length" variant="text" :disabled="disabled || loading" @click="emit('update:modelValue',[])">明确取消全部输入选择</v-btn>
  </section>
</template>

<style scoped>
.material-selection{border:1px solid var(--line);border-radius:10px;padding:12px;min-width:0;overflow-wrap:anywhere}.selection-heading{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}.path{font-size:13px;overflow-wrap:anywhere}.material-selection :deep(.v-btn){min-height:44px}
</style>
