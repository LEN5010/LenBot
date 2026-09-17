<script setup>
const props = defineProps({
  conflict: Object,
  current: Object,
  pathLabel: { type: String, default: '' },
})
const emit = defineEmits(['keep', 'take'])
</script>
<template>
  <v-alert v-if="conflict" type="warning" variant="tonal" class="mb-4">
    <p>{{ conflict.message || '配置已被其他操作修改；草稿未保存。' }}</p>
    <p v-if="pathLabel" class="mt-2">冲突字段：{{ pathLabel }}</p>
    <p v-if="current" class="mt-2">最新已保存值已读入对照；选择后才会用现值重建基线。</p>
    <div class="mt-3 d-flex flex-wrap ga-2">
      <v-btn size="small" variant="tonal" @click="emit('keep')">保留我的改动</v-btn>
      <v-btn size="small" variant="outlined" @click="emit('take')">采用现值</v-btn>
    </div>
  </v-alert>
</template>
