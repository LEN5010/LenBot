<script setup>
import { computed } from 'vue'
import { attentionReason, fmtTime } from '../../api.js'
import EntityLink from '../EntityLink.vue'
const props = defineProps({page: {type: Object, required: true}})
const { sceneId, aux, detail, expandedRecords } = props.page
const focused = computed(() => Object.entries(detail.value?.session.focused_participants || {}))
</script>
<template>
  <h3>注意力扫描与待处理来源</h3>
  <p class="muted-copy">扫描到原始位置 {{ detail.session.attention_scanned_event_rowid }}；当前接收截点 {{ detail.session.last_observed_event_rowid }}。扫描位置与模型实际读取分别记录。</p>
  <p>短时观察截止：{{ detail.session.observing_until ? fmtTime(detail.session.observing_until) : '未开启' }}；有新输入才执行，截止不表示正在调用模型。</p>
  <p>待观察或待处理来源 {{ aux?.total ?? detail.session.pending_wake_count }} 项</p>
  <article
    v-for="wake in aux?.items || []"
    :key="wake.event_id"
    :data-scene-record="'wake:' + wake.event_id"
    tabindex="-1"
    class="detail-record"
  >
    <div class="record-meta">
      <v-chip variant="tonal" size="small">
        {{ wake.certain ? '直接搭话或运行来源' : '观察机会' }}
      </v-chip>
      <span>{{ wake.actor_id || '运行事件' }}</span>
      <span>位置 {{ wake.rowid }}</span>
    </div>
    <p>{{ wake.reasons.map(attentionReason).join(' · ') || '未记录原因' }}</p>
    <EntityLink
      type="event"
      :id="wake.event_id"
      :scene-id="sceneId"
      label="查看来源原话与关联"
    />
  </article>
  <p v-if="aux && !aux.items.length" class="empty-copy">没有尚待处理的唤醒来源。</p>
  <h3>真实送达建立的连续关注</h3>
  <div v-for="[actor, until] in focused" :key="actor" class="detail-record">
    <strong class="breakable">{{ actor }}</strong>
    <p>截止 {{ fmtTime(until) }}</p>
  </div>
  <p v-if="!focused.length" class="muted-copy">暂无连续关注记录。</p>
  <v-expansion-panels v-model="expandedRecords.attention" variant="accordion">
    <v-expansion-panel title="事实状态与版本">
      <v-expansion-panel-text>
        <p>事实版本 {{ detail.session.version }} · 认识版本 {{ detail.session.knowledge_revision }}
        </p>
        <p>最近实际发言 {{ fmtTime(detail.session.last_bot_message_at) }}</p>
        <p>发言后新增群友消息 {{ detail.session.human_messages_since_bot }}</p>
        <p>本群清醒截止 {{ fmtTime(detail.session.awake_until) }}</p>
        <p>最近直接人类互动 {{ fmtTime(detail.session.last_direct_human_at) }}</p>
        <div v-if="detail.session.wake_confirmation">
          <p>叫醒确认：{{ detail.session.wake_confirmation.prompt_event_id ? '等待请求者答复' : detail.session.wake_confirmation.prompt_commit_id ? '提问已提交，等待实际送达' : '等待确认提问' }} · {{ fmtTime(detail.session.wake_confirmation.expires_at) }} 到期</p>
          <EntityLink
            type="event"
            :id="detail.session.wake_confirmation.request_event_id"
            :scene-id="sceneId"
            label="叫醒请求原话"
          />
        </div>
        <EntityLink
          v-if="detail.session.wake_source_event_id"
          type="event"
          :id="detail.session.wake_source_event_id"
          :scene-id="sceneId"
          label="本群叫醒来源"
        />
        <EntityLink
          v-if="detail.session.last_bot_message_event_id"
          type="event"
          :id="detail.session.last_bot_message_event_id"
          :scene-id="sceneId"
          label="最近实际发言原始回执"
        />
      </v-expansion-panel-text>
    </v-expansion-panel>
  </v-expansion-panels>
</template>
<style scoped>
.detail-record:focus-visible{outline:2px solid rgb(var(--v-theme-primary));outline-offset:4px}
.scene-detail-body h3{font-size:17px;margin:24px 0 10px;line-height:1.6}
.scene-detail-body h3:first-child{margin-top:0}
.scene-detail-body p{margin:10px 0;overflow-wrap:anywhere}
.muted-copy{font-size:13px;color:var(--muted);line-height:1.8}
.detail-record{padding:18px 0;border-bottom:1px solid var(--line);min-width:0}
.detail-record:last-child{border-bottom:0}
.record-meta{display:flex;gap:8px 12px;align-items:center;flex-wrap:wrap;font-size:12px;color:var(--muted)}
.record-meta>*{min-width:0}
.record-meta strong{color:var(--ink);overflow-wrap:anywhere}
.breakable{overflow-wrap:anywhere}
.empty-copy{padding:28px 16px;text-align:center;font-size:13px;color:var(--muted);line-height:1.8}
</style>
