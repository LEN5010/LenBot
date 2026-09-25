<script setup>
import { computed } from 'vue'
import { spanLabel } from './jobLabels.js'
import EntityLink from '../EntityLink.vue'
import PluginWorkDetails from '../PluginWorkDetails.vue'
import ResourceViewer from '../ResourceViewer.vue'
const props = defineProps({page: {type: Object, required: true}})
const { job, imageErrors } = props.page
const preparedImages=computed(()=>(job.value?.result?.delivery?.segments || []).filter(segment=>segment.type==='image'))
const imageUrl=id=>`/api/media/${encodeURIComponent(id)}/file?scene_id=${encodeURIComponent(job.value.scene_id)}`
const deliveryExplanation = computed(() => job.value?.delivery_required === false
  ? '这是已有公共研究来源与系统发起身份的工作，不走群内首次交付。执行完成只表示结果已保存；之后各群是否分享及其真实回执另行记录。'
  : ({
  result_ready: job.value?.result?.delivery ? '当前版本的成品已经保存，待通过工作交付；图片不会交给对话模型重新改写。' : '当前版本的执行结果已经保存，正在等待组织首次回应。',
  awaiting_delivery: '当前版本的结果已关联表达行动，正在等待真实发送回执。',
  completed: '已记录此工作的交付完成状态，具体送达以关联回执为准。',
  delivery_unknown: '发送结果未知，需要核对原发送回执；已有研究结果仍然保留。',
  failed: job.value?.result ? '工作保留了执行结果，当前发送没有确认送达。' : '当前工作失败，已保存进度与原因可在本页回查。',
  review_required: '执行已中断，进度和已用预算保留；恢复前需核对当前版本和中断原因。',
  shadow_observed: '本次仅保存了 Shadow 表达，没有真实群聊送达回执。',
  cancelled: '工作已停止，已有资料和历史记录保留。',
}[job.value?.status] || '当前还没有保存首次交付结果。'))
</script>
<template>
  <v-card-text class="detail-body">
    <section v-if="job.resume_from">
      <h3>本版继续自 v{{ job.resume_from.revision }}</h3>
      <p>原版交付状态：{{ job.resume_from.response_status }}。旧版结果与已用预算保留。</p>
      <EntityLink
        v-if="job.resume_from.delivery_event_id"
        type="event"
        :id="job.resume_from.delivery_event_id"
        :scene-id="job.scene_id"
        label="查看原版交付回执"
      />
      <ResourceViewer title="原版结果与未完成项" :content="job.resume_from.result" />
    </section>
    <h3>{{ job.delivery_required === false ? '结果保存与后续分享' : '当前版本首次交付' }}</h3>
    <p>{{ deliveryExplanation }}</p>
    <dl v-if="job.delivery_required !== false" class="summary-facts">
      <dt>创建确认行动</dt>
      <dd>
        <code v-if="job.ack_action_id">{{ job.ack_action_id }}</code>
        <span v-else class="muted-copy">未保存确认行动引用</span>
      </dd>
      <dt>结果交付行动</dt>
      <dd>
        <code v-if="job.delivery_action_id">{{ job.delivery_action_id }}</code>
        <span v-else class="muted-copy">未保存交付行动引用</span>
      </dd>
      <dt>结果送达回执</dt>
      <dd>
        <EntityLink
          v-if="job.delivery_event_id"
          type="event"
          :id="job.delivery_event_id"
          :scene-id="job.scene_id"
          label="读取此工作版本的结果发送回执"
        />
        <span v-else class="muted-copy">未保存结果回执引用</span>
      </dd>
    </dl>
    <PluginWorkDetails :job="job" />
    <section v-if="job.result?.delivery">
      <h3>已生成的交付成品</h3>
      <p class="muted-copy">这里显示保存的成品。发送失败或送达未知时仍可查看，实际交付见上方回执。</p>
      <EntityLink
        type="result"
        :id="job.result.delivery.result_id"
        :scene-id="job.scene_id"
        label="成品结构化资料与来源"
      />
      <div v-for="segment in preparedImages" :key="segment.asset_id" class="prepared-image">
        <v-alert v-if="imageErrors.has(segment.asset_id)" type="warning" variant="tonal">此图片目前不可读取，已保存的报告资料仍可回查。</v-alert>
        <a v-else :href="imageUrl(segment.asset_id)" target="_blank" rel="noopener">
          <img
            :src="imageUrl(segment.asset_id)"
            alt="已生成的工作报告，点击查看完整图片"
            @error="imageErrors.add(segment.asset_id)"
          />
        </a>
        <EntityLink
          type="media"
          :id="segment.asset_id"
          :scene-id="job.scene_id"
          label="图片资产与来源"
        />
      </div>
    </section>
    <template v-if="job.result">
      <h3>当前版本执行结果</h3>
      <ResourceViewer title="完整结果" :content="job.result.summary" />
      <h3 v-if="job.result.unresolved.length">尚未解决</h3>
      <ul v-if="job.result.unresolved.length">
        <li v-for="(item, index) in job.result.unresolved" :key="index">{{ item }}</li>
      </ul>
    </template>
    <v-alert v-else type="info" variant="tonal">尚无已保存的执行结果。</v-alert>
    <p v-if="job.result?.reason" class="readable-copy">结果或中断原因：{{ job.result.reason }}</p>
    <template v-if="job.result?.evidence_spans?.length">
      <h3>结论关联的资料范围</h3>
      <ul>
        <li v-for="(span,index) in job.result.evidence_spans" :key="index">
          <EntityLink
            type="result"
            :id="span.result_id"
            :scene-id="job.scene_id"
            :span="span"
            label="回读结论引用范围"
          />
          <span>{{ spanLabel(span) }}（起含止不含）</span>
        </li>
      </ul>
    </template>
    <ResourceViewer
      v-if="job.result?.work_state && !job.work_state"
      title="形成此结果时保存的进度"
      :content="job.result.work_state"
    />
    <h3>当前要求</h3>
    <ul v-if="job.constraints.length">
      <li v-for="(item, index) in job.constraints" :key="index">{{ item }}</li>
    </ul>
    <p v-else class="muted-copy">没有附加要求。</p>
    <h3>已保存的来源与修订原话</h3>
    <p class="muted-copy">以下是工作的资料来源集合；请求者与唯一请求原话单独显示在页首。</p>
    <div class="link-list">
      <EntityLink
        v-for="id in job.source_event_ids"
        :key="id"
        type="event"
        :id="id"
        :scene-id="job.scene_id"
      />
    </div>
  </v-card-text>
