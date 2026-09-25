<script setup>
import { computed } from 'vue'
import EntityLink from './EntityLink.vue'
const props = defineProps({ turn: { type: Object, required: true }, sceneId: String })
const ranges = computed(() => {
  return Object.entries(props.turn.provided_result_ranges || {}).flatMap(([id, units]) =>
    Object.entries(units).flatMap(([unit, spans]) =>
      spans.map(([start, end]) => ({ id, unit, start, end })),
    ),
  )
})
const unitName = value => ({ characters: '字符', records: '记录' }[value] || value)
</script>
<template>
  <details class="provided-reads">
    <summary>已确认提供的资料与工作结果</summary>
    <p>这是本次提交保存的阅读范围，不等于全部用于每条答复，也不是资料内容正确性的证明。</p>
    <p v-if="turn.provided_result_ranges==null">旧提交未单独记录工具资料的提供范围。</p>
    <p v-else-if="!ranges.length">此提交没有工具资料提供范围。</p>
    <ul v-else>
      <li v-for="(span,index) in ranges" :key="index">
        <EntityLink
          type="result"
          :id="span.id"
          :scene-id="sceneId"
          :span="{start:span.start,end:span.end,coordinate_unit:span.unit}"
          :label="`查看已提供资料 ${index+1}`"
        />
        <span>[{{ span.start }}, {{ span.end }}) · {{ unitName(span.unit) }}</span>
      </li>
    </ul>
    <p v-if="turn.provided_work_results==null">旧提交未单独记录工作结果的提供身份。</p>
    <p v-else-if="!turn.provided_work_results.length">此提交没有完整提供的工作结果。</p>
    <ul v-else>
      <li v-for="[id,revision] in turn.provided_work_results" :key="`${id}:${revision}`">
        <EntityLink type="job" :id="id" :scene-id="sceneId" />
        <span>采用时提供的工作版本 v{{ revision }}；当前详情可能已更新</span>
      </li>
    </ul>
  </details>
</template>
<style scoped>
.provided-reads{margin-top:12px;min-width:0;font-size:12px;line-height:1.7}
.provided-reads summary{cursor:pointer}
.provided-reads p{margin:8px 0;color:var(--muted)}
.provided-reads ul{list-style:none;padding:0;margin:8px 0}
.provided-reads li{display:grid;gap:4px;margin:8px 0;min-width:0}
.provided-reads li>span{color:var(--muted);overflow-wrap:anywhere}
</style>
