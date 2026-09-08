<script setup>
defineProps({ budget: { type: Object, required: true }, title: { type: String, default: '执行预算' } })
function value(number) { return number ?? '未记录' }
function seconds(number) { return typeof number === 'number' ? Math.round(number * 1000) / 1000 : '未记录' }
</script>

<template>
  <section class="budget-details" :aria-label="title">
    <h4>{{ title }}</h4>
    <dl>
      <div><dt>模型调用</dt><dd><template v-if="budget.model_call_index!==undefined">本次第 {{ budget.model_call_index }} 次 / {{ value(budget.model_calls_limit) }}</template><template v-else>上限 {{ value(budget.model_calls_limit) }} 次<span v-if="budget.model_calls_used!==undefined"> · 已用 {{ budget.model_calls_used }}</span></template></dd></div>
      <div v-if="budget.model_calls_remaining_after!==undefined"><dt>本次调用后剩余</dt><dd>{{ budget.model_calls_remaining_after }} 次模型调用</dd></div>
      <div><dt>普通工具调用</dt><dd><span v-if="budget.tool_calls_used!==undefined">已用 {{ budget.tool_calls_used }} / </span><span v-else>上限 </span>{{ value(budget.tool_calls_limit) }} 次</dd></div>
      <div v-if="budget.tool_calls_remaining!==undefined"><dt>当前可用工具次数</dt><dd>{{ budget.tool_calls_remaining }} 次</dd></div>
      <div v-if="budget.elapsed_seconds_limit!==undefined"><dt>累计执行时间</dt><dd><span v-if="budget.elapsed_seconds_used!==undefined">{{ seconds(budget.elapsed_seconds_used) }} / </span>{{ seconds(budget.elapsed_seconds_limit) }} 秒</dd></div>
      <div v-if="budget.elapsed_seconds_remaining!==undefined"><dt>执行时间余量</dt><dd>{{ seconds(budget.elapsed_seconds_remaining) }} 秒</dd></div>
    </dl>
    <p v-if="budget.terminal_required">终结入口 <code>{{ budget.terminal_required }}</code>；终结占用模型调用，不计普通工具次数。</p>
  </section>
</template>

<style scoped>
.budget-details{padding:16px;background:rgb(var(--v-theme-surface-variant));border-radius:8px;font-size:13px}.budget-details h4{font-size:14px;margin:0 0 14px}.budget-details dl{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px 24px}.budget-details dt{color:var(--muted);margin-bottom:5px}.budget-details dd{margin:0;font-weight:600;overflow-wrap:anywhere}.budget-details p{margin:14px 0 0;line-height:1.6;color:var(--muted)}@media(max-width:550px){.budget-details dl{grid-template-columns:1fr}}
</style>
