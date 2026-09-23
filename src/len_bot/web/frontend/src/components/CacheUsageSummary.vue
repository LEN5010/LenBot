<script setup>
defineProps({ cache: Object })
const percent = value => Number.isFinite(value) && value >= 0 && value <= 1 ? `${(value * 100).toFixed(1)}%` : '无法计算'
const count = value => Number.isFinite(value) && value >= 0 ? value.toLocaleString() : '未记录'
</script>

<template>
  <section class="cache-summary" aria-label="缓存命中与统计覆盖">
    <h3>缓存命中与统计覆盖</h3>
    <template v-if="cache">
      <dl>
        <div><dt>有报告输入的缓存命中率</dt><dd>{{ percent(cache.hit_rate) }}</dd></div>
        <div><dt>调用覆盖率</dt><dd>{{ percent(cache.call_coverage) }}（{{ count(cache.cache_reported_calls) }} / {{ count(cache.calls) }}）</dd></div>
        <div><dt>已知输入量覆盖率</dt><dd>{{ percent(cache.input_coverage) }}</dd></div>
      </dl>
      <p>命中率按输入 token 加权：{{ count(cache.cache_reported_tokens) }} 个缓存 token / {{ count(cache.cache_reported_input_tokens) }} 个有缓存报告的输入 token。当前筛选内已知输入共 {{ count(cache.known_input_tokens) }} token。</p>
      <p>缓存字段缺失 {{ count(cache.cache_missing_calls) }} 次；字段无法与有效输入量配对 {{ count(cache.cache_unusable_calls) }} 次；输入量缺失或无效 {{ count(cache.calls - cache.known_input_calls) }} 次。明确报告的零命中参与统计；分母为零时不显示命中率。</p>
      <p>使用当前筛选的全部调用，不限本页。缺失字段不按零命中计算；覆盖率只描述已保存记录，不代表上游账单、价格或缓存保留时长。</p>
    </template>
    <p v-else>未取得缓存覆盖统计。</p>
  </section>
</template>

<style scoped>
.cache-summary{margin:18px 0;padding:16px;border:1px solid var(--line);border-radius:8px;min-width:0}.cache-summary h3{font-size:14px;margin:0 0 12px}.cache-summary dl{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;font-size:12px}.cache-summary dt,.cache-summary p{color:var(--muted)}.cache-summary dd{font-size:14px;margin:6px 0 0;overflow-wrap:anywhere}.cache-summary p{font-size:12px;line-height:1.7;margin:12px 0 0}
@media(max-width:700px){.cache-summary dl{grid-template-columns:minmax(0,1fr)}}
</style>
