<script setup>
import { computed } from 'vue'
import { fmtTime } from '../api.js'
import EntityLink from './EntityLink.vue'
const props = defineProps({ data:Object, compact:Boolean })
const items = computed(()=>props.data?.items || [])
const lifecycle = value => ({enabled:'已装载',disabled:'未装载',unconfigured:'未配置',failed:'装载失败',error:'运行错误'}[value] || value)
const requestStatus = value => ({callable:'请求资格通过，执行仍需当前工作与预算',unconfigured:'缺全局参数',plugin_disabled:'运行插件未启用',scene_not_enabled:'本群或发起者未开放',capability_denied:'缺当前授权'}[value] || value || '选择群后核对')
</script>
<template>
  <div class="capability-grid" :class="{compact}">
    <v-card v-for="item in items" :key="item.id" class="capability-card" variant="outlined">
      <v-card-text>
        <div class="card-heading"><h2>{{ item.title }}</h2><v-chip size="small" variant="tonal">{{ item.recent_observation || item.recent_execution || item.recent_delivery ? '有历史记录' : '未验证' }}</v-chip></div>
        <p class="muted mt-2">{{ item.entry }}</p>
        <div v-for="plugin in item.plugins" :key="plugin.id" class="plugin-fact">
          <strong>{{ plugin.name }}</strong>
          <p class="muted">插件 ID：{{ plugin.id }}</p>
          <p>保存：{{ !plugin.configured?'缺参数':plugin.enabled?'启用':'停用' }} · 运行：{{ lifecycle(plugin.state) }}</p>
          <p v-if="data.scene_id">本群：{{ plugin.scene_open?'已开放':'未开放' }} · {{ requestStatus(plugin.request_status) }}</p>
          <p v-if="plugin.last_error" class="error-text">{{ plugin.last_error }}</p>
          <ul v-if="plugin.actions?.length" class="plugin-actions"><li v-for="action in plugin.actions" :key="action.name">{{ action.name }} · {{ action.purpose }}</li></ul>
          <v-btn v-if="!compact && plugin.state!=='absent'" size="small" variant="text" :to="{name:'plugins',query:{id:plugin.id}}">配置 {{ plugin.name }}</v-btn>
        </div>
        <p v-if="item.missing_owners?.length" class="error-text">有未装入的实现：{{ item.missing_owners.join('、') }}</p>
        <dl v-if="!compact && item.deployment.length" class="facts"><template v-for="(fact,index) in item.deployment" :key="index"><dt>{{ fact.label }}</dt><dd>{{ fact.value }}</dd></template></dl>
        <p v-for="grant in item.authorization" :key="grant.title" class="authorization"><strong>{{ grant.title }}</strong>：{{ grant.reason }}</p>
        <template v-if="!compact">
          <details v-if="item.actions.length"><summary>代码已装载的动作（{{ item.actions.length }}）</summary><ul><li v-for="tool in item.actions" :key="tool.name">{{ tool.purpose }} · {{ tool.roles.join(' / ') }}</li></ul></details>
          <div class="evidence">
            <h3>最近事实</h3>
            <p v-if="item.recent_observation">工具 {{ item.recent_observation.tool_name }}：{{ item.recent_observation.status }} · {{ fmtTime(item.recent_observation.created_at) }} <EntityLink type="result" :id="item.recent_observation.id" :scene-id="item.recent_observation.scene_id" label="查看返回" :copyable="false" /></p>
            <p v-if="item.recent_execution">{{ item.recent_execution.worker_type }} 执行：{{ item.recent_execution.state }} · {{ fmtTime(item.recent_execution.accepted_at) }} <EntityLink type="job" :id="item.recent_execution.job_id" :scene-id="item.recent_execution.scene_id" label="查看原工作与执行" :copyable="false" /></p>
            <p v-if="item.recent_delivery">平台行动：{{ item.recent_delivery.status || item.recent_delivery.event_type }} · {{ fmtTime(item.recent_delivery.timestamp) }} <EntityLink type="event" :id="item.recent_delivery.id" :scene-id="item.recent_delivery.scene_id" label="查看真实回执" :copyable="false" /></p>
            <p v-if="!item.recent_observation&&!item.recent_execution&&!item.recent_delivery" class="muted">当前范围没有可展示的调用、执行或交付记录。</p>
          </div>
        </template>
        <div class="actions mt-4">
          <v-btn v-if="data.scene_id" size="small" variant="tonal" :to="{name:'scene',params:{sceneId:data.scene_id},query:{tab:'settings'}}">本群设置</v-btn>
        </div>
      </v-card-text>
    </v-card>
  </div>
</template>
<style scoped>
.capability-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}.card-heading{display:flex;align-items:center;justify-content:space-between;gap:12px}.card-heading h2{font-size:18px}.plugin-fact{padding:14px 0;border-bottom:1px solid var(--line)}.plugin-fact p,.evidence p{margin-top:6px;line-height:1.65;font-size:13px}.plugin-actions{margin:8px 0 0;padding-left:18px;font-size:12px;line-height:1.6}.facts{display:grid;grid-template-columns:90px 1fr;gap:8px;margin:16px 0;font-size:13px}.facts dt{color:var(--muted)}.facts dd{margin:0;overflow-wrap:anywhere}.authorization{font-size:13px;margin:12px 0;line-height:1.6}.error-text{color:rgb(var(--v-theme-error))}.evidence{margin-top:18px}.evidence h3{font-size:13px}details{margin-top:12px;font-size:13px}details ul{padding-left:20px;margin-top:10px}.compact .plugin-fact{font-size:13px}.actions{display:flex;gap:8px;flex-wrap:wrap}@media(max-width:850px){.capability-grid{grid-template-columns:1fr}}
</style>
