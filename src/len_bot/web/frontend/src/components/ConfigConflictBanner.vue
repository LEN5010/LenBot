<script setup>
import { fmtTime } from '../api.js'
const props = defineProps({
  conflict: Object,
  current: Object,
  pathLabel: { type: String, default: '' },
  busy: Boolean,
  readError: { type: String, default: '' },
  readAt: Number,
  exclusiveBackend: Boolean,
  keepDisabled: Boolean,
})
const emit = defineEmits(['keep', 'take', 'reload'])
</script>
<template>
  <v-alert v-if="conflict" type="warning" variant="tonal" class="mb-4">
    <p>{{ conflict.message || '配置已被其他操作修改；草稿未保存。' }}</p>
    <p v-if="pathLabel" class="mt-2">冲突字段：{{ pathLabel }}</p>
    <p v-if="current" class="mt-2">已读入当前保存值<span v-if="readAt">（{{ fmtTime(readAt) }}）</span>；选择只更新本页草稿和基线，不自动保存。“保留我的改动”保留实际改过的字段，未改字段采用现值；改过的列表按整组保留。</p>
    <p v-if="current&&exclusiveBackend" class="mt-2">互斥后端冲突保留自己编辑的分支，保存前请核对。</p>
    <p v-if="!current" class="mt-2">尚未取得冲突后的保存值，不能用之前的记录重建基线。请先重读，原草稿保留。</p>
    <p v-if="readError" class="mt-2" role="alert">重读失败：{{ readError }}</p>
    <details v-if="current&&$slots.current" class="conflict-current mt-3">
      <summary>查看本次读取的保存值</summary>
      <div class="mt-3"><slot name="current" /></div>
    </details>
    <div class="mt-3 d-flex flex-wrap ga-2">
      <v-btn
        size="small"
        variant="tonal"
        :disabled="busy || !current || keepDisabled"
        @click="emit('keep')"
      >保留我的改动</v-btn>
      <v-btn size="small" variant="outlined" :disabled="busy || !current" @click="emit('take')">采用现值</v-btn>
      <v-btn size="small" variant="text" :disabled="busy" @click="emit('reload')">重读保存值</v-btn>
    </div>
  </v-alert>
</template>
<style scoped>
.conflict-current{min-width:0}
.conflict-current summary{cursor:pointer;font-weight:600}
.conflict-current summary:focus-visible{outline:2px solid currentColor;outline-offset:4px}
</style>
