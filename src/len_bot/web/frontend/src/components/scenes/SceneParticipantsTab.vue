<script setup>
import { computed } from 'vue'
const props = defineProps({page: {type: Object, required: true}})
const { detail, participantSearch } = props.page
const participants = computed(() => Object.values(detail.value?.session.participants || {}).filter(item => `${item.actor_id} ${item.nickname || ''} ${item.card || ''}`.toLowerCase().includes((participantSearch.value || '').toLowerCase())))
</script>
<template>
  <h3>参与者</h3>
  <p class="muted-copy">账号昵称与群名片来自已保存的消息；称呼偏好单独保留在认识中。</p>
  <v-text-field
    v-model="participantSearch"
    label="查找账号、昵称或群名片"
    clearable
    hide-details
    class="my-4"
  />
  <article
    v-for="person in participants"
    :key="person.actor_id"
    :data-scene-record="'participant:' + person.actor_id"
    tabindex="-1"
    class="detail-record"
  >
    <strong class="breakable">
      {{ person.card || person.nickname || person.actor_id }}
    </strong>
    <dl class="participant-facts">
      <dt>账号</dt>
      <dd>{{ person.actor_id }}</dd>
      <dt>昵称</dt>
      <dd>{{ person.nickname || '未记录' }}</dd>
      <dt>群名片</dt>
      <dd>{{ person.card || '未记录' }}</dd>
      <dt>群角色</dt>
      <dd>{{ person.role || '未记录' }}</dd>
    </dl>
  </article>
  <p v-if="!participants.length" class="empty-copy">没有符合条件的参与者事实。</p>
</template>
<style scoped>
.detail-record:focus-visible{outline:2px solid rgb(var(--v-theme-primary));outline-offset:4px}
.scene-detail-body h3{font-size:17px;margin:24px 0 10px;line-height:1.6}
.scene-detail-body h3:first-child{margin-top:0}
.scene-detail-body p{margin:10px 0;overflow-wrap:anywhere}
.muted-copy{font-size:13px;color:var(--muted);line-height:1.8}
.detail-record{padding:18px 0;border-bottom:1px solid var(--line);min-width:0}
.detail-record:last-child{border-bottom:0}
.breakable{overflow-wrap:anywhere}
.participant-facts{display:grid;grid-template-columns:64px minmax(0,1fr);gap:6px 12px;font-size:13px;margin-top:12px}
.participant-facts dt{color:var(--muted)}
.participant-facts dd{margin:0;overflow-wrap:anywhere}
.empty-copy{padding:28px 16px;text-align:center;font-size:13px;color:var(--muted);line-height:1.8}
</style>
