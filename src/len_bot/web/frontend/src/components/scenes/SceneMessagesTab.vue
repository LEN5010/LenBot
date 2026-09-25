<script setup>
import { fmtTime } from '../../api.js'
import MessageItem from '../MessageItem.vue'
const props = defineProps({page: {type: Object, required: true}})
const { messageReadAt, historical, newPage, messageLoading, olderLoading, viewLatest,
  messageError, messageScroll, messageContent, captureScrollAnchor, hasMore, loadMessages,
  messageLoaded, messages, eventId, inspect } = props.page
</script>
<template>
  <v-card class="scene-tab-card">
    <div class="timeline-toolbar">
      <span>原话与实际发送记录<span v-if="messageReadAt"> · 窗口读取于 {{ fmtTime(messageReadAt) }}</span>
      </span>
      <v-btn
        v-if="historical || newPage"
        size="small"
        color="primary"
        variant="tonal"
        :disabled="messageLoading || olderLoading"
        @click="viewLatest"
      >
        {{ newPage ? '有新消息 · 查看最新' : '返回最新消息' }}
      </v-btn>
    </div>
    <v-alert v-if="messageError" type="error" variant="tonal" class="mx-4 mb-3">
      {{ messageError }}<div v-if="messageReadAt">上次读取于 {{ fmtTime(messageReadAt) }}</div>
    </v-alert>
    <v-progress-linear v-if="messageLoading" indeterminate aria-label="正在读取场景原话" />
    <div
      ref="messageScroll"
      class="message-scroll"
      tabindex="0"
      aria-label="场景原话时间线"
      @scroll.passive="captureScrollAnchor"
    >
      <div ref="messageContent" class="message-content">
        <div v-if="hasMore" class="older-messages">
          <v-btn
            variant="outlined"
            :loading="olderLoading"
            :disabled="olderLoading || messageLoading"
            @click="loadMessages({ older: true })"
          >读取更早原话</v-btn>
        </div>
        <p v-else-if="messageLoaded && messages.length" class="history-start">已到此场景保存的最早原话</p>
        <MessageItem
          v-for="event in messages"
          :key="event.id"
          :event="event"
          :selected="event.id === eventId"
          @inspect="inspect"
        />
        <p v-if="messageLoaded && !messages.length" class="empty-copy">此场景尚无原话记录。</p>
      </div>
    </div>
  </v-card>
</template>
<style scoped>
.scene-directory,.scene-main,.scene-tab-card,.scene-heading,.inline-inspector{min-width:0}
.scene-tab-card{margin-top:16px}
.timeline-toolbar{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;padding:12px 16px;border-bottom:1px solid var(--line);font-size:12px;color:var(--muted)}
.message-content{min-width:0;display:flow-root}
.message-scroll{overflow-anchor:none;padding:4px 16px 12px;max-height:70vh;min-height:360px;overflow:auto;position:relative;scrollbar-gutter:stable}
.message-scroll:focus-visible{outline:2px solid rgb(var(--v-theme-primary));outline-offset:-2px}
.older-messages{text-align:center;padding:16px 0}
.history-start{text-align:center;color:var(--muted);font-size:11px;padding:14px 0}
.empty-copy{padding:28px 16px;text-align:center;font-size:13px;color:var(--muted);line-height:1.8}
@media(max-width:1023px){
  .message-scroll{max-height:none;min-height:240px;overflow:visible;scrollbar-gutter:auto;padding:4px 12px 12px}
  .timeline-toolbar{padding:12px;align-items:flex-start}
  .timeline-toolbar>.v-btn{max-width:100%}
}
</style>
