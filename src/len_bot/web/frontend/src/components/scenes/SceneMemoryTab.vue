<script setup>
import { fmtTime } from '../../api.js'
import StatusBadge from '../StatusBadge.vue'
const props = defineProps({page: {type: Object, required: true}})
const { sceneId, related, setTab, aux, auxError } = props.page
</script>
<template>
  <h3>{{ sceneId.startsWith('group:')?'本群':'本会话' }}记忆</h3>
  <p class="muted-copy">认识保留来源与修订，不是原话本身。本页只列采样时仍有效且未到期的认识；已撤销、替代或到期的旧版本可从认识列表回查。</p>
  <div class="scene-quick-links">
    <v-btn
      variant="tonal"
      size="small"
      :to="related({name:'memories',query:{scene:sceneId}})"
    >筛选认识与查看修订</v-btn>
    <v-btn variant="text" size="small" @click="setTab('preferences')">称呼与互动偏好</v-btn>
    <v-btn variant="text" size="small" @click="setTab('history')">历史摘要覆盖</v-btn>
    <v-btn
      variant="text"
      size="small"
      :to="related({name:'skills',query:{scene:sceneId}})"
    >方法与经验</v-btn>
  </div>
  <article
    v-for="memory in aux?.items || []"
    :key="memory.id"
    :data-scene-record="'memory:' + memory.id"
    tabindex="-1"
    class="detail-record"
  >
    <div class="record-meta">
      <StatusBadge domain="basis" :status="memory.basis" />
      <span class="breakable">{{ memory.subject }}</span>
      <v-chip
        v-if="memory.expires_at!==null&&memory.expires_at<=Date.now()/1000"
        color="warning"
        size="small"
        variant="tonal"
      >现已过期</v-chip>
    </div>
    <p>
      <RouterLink
        class="two-lines"
        :to="related({name:'memories',query:{scene:sceneId,id:memory.id}})"
      >
        {{ memory.statement }}
      </RouterLink>
    </p>
    <div class="record-meta">
      <span>{{ memory.evidence.length }} 条来源原话</span>
      <span>到期 {{ memory.expires_at ? fmtTime(memory.expires_at) : '未设置' }}</span>
      <time>{{ fmtTime(memory.created_at) }}</time>
    </div>
  </article>
  <p v-if="aux && !aux.items.length && !auxError" class="empty-copy">此范围没有当前有效的认识；历史原话与旧版本仍可回查。</p>
</template>
<style scoped>
.detail-record:focus-visible{outline:2px solid rgb(var(--v-theme-primary));outline-offset:4px}
.scene-quick-links{display:flex;gap:8px;flex-wrap:wrap;margin-top:16px}
.scene-quick-links>.v-btn{max-width:100%}
@media(max-width:650px){
  .scene-quick-links{gap:4px}
}
.scene-detail-body h3{font-size:17px;margin:24px 0 10px;line-height:1.6}
.scene-detail-body h3:first-child{margin-top:0}
.scene-detail-body p{margin:10px 0;overflow-wrap:anywhere}
.muted-copy{font-size:13px;color:var(--muted);line-height:1.8}
.detail-record{padding:18px 0;border-bottom:1px solid var(--line);min-width:0}
.detail-record:last-child{border-bottom:0}
.record-meta{display:flex;gap:8px 12px;align-items:center;flex-wrap:wrap;font-size:12px;color:var(--muted)}
.record-meta>*{min-width:0}
.two-lines{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere;white-space:pre-wrap}
.breakable{overflow-wrap:anywhere}
.empty-copy{padding:28px 16px;text-align:center;font-size:13px;color:var(--muted);line-height:1.8}
</style>
