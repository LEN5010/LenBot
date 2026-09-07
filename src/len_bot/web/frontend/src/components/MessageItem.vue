<script setup>
import { computed, ref } from 'vue'
import { mdiRobotOutline, mdiInformationOutline, mdiClose } from '@mdi/js'
import { fmtTime,attentionReason } from '../api.js'
import EntityLink from './EntityLink.vue'
import StatusBadge from './StatusBadge.vue'
const props=defineProps({event:{type:Object,required:true},selected:Boolean})
const emit=defineEmits(['inspect'])
const expanded=ref(false),quoteExpanded=ref(false),image=ref(null)
const text=computed(()=>props.event.payload.raw_text ?? props.event.payload.content ?? '')
const name=computed(()=>props.event.display_name || props.event.actor_id)
const reasons=computed(()=>props.event.attention.attention_reasons || [])
const attentionText=computed(()=>Object.hasOwn(props.event.attention,'attention_reasons')?(reasons.value.length?reasons.value.map(attentionReason).join(' · '):'仅存储 · 未产生独立唤醒'):'没有保存注意力判定')
const imageUrl=asset=>`/api/media/${encodeURIComponent(asset.id)}/file?scene_id=${encodeURIComponent(props.event.scene_id)}`
</script>
<template>
  <article class="message-item" :class="{selected,bot:event.display_kind==='bot',system:event.display_kind==='system'}" :data-event-id="event.id">
    <div class="message-avatar"><v-icon v-if="event.display_kind==='bot'" :icon="mdiRobotOutline" size="19" /><span v-else>{{ name.slice(0,1) }}</span></div>
    <div class="message-main">
      <header class="message-meta"><strong>{{ name }}</strong><span v-if="event.display_kind==='system'" class="muted">系统记录</span><time>{{ fmtTime(event.timestamp) }}</time><v-chip v-if="event.simulated" size="small" variant="tonal" label>模拟记录</v-chip><StatusBadge v-if="event.delivery_status" domain="delivery" :status="event.delivery_status" /></header>
      <div v-if="event.quote" class="message-quote"><span v-if="event.quote.missing" class="muted">引用原话不可用或不属于当前场景</span><template v-else><EntityLink type="event" :id="event.quote.event_id" :scene-id="event.scene_id" :label="`引用 ${event.quote.display_name}`" :copyable="false" /><p :class="{'clamp-2':!quoteExpanded}">{{ event.quote.text }}</p><v-btn v-if="event.quote.text.length>200" variant="text" size="small" @click="quoteExpanded=!quoteExpanded">{{ quoteExpanded?'收起引用':'展开引用全文' }}</v-btn><div v-if="event.quote.media.length" class="quote-images"><button v-for="asset in event.quote.media" :key="asset.id" type="button" class="image-button" aria-label="查看引用原图" @click="image=asset"><v-img :src="imageUrl(asset)" :alt="asset.description || '引用原图'" width="72" height="72" contain /></button></div></template></div>
      <p v-if="text" class="message-text" :class="{collapsed:!expanded && text.length>800}">{{ text }}</p>
      <v-btn v-if="text.length>800" size="small" variant="text" color="primary" @click="expanded=!expanded">{{ expanded?'收起全文':`展开全文（${text.length} 字）` }}</v-btn>
      <div v-if="event.media.length" class="message-images"><button v-for="asset in event.media" :key="asset.id" type="button" class="image-button" :aria-label="`查看原图：${asset.description || '图片'}`" @click="image=asset"><v-img :src="imageUrl(asset)" :alt="asset.description || '消息原图'" width="180" height="160" contain><template #error><span class="image-error">原图不可读</span></template></v-img></button></div>
      <footer class="message-footer"><span v-if="event.display_kind==='human'" class="attention-copy">{{ attentionText }}</span><span v-else class="attention-copy">{{ event.display_kind==='system'?'系统资料，不代表群聊发言':'' }}</span><v-btn size="small" variant="text" :prepend-icon="mdiInformationOutline" @click="emit('inspect',event)">查看关联</v-btn></footer>
    </div>
    <v-dialog :model-value="!!image" max-width="960" @update:model-value="!$event && (image=null)"><v-card v-if="image"><v-card-title class="image-title"><span>消息原图</span><v-btn :icon="mdiClose" variant="text" aria-label="关闭原图" @click="image=null" /></v-card-title><v-card-text><img class="original-image" :src="imageUrl(image)" :alt="image.description || '消息原图'" /><p class="muted">{{ image.description }}</p><EntityLink type="media" :id="image.id" :scene-id="event.scene_id" label="素材来源与详情" /></v-card-text></v-card></v-dialog>
  </article>
</template>
<style scoped>
.message-item{display:flex;align-items:flex-start;gap:12px;padding:20px 4px;border-bottom:1px solid var(--line);min-width:0}.message-avatar{display:grid;place-items:center;flex:none;width:32px;height:32px;border-radius:10px;background:#edf1f6;color:#63748b;font-size:13px;font-weight:600}.bot .message-avatar{background:#eaf1ff;color:#2563eb}.message-main{min-width:0;flex:1}.message-meta{display:flex;align-items:center;gap:8px;flex-wrap:wrap;min-width:0;font-size:12px}.message-meta strong{font-size:13px;overflow-wrap:anywhere}.message-meta time{font-size:11px;color:var(--muted);white-space:nowrap}.message-text{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.85;margin:8px 0 0;font-size:14px}.message-text.collapsed{display:-webkit-box;-webkit-line-clamp:8;-webkit-box-orient:vertical;overflow:hidden}.message-quote{padding:8px 12px;border-left:3px solid #ccd8e7;background:#f7f9fc;margin:12px 0;min-width:0;font-size:12px}.message-quote p{white-space:pre-wrap;overflow-wrap:anywhere;margin:6px 0 0}.quote-images{display:flex;gap:8px;margin-top:8px}.message-images{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}.image-button{border:1px solid var(--line);border-radius:8px;background:#f5f7fa;max-width:100%;cursor:pointer;padding:4px}.image-error{display:grid;place-items:center;height:100%;font-size:12px;color:var(--muted)}.message-footer{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;margin-top:8px}.attention-copy{font-size:11px;color:var(--muted)}.selected{background:#f2f6ff;border-radius:8px;padding-inline:12px}.system{background:#f7f9fb}.image-title{display:flex;align-items:center;justify-content:space-between}.original-image{display:block;max-width:100%;max-height:72vh;object-fit:contain;margin:0 auto 16px}
@media(max-width:600px){.message-item{gap:8px;padding-block:16px}.message-avatar{width:28px;height:28px;border-radius:8px}.message-meta time{flex-basis:100%}.message-text{font-size:14px}.message-footer{align-items:flex-start}.message-footer .v-btn{margin-left:auto}}
</style>