</template>
<style scoped>
.prepared-image{display:grid;gap:12px;margin-top:20px}
.prepared-image img{display:block;width:min(100%,540px);height:auto;border:1px solid var(--line);border-radius:8px}
.summary-facts{display:grid;grid-template-columns:130px minmax(0,1fr);gap:10px 16px;margin:12px 0}
.summary-facts dt{color:rgb(var(--v-theme-on-surface-variant))}
.summary-facts dd{margin:0;overflow-wrap:anywhere}
@media(max-width:650px){
  .summary-facts{grid-template-columns:minmax(0,1fr);gap:4px}
  .summary-facts dd{margin-bottom:10px}
}
.field-label,.work-usage,.muted-copy,.read-time{font-size:13px;color:rgb(var(--v-theme-on-surface-variant))}
.identity-line>*,.link-list>*{min-width:0;overflow-wrap:anywhere}
.detail-body{min-width:0;line-height:1.65}
.detail-body h3{font-size:17px;margin:24px 0 12px}
.detail-body h3:first-child{margin-top:0}
.detail-body ul,.detail-body ol{padding-left:24px;margin:8px 0}
.detail-body li{margin:8px 0;overflow-wrap:anywhere}
.detail-body p{margin:10px 0}
.link-list{display:grid;gap:10px}
</style>
