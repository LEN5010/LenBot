<script setup>
import { computed } from 'vue'
import EntityLink from './EntityLink.vue'

const props = defineProps({ basis: Object, sceneId: String })
const labels = {
  social: '社交表达', general: '一般解释', observed: '引用已读资料',
  work_result: '采用工作成果', mixed: '混合依据', unverified: '含未核实内容',
}
const label = computed(() => labels[props.basis?.kind] || `未识别类别：${props.basis?.kind}`)
const unit = value => ({ characters: '字符', records: '记录' }[value] || `未识别单位 ${value}`)
const work = computed(() => props.basis?.work_result)
</script>

<template>
  <section class="answer-basis" aria-label="本条答复依据">
    <div class="basis-heading">
      <strong>本条答复依据</strong>
      <v-chip
        v-if="basis"
        size="small"
        variant="tonal"
        :color="basis.kind==='unverified'?'warning':undefined"
      >
        {{ label }}
      </v-chip>
    </div>
    <p v-if="!basis" class="basis-note">此表达未记录答复依据；不从来源原话或同轮工具调用补推。</p>
    <template v-else>
      <p class="basis-note">这是保存的依据声明与来源关联，不代表结论已验证为真，也不代表已经送达。</p>
      <div v-if="basis.unresolved?.length" class="basis-gaps">
        <strong>尚未核实或完成</strong>
        <ul><li v-for="(gap,index) in basis.unresolved" :key="index">{{ gap }}</li></ul>
      </div>
      <div v-if="basis.event_ids?.length" class="basis-section">
        <h4>实际引用的原话</h4>
        <div class="basis-links">
          <EntityLink
            v-for="id in basis.event_ids"
            :key="id"
            type="event"
            :id="id"
            :scene-id="sceneId"
          />
        </div>
      </div>
      <div v-if="basis.result_spans?.length" class="basis-section">
        <h4>实际引用的资料范围</h4>
        <ul class="basis-spans">
          <li v-for="(span,index) in basis.result_spans" :key="index">
            <EntityLink
              type="result"
              :id="span.result_id"
              :scene-id="sceneId"
              :span="span"
              :label="`查看引用资料 ${index+1}`"
            />
            <span>[{{ span.start }}, {{ span.end }}) · {{ unit(span.coordinate_unit) }}</span>
          </li>
        </ul>
        <p class="basis-note">区间不包含终点。链接按原坐标读取已保存资料，不重新调用工具，也不增加模型已读范围。</p>
      </div>
      <div v-if="work" class="basis-section">
        <h4>采用的工作版本</h4>
        <div class="basis-work">
          <EntityLink type="job" :id="work.job_id" :scene-id="sceneId" label="打开原工作" />
          <span>当时采用 v{{ work.revision }} · {{ work.status==='completed'?'完整结果':work.status==='partial'?'部分结果':work.status }}
          </span>
        </div>
        <p class="basis-note">这是本条采用时的来源快照；工作详情可能已有新版本。工作结果不等于对话重读了全部原始材料。</p>
        <details v-if="work.result_ids?.length || work.evidence_spans?.length">
          <summary>工作成果保留的资料关联</summary>
          <div class="basis-links">
            <EntityLink
              v-for="id in work.result_ids || []"
              :key="id"
              type="result"
              :id="id"
              :scene-id="sceneId"
            />
          </div>
          <ul class="basis-spans">
            <li v-for="(span,index) in work.evidence_spans || []" :key="index">
              <EntityLink
                type="result"
                :id="span.result_id"
                :scene-id="sceneId"
                :span="span"
                :label="`查看工作来源 ${index+1}`"
              />
              <span>[{{ span.start }}, {{ span.end }}) · {{ unit(span.coordinate_unit) }}</span>
            </li>
          </ul>
        </details>
      </div>
      <p v-if="!basis.event_ids?.length && !basis.result_spans?.length && !work" class="basis-note">本条没有直接原话、工具资料或工作成果关联。</p>
    </template>
  </section>
</template>

<style scoped>
.answer-basis{margin:14px 0;padding:14px;border:1px solid var(--line);border-radius:8px;min-width:0;font-size:13px;line-height:1.7}
.basis-heading,.basis-work{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.basis-note{font-size:12px;color:var(--muted);margin:8px 0!important;overflow-wrap:anywhere}
.basis-section{margin-top:14px;min-width:0}
.basis-section h4{font-size:13px;margin:0 0 8px}
.basis-links{display:grid;gap:8px;min-width:0}
.basis-spans{list-style:none;padding:0;margin:8px 0}
.basis-spans li{display:grid;gap:4px;padding:6px 0;min-width:0}
.basis-spans li>span{font-size:12px;color:var(--muted)}
.basis-gaps{border-left:3px solid rgb(var(--v-theme-warning));padding-left:12px;margin:12px 0}
.basis-gaps ul{padding-left:18px;margin:6px 0}
.basis-gaps li{white-space:pre-wrap;overflow-wrap:anywhere}
.basis-section details{margin-top:10px}
.basis-section summary{cursor:pointer;margin-bottom:8px}
.basis-heading strong{font-size:13px}
</style>
