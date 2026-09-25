<script setup>
import { fmtTime } from '../../api.js'
import EntityLink from '../EntityLink.vue'
import StatusBadge from '../StatusBadge.vue'
const props = defineProps({page: {type: Object, required: true}})
const { sceneId, detail, expandedRecords } = props.page
</script>
<template>
  <h3>称呼与互动偏好</h3>
  <article
    v-for="memory in detail.preferences"
    :key="memory.id"
    :data-scene-record="'preference:' + memory.id"
    tabindex="-1"
    class="detail-record"
  >
    <div class="record-meta">
      <StatusBadge domain="basis" :status="memory.basis" />
      <span class="breakable">{{ memory.subject }}</span>
    </div>
    <p class="two-lines">{{ memory.statement }}</p>
    <div class="record-meta">
      <EntityLink
        type="memory"
        :id="memory.id"
        :scene-id="sceneId"
        label="认识全文与修订链"
      />
      <span>到期 {{ memory.expires_at ? fmtTime(memory.expires_at) : '未设置' }}</span>
    </div>
    <v-expansion-panels
      v-model="expandedRecords['preference:' + memory.id]"
      variant="accordion"
      class="mt-3"
    >
      <v-expansion-panel title="来源原话">
        <v-expansion-panel-text>
          <div class="detail-links">
            <EntityLink
              v-for="id in memory.evidence"
              :key="id"
              type="event"
              :id="id"
              :scene-id="sceneId"
            />
          </div>
        </v-expansion-panel-text>
      </v-expansion-panel>
    </v-expansion-panels>
  </article>
  <p v-if="!detail.preferences.length" class="empty-copy">暂无有效的称呼与互动偏好。</p>
</template>
<style scoped>
.detail-record:focus-visible{outline:2px solid rgb(var(--v-theme-primary));outline-offset:4px}
.scene-detail-body h3{font-size:17px;margin:24px 0 10px;line-height:1.6}
.scene-detail-body h3:first-child{margin-top:0}
.scene-detail-body p{margin:10px 0;overflow-wrap:anywhere}
.detail-record{padding:18px 0;border-bottom:1px solid var(--line);min-width:0}
.detail-record:last-child{border-bottom:0}
.record-meta{display:flex;gap:8px 12px;align-items:center;flex-wrap:wrap;font-size:12px;color:var(--muted)}
.record-meta>*{min-width:0}
.detail-links{display:grid;gap:10px;min-width:0}
.two-lines{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere;white-space:pre-wrap}
.breakable{overflow-wrap:anywhere}
.empty-copy{padding:28px 16px;text-align:center;font-size:13px;color:var(--muted);line-height:1.8}
</style>
