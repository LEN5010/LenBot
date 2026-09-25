<script setup>
import MessageItem from '../MessageItem.vue'
const props = defineProps({page: {type: Object, required: true}})
const { eventId, inspect, aux, auxLoading, deliveryType, setDelivery, route, latestDeliveries,
  olderDeliveries } = props.page
const deliveryOptions = [
  { title: 'MESSAGE_SENT · 实际发送记录', value: 'MESSAGE_SENT' },
  { title: 'MESSAGE_SEND_FAILED · 发送失败', value: 'MESSAGE_SEND_FAILED' },
  { title: 'ACTION_SHADOWED · Shadow 记录', value: 'ACTION_SHADOWED' }
]
</script>
<template>
  <h3>发送记录</h3>
  <p class="muted-copy">按原始事件类型读取。模拟与 Shadow 会单独标记，送达状态以保存的回执为准。</p>
  <v-select
    :model-value="deliveryType"
    :items="deliveryOptions"
    label="原始事件类型"
    hide-details
    class="my-4"
    @update:model-value="setDelivery"
  />
  <v-btn v-if="route.query.before" variant="text" @click="latestDeliveries">返回最新记录</v-btn>
  <MessageItem
    v-for="event in aux?.items || []"
    :key="event.id"
    :data-scene-record="'delivery:' + event.id"
    tabindex="-1"
    :event="event"
    :selected="event.id === eventId"
    @inspect="inspect"
  />
  <p v-if="aux && !aux.items.length" class="empty-copy">此范围没有该类型的发送记录。</p>
  <v-btn
    v-if="aux?.has_more"
    variant="outlined"
    class="mt-4"
    :disabled="auxLoading"
    @click="olderDeliveries"
  >读取更早记录</v-btn>
</template>
<style scoped>
.scene-detail-body h3{font-size:17px;margin:24px 0 10px;line-height:1.6}
.scene-detail-body h3:first-child{margin-top:0}
.scene-detail-body p{margin:10px 0;overflow-wrap:anywhere}
.muted-copy{font-size:13px;color:var(--muted);line-height:1.8}
.empty-copy{padding:28px 16px;text-align:center;font-size:13px;color:var(--muted);line-height:1.8}
</style>
