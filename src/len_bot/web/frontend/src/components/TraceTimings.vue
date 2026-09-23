<script setup>
import EntityLink from './EntityLink.vue'
import { formatDurationMs as duration } from '../domain/activity.js'
defineProps({ timings: Object, sceneId: String })
</script>

<template>
  <section class="trace-timings" aria-label="已保存的阶段耗时">
    <h4>已保存的阶段耗时</h4>
    <p class="timing-note">按原轨迹逐段列出，缺失不按零计。嵌套调用、并行工具和装配中的压缩可能重叠，不相加为总延迟；模型计时不是首字延迟，发布也不等于平台送达。</p>
    <p v-if="timings?.elapsed_ms !== null && timings?.elapsed_ms !== undefined" class="timing-note">轨迹记录的外层耗时：{{ duration(timings.elapsed_ms) }}；不是这条消息的独占耗时，执行槽位等待另列，不据此相加为总延迟。</p>
    <p v-if="!timings?.runs?.length" class="timing-note">该轨迹没有可展示的分阶段计时。</p>
    <article v-for="run in timings?.runs || []" :key="run.index" class="timing-run">
      <h5>{{ run.cognition_slot_wait_ms != null ? '槽位等待记录' : `执行段 ${run.index}` }}<span v-if="run.job_revision !== null"> · 目标版本 {{ run.job_revision }}</span></h5>
      <dl class="timing-facts">
        <div v-if="run.cognition_slot_wait_ms !== null && run.cognition_slot_wait_ms !== undefined"><dt>对话执行槽位等待</dt><dd>{{ duration(run.cognition_slot_wait_ms) }}</dd></div>
        <div><dt>初始来源读取</dt><dd>{{ duration(run.initial_source_reads_ms) }}</dd></div>
        <div><dt>初始上下文装配</dt><dd>{{ duration(run.initial_context_ms) }}</dd></div>
        <div><dt>本段提交累计</dt><dd>{{ duration(run.commit_ms) }}</dd></div>
        <div><dt>本段发布累计</dt><dd>{{ duration(run.publication_ms) }}</dd></div>
      </dl>
      <p class="timing-note">提交与发布计时包含各自等待和失败前已耗时间，不能按耗时判断成功。工具执行计时不含全部回执保存与后续处理。</p>
      <details v-if="run.steps.length">
        <summary>逐请求与工具计时（{{ run.steps.length }} 步）</summary>
        <article v-for="(step, index) in run.steps" :key="index" class="timing-step">
          <strong>{{ Number.isInteger(step.index) ? `第 ${step.index + 1} 步` : '步骤序号未记录' }}</strong>
          <EntityLink v-if="step.call_id" type="call" :id="step.call_id" :scene-id="sceneId" label="查看对应调用" />
          <dl class="timing-facts">
            <div><dt>请求准备</dt><dd>{{ duration(step.request_preparation_ms) }}</dd></div>
            <div><dt>模型请求至响应读取</dt><dd>{{ duration(step.model_ms) }}</dd></div>
            <div><dt>工具结果装配</dt><dd>{{ duration(step.tool_presentation_ms) }}</dd></div>
          </dl>
          <ul v-if="step.tools.length"><li v-for="(tool, toolIndex) in step.tools" :key="toolIndex">{{ tool.name || '工具名未记录' }} · 执行 {{ duration(tool.execution_ms) }}<span v-if="tool.id"> · {{ tool.id }}</span></li></ul>
        </article>
      </details>
    </article>
  </section>
</template>

<style scoped>
.trace-timings{min-width:0;margin:16px 0}.trace-timings h4{font-size:13px;margin:0 0 8px}.trace-timings h5{font-size:12px;margin:0}.timing-note{font-size:12px;color:var(--muted);line-height:1.7;overflow-wrap:anywhere}.timing-run{border-top:1px solid var(--line);padding:14px 0}.timing-facts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;font-size:12px;margin:12px 0}.timing-facts dt{color:var(--muted)}.timing-facts dd{margin:4px 0 0;overflow-wrap:anywhere}.trace-timings summary{font-size:12px;cursor:pointer}.timing-step{border-top:1px solid var(--line);padding:12px 0;margin-top:10px;font-size:12px}.timing-step strong{display:block;margin-bottom:6px}.timing-step ul{padding-left:18px;line-height:1.8;overflow-wrap:anywhere}.timing-step li span{color:var(--muted)}
@media(max-width:600px){.timing-facts{grid-template-columns:minmax(0,1fr)}}
</style>
