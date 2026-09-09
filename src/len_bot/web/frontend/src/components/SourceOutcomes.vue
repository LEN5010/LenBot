<script setup>
import EntityLink from './EntityLink.vue'
defineProps({items:Array,sceneId:String})
const labels={replied:'已组织回应',delegated:'已委托',waiting:'等待外部回应',incomplete:'本次未完成',silent:'选择旁听'}
</script>
<template>
  <div v-if="items?.length" class="source-outcomes">
    <article v-for="source in items" :key="source.source_event_id" class="source-outcome">
      <div class="source-heading"><EntityLink type="event" :id="source.source_event_id" :scene-id="sceneId" label="请求或触发来源" /><v-chip size="small" variant="tonal">{{ labels[source.status] || source.status }}</v-chip></div>
      <p v-if="source.reason">{{ source.reason }}</p>
      <p v-for="(item,index) in source.unfinished || []" :key="index">未完成：{{ item }}</p>
      <p v-if="source.proposal_refs?.length">关联操作：{{ source.proposal_refs.join('、') }}</p>
      <p v-if="source.task_ids?.length">关联工作或提醒：{{ source.task_ids.join('、') }}</p>
      <p v-if="source.action_ids?.length">关联行动：{{ source.action_ids.join('、') }}。送达以实际回执为准。</p>
    </article>
  </div>
</template>
<style scoped>
.source-outcomes{display:grid;gap:10px}.source-outcome{border-left:3px solid var(--line);padding:8px 12px;min-width:0}.source-heading{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.source-outcome p{font-size:12px;line-height:1.7;overflow-wrap:anywhere;white-space:pre-wrap;margin:8px 0 0}
</style>
