<script setup>
import { computed,onBeforeUnmount,ref,watch } from 'vue'
import { useRoute,useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'
import { mdiRefresh,mdiClose,mdiArrowLeft,mdiFilterOutline } from '@mdi/js'
import { api,fmtTime,queryString } from '../api.js'
import { purposeOptions,purposeLabel,eventLabel,traceLabel,publicationActionLabel } from '../domain/activity.js'
import PageHeader from '../components/PageHeader.vue'
import StatusBadge from '../components/StatusBadge.vue'
import EntityLink from '../components/EntityLink.vue'
import ScopeSelect from '../components/ScopeSelect.vue'
import ResourceViewer from '../components/ResourceViewer.vue'
import MessageItem from '../components/MessageItem.vue'
import TraceDetails from '../components/TraceDetails.vue'
import ObservationDetails from '../components/ObservationDetails.vue'
import OperationReceipts from '../components/OperationReceipts.vue'
import SourceOutcomes from '../components/SourceOutcomes.vue'
const route=useRoute(),router=useRouter(),{smAndDown}=useDisplay()
const tabs=[{value:'calls',title:'调用账'},{value:'turns',title:'对话轮次'},{value:'events',title:'原始事件'},{value:'logs',title:'运行日志'}]
const tab=computed(()=>route.query.tab || 'calls'),validTab=computed(()=>tabs.some(item=>item.value===tab.value))
const page=computed(()=>Math.max(1,Number(route.query.page)||1)),scene=computed(()=>route.query.scene || '')
const data=ref(null),error=ref(''),loading=ref(false),updatedAt=ref(null),eventTypes=ref([])
const filters=ref({}),selected=ref(null),detailError=ref(''),detailLoading=ref(false),relations=ref(null),relationError=ref('')
const resource=ref(null),resourceError=ref(''),resourceLoading=ref(false)
let sequence=0,detailSequence=0,resourceSequence=0
const detailScene=computed(()=>route.query.object_scene || scene.value)
const selectedId=computed(()=>route.query.id || ''),resourceId=computed(()=>route.query.result || '')
const traceKinds=[['conversation','对话'],['conversation_error','对话失败'],['calendar_command','日程命令'],['live_announcement','订阅开播邀请'],['agent_job','信息工作'],['agent_job_error','工作失败'],['history_maintenance','历史维护'],['history_maintenance_error','历史维护失败'],['work_compression','工作压缩'],['skill_maintenance','技能整理']].map(([value,title])=>({value,title}))
const callStatuses=[['completed','请求完成'],['failed','请求失败'],['cancelled','已取消'],['unconfirmed','未确认']].map(([value,title])=>({value,title}))
const knownUsage=computed(()=>data.value?.totals.reduce((sum,row)=>({prompt:sum.prompt+row.prompt_tokens,output:sum.output+row.completion_tokens,unknown:sum.unknown+row.unknown_usage,cached:sum.cached+row.cached_tokens,reasoning:sum.reasoning+row.reasoning_tokens}),{prompt:0,output:0,unknown:0,cached:0,reasoning:0}))
const items=computed(()=>tab.value==='logs'?data.value:data.value?.items)
function localDate(value){if(!value)return '';const date=new Date(Number(value)*1000);return new Date(date.getTime()-date.getTimezoneOffset()*60000).toISOString().slice(0,16)}
function readFilters(){filters.value={scene:scene.value,purpose:route.query.purpose || '',status:route.query.status || '',kind:route.query.kind || '',actor:route.query.actor || '',event_type:route.query.event_type || '',level:route.query.level || '',since:localDate(route.query.since),until:localDate(route.query.until)}}
function navigate(values){const query={...route.query,...values};for(const key of Object.keys(query))if(query[key]===undefined || query[key]===null || query[key]==='')delete query[key];return router.push({name:'activity',query})}
function changeTab(value){navigate({tab:value,id:undefined,object_scene:undefined,result:undefined,episode:undefined,page:undefined,before:undefined,snapshot:undefined,cursors:undefined,status:undefined,kind:undefined,purpose:undefined,event_type:undefined,actor:undefined})}
function applyFilters(){const f=filters.value;navigate({...f,actor:f.actor || undefined,since:f.since?new Date(f.since).getTime()/1000:undefined,until:f.until?new Date(f.until).getTime()/1000:undefined,id:undefined,object_scene:undefined,result:undefined,episode:undefined,page:undefined,before:undefined,snapshot:undefined,cursors:undefined})}
function args(){return {scene_id:scene.value,since:route.query.since,until:route.query.until}}
async function load(){
  const own=++sequence;loading.value=true;error.value=''
  if(!validTab.value){data.value=null;loading.value=false;return}
  try{
    let value
    if(tab.value==='calls')value=await api('/api/models/usage?'+queryString({...args(),purpose:route.query.purpose,status:route.query.status,page:page.value,page_size:30}))
    else if(tab.value==='turns')value=await api('/api/cockpit/traces?'+queryString({...args(),kind:route.query.kind,episode_id:route.query.episode,page:page.value,page_size:30}))
    else if(tab.value==='events')value=await api('/api/cockpit/events?'+queryString({...args(),actor_id:route.query.actor,event_type:route.query.event_type,limit:50,before:route.query.before,snapshot_rowid:route.query.snapshot}))
    else value=await api('/api/logs?'+queryString({level:route.query.level,limit:200}))
    if(own===sequence){data.value=value;updatedAt.value=Date.now()/1000}
  }catch(e){if(own===sequence)error.value=e.message}finally{if(own===sequence)loading.value=false}
}
async function loadDetail(){
  const own=++detailSequence;selected.value=null;relations.value=null;detailError.value='';relationError.value=''
  if(!selectedId.value || tab.value==='logs')return
  detailLoading.value=true
  const path={calls:'/api/models/usage/',turns:'/api/cockpit/traces/',events:'/api/cockpit/events/'}[tab.value]
  if(!path){detailLoading.value=false;return}
  try{
    const value=await api(path+encodeURIComponent(selectedId.value)+'?'+queryString({scene_id:detailScene.value}))
    if(own!==detailSequence)return
    selected.value=value
    const relationArgs={scene_id:value.scene_id}
    if(tab.value==='events')relationArgs.event_id=value.id
    else if(tab.value==='turns'){
      if(value.kind.startsWith('agent_job'))relationArgs.job_id=value.ref_id
      else if(value.kind.startsWith('history_maintenance'))relationArgs.batch_id=value.ref_id
      else if(['conversation','conversation_error'].includes(value.kind))relationArgs.episode_id=value.ref_id
      else if(['calendar_command','live_announcement'].includes(value.kind))relationArgs.event_id=value.ref_id
      else return
    }
    else if(value.episode_id)relationArgs.episode_id=value.episode_id
    else if(value.job_id)relationArgs.job_id=value.job_id
    else if(value.batch_id)relationArgs.batch_id=value.batch_id
    else return
    try{const linked=await api('/api/cockpit/relations?'+queryString(relationArgs));if(own===detailSequence)relations.value=linked}catch(e){if(own===detailSequence)relationError.value=e.message}
  }catch(e){if(own===detailSequence)detailError.value=e.status===404?'对象不存在，或不属于选定场景。':e.message}finally{if(own===detailSequence)detailLoading.value=false}
}
async function loadResource(offset=0,append=false){const own=++resourceSequence;resourceError.value='';if(!resourceId.value){resource.value=null;return}if(!scene.value){resourceError.value='读取原始资料需要明确来源场景。';return}resourceLoading.value=true;try{const value=await api(`/api/cockpit/tool-results/${encodeURIComponent(resourceId.value)}?`+queryString({scene_id:scene.value,offset}));if(own===resourceSequence)resource.value=append?{...value,content:resource.value.content+value.content}:value}catch(e){if(own===resourceSequence)resourceError.value=e.status===404?'该场景中没有此资料。':e.message}finally{if(own===resourceSequence)resourceLoading.value=false}}
function openItem(item){navigate({id:item.id,object_scene:tab.value==='events'?item.scene_id:undefined,result:undefined})}
function inspectEvent(event){navigate({tab:'events',id:event.id,scene:event.scene_id})}
function previousEvents(){const trail=String(route.query.cursors || '').split(',').filter(Boolean);const before=trail.pop();navigate({before:before==='latest'?undefined:before,cursors:trail.join(',') || undefined,id:undefined})}
function nextEvents(){const trail=String(route.query.cursors || '').split(',').filter(Boolean);trail.push(route.query.before || 'latest');navigate({before:data.value.next_before,snapshot:data.value.snapshot_rowid,cursors:trail.join(','),id:undefined})}
function closeDetail(){navigate({id:undefined,object_scene:undefined})}
function displayUsage(call,key){return call.usage?.[key] ?? '未知'}
function eventSummary(event){return event.payload.raw_text || event.payload.content || event.payload.summary || event.payload.error || eventLabel(event.event_type)}
const listKey=computed(()=>JSON.stringify([tab.value,scene.value,page.value,route.query.purpose,route.query.status,route.query.kind,route.query.actor,route.query.event_type,route.query.level,route.query.since,route.query.until,route.query.before,route.query.snapshot,route.query.episode]))
watch(listKey,()=>{data.value=null;readFilters();load()},{immediate:true})
watch(()=>[selectedId.value,tab.value,detailScene.value],loadDetail,{immediate:true})
watch(()=>[resourceId.value,scene.value],()=>{resource.value=null;loadResource()},{immediate:true})
api('/api/cockpit/event-types').then(value=>eventTypes.value=value.items.map(type=>({value:type,title:eventLabel(type)}))).catch(e=>error.value=e.message)
onBeforeUnmount(()=>{sequence++;detailSequence++;resourceSequence++})
</script>
<template>
  <div class="page-stack activity-view">
    <PageHeader title="运行记录" description="按持久来源查看请求、对话、事件与回执。阅读、筛选和翻页不会调用模型。"><v-btn variant="outlined" :prepend-icon="mdiRefresh" :loading="loading" @click="load">刷新</v-btn></PageHeader>
    <v-tabs :model-value="tab" color="primary" @update:model-value="changeTab"><v-tab v-for="item in tabs" :key="item.value" :value="item.value">{{ item.title }}</v-tab></v-tabs>
    <v-alert v-if="!validTab" type="error" variant="tonal">未找到这个记录标签，请选择上方有效入口。</v-alert>
    <form v-if="validTab" class="filters" @submit.prevent="applyFilters">
      <template v-if="tab!=='logs'"><ScopeSelect v-model="filters.scene" /><v-text-field v-model="filters.since" label="开始时间（浏览器时区）" type="datetime-local" /><v-text-field v-model="filters.until" label="结束时间（浏览器时区）" type="datetime-local" /></template>
      <template v-if="tab==='calls'"><v-select v-model="filters.purpose" :items="purposeOptions" label="调用用途" clearable /><v-select v-model="filters.status" :items="callStatuses" label="请求状态" clearable /></template>
      <v-select v-if="tab==='turns'" v-model="filters.kind" :items="traceKinds" label="记录类型" clearable />
      <template v-if="tab==='events'"><v-select v-model="filters.event_type" :items="eventTypes" label="事件类型" clearable /><v-text-field v-model="filters.actor" label="主体编号" placeholder="user:…" /></template>
      <v-select v-if="tab==='logs'" v-model="filters.level" :items="['DEBUG','INFO','WARNING','ERROR','CRITICAL']" label="日志级别" clearable />
      <v-btn type="submit" :prepend-icon="mdiFilterOutline" color="primary">筛选</v-btn>
    </form>
    <v-alert v-if="error" type="error" variant="tonal">读取失败：{{ error }}<span v-if="data">。下方保留 {{ fmtTime(updatedAt) }} 的结果。</span></v-alert>
    <v-progress-linear v-if="loading" indeterminate color="primary" />
    <template v-if="data">
      <template v-if="tab==='calls'"><div class="usage-grid"><v-card><v-card-text><span>匹配请求</span><strong>{{ data.total }}</strong><small>当前全部筛选条件</small></v-card-text></v-card><v-card><v-card-text><span>已知输入 tokens</span><strong>{{ knownUsage.prompt.toLocaleString() }}</strong><small>其中缓存 {{ knownUsage.cached.toLocaleString() }}</small></v-card-text></v-card><v-card><v-card-text><span>已知输出 tokens</span><strong>{{ knownUsage.output.toLocaleString() }}</strong><small>其中推理 {{ knownUsage.reasoning.toLocaleString() }}</small></v-card-text></v-card><v-card><v-card-text><span>usage 未知</span><strong>{{ knownUsage.unknown }}</strong><small>未知请求不按零费用计算</small></v-card-text></v-card></div><p class="muted usage-note">缓存和推理是各自子项，不重复相加。费用未核实：{{ data.cost.reason }}</p></template>
      <p v-if="tab==='logs'" class="muted range-note">本次进程最多保留最近 1,000 条日志；此页读取符合级别的最近 {{ data.length }} 条，最多 200 条。单条日志由服务端保留最多 500 字符，不是无限历史。</p>
      <p v-else-if="tab==='events'" class="muted range-note">本页 {{ data.items.length }} 条，按首次读取截点向前翻页。事件保存在数据库，页面只读取当前范围。</p>
      <p v-else class="muted range-note">共 {{ data.total }} 条 · 第 {{ page }} 页 · 每页 {{ data.page_size }} 条<span v-if="route.query.episode"> · 已定位关联轮次 {{ route.query.episode }}</span></p>
      <v-card class="activity-list">
        <template v-if="tab==='calls'"><div class="list-heading calls-grid" aria-hidden="true"><span>用途 / 型号</span><span>场景</span><span>请求状态</span><span>输入 / 输出</span><span>开始时间</span><span></span></div><article v-for="item in data.items" :key="item.id" class="record-row calls-grid"><div class="record-main"><strong>{{ purposeLabel(item.purpose) }}</strong><p class="clamp-2">{{ item.provider_id }} / {{ item.model }}</p></div><div><EntityLink type="scene" :id="item.scene_id" :copyable="false" /></div><div><StatusBadge domain="call" :status="item.status" /><p v-if="item.error_type" class="minor clamp-2">{{ item.error_type }}</p></div><div class="usage-cell"><span>{{ displayUsage(item,'prompt_tokens') }} / {{ displayUsage(item,'completion_tokens') }}</span><small>估算输入 {{ item.estimate.input_tokens }}</small></div><time>{{ fmtTime(item.started_at) }}</time><v-btn variant="text" color="primary" size="small" @click="openItem(item)">详情</v-btn></article></template>
        <template v-else-if="tab==='turns'"><article v-for="item in data.items" :key="item.id" class="record-row turns-grid"><div class="record-main"><strong>{{ item.committed && item.kind==='conversation_error'?'对话已提交':traceLabel(item.kind) }}</strong><p class="clamp-2">{{ item.summary }}</p><div class="event-heading mt-2"><v-chip v-if="item.tool_outcomes?.errors" color="error" variant="tonal" size="small">{{ item.tool_outcomes.errors }} 条工具错误</v-chip><v-chip v-if="item.tool_outcomes?.no_results" variant="tonal" size="small">{{ item.tool_outcomes.no_results }} 次无结果</v-chip></div></div><EntityLink type="scene" :id="item.scene_id" :copyable="false" /><time>{{ fmtTime(item.created_at) }}</time><v-btn variant="text" color="primary" size="small" @click="openItem(item)">查看过程</v-btn></article></template>
        <template v-else-if="tab==='events'"><article v-for="item in data.items" :key="item.id" class="record-row events-grid"><div class="record-main"><div class="event-heading"><strong>{{ eventLabel(item.event_type) }}</strong><v-chip v-if="item.simulated" size="small" label>模拟记录</v-chip><StatusBadge v-if="item.delivery_status" domain="delivery" :status="item.delivery_status" /></div><p class="clamp-2">{{ eventSummary(item) }}</p></div><div class="event-origin"><span>{{ item.display_name }}</span><EntityLink type="scene" :id="item.scene_id" :copyable="false" /></div><time>{{ fmtTime(item.timestamp) }}</time><v-btn variant="text" color="primary" size="small" @click="openItem(item)">原文与关联</v-btn></article></template>
        <template v-else><article v-for="(item,index) in data" :key="index" class="record-row log-grid"><div><v-chip :color="item.level==='ERROR' || item.level==='CRITICAL'?'error':item.level==='WARNING'?'warning':'default'" size="small" label>{{ item.level }}</v-chip><time>{{ fmtTime(item.timestamp) }}</time></div><div class="record-main"><strong>{{ item.component }}</strong><details><summary class="clamp-2">{{ item.message }}</summary><p class="readable-copy">{{ item.message }}</p></details></div></article></template>
        <p v-if="!items.length" class="empty-state">{{ tab==='logs'?'当前保留范围内没有匹配日志':'当前筛选下没有匹配记录' }}</p>
      </v-card>
      <v-pagination v-if="['calls','turns'].includes(tab) && data.total>data.page_size" :model-value="page" :length="Math.ceil(data.total/data.page_size)" :total-visible="smAndDown?3:7" @update:model-value="navigate({page:$event,id:undefined})" />
      <div v-if="tab==='events'" class="cursor-actions"><v-btn v-if="route.query.cursors" variant="outlined" :prepend-icon="mdiArrowLeft" @click="previousEvents">返回上一页</v-btn><v-btn :disabled="!data.has_more" variant="outlined" @click="nextEvents">读取更早事件</v-btn><v-btn v-if="route.query.snapshot" variant="text" @click="navigate({before:undefined,snapshot:undefined,cursors:undefined,id:undefined})">回到最新</v-btn></div>
      <p class="sample-time">读取于 {{ fmtTime(updatedAt) }}</p>
    </template>
    <v-dialog :model-value="!!selectedId" :fullscreen="smAndDown" max-width="1000" scrollable @update:model-value="!$event && closeDetail()"><v-card><v-card-title class="detail-heading"><span>{{ tab==='calls'?'调用详情':tab==='turns'?'对话与执行过程':'事件原文与关联' }}</span><v-btn :icon="mdiClose" variant="text" aria-label="关闭详情" @click="closeDetail" /></v-card-title><v-card-text class="detail-body"><v-progress-linear v-if="detailLoading" indeterminate color="primary" /><v-alert v-if="detailError" type="error" variant="tonal">{{ detailError }}</v-alert>
      <template v-if="selected">
        <div class="detail-meta"><EntityLink type="scene" :id="selected.scene_id" /><span class="entity-id">{{ selected.id }}</span></div>
        <template v-if="tab==='calls'"><div class="call-facts"><div><span>调用用途</span><strong>{{ purposeLabel(selected.purpose) }}</strong></div><div><span>模型绑定</span><strong>{{ selected.provider_id }} / {{ selected.model }} / {{ selected.reasoning_effort }}</strong></div><div><span>请求状态</span><StatusBadge domain="call" :status="selected.status" /></div><div><span>开始 / 结束</span><strong>{{ fmtTime(selected.started_at) }} / {{ fmtTime(selected.ended_at) }}</strong></div></div><v-alert v-if="!selected.usage" variant="tonal" type="warning">供应商 usage 未知；取消或失败不证明没有计费。</v-alert><ResourceViewer title="供应商返回的原始 usage" :content="selected.usage" /><ResourceViewer title="独立的本地输入估算" :content="selected.estimate" /><p v-if="selected.error_type" class="text-error">{{ selected.error_type }}</p><div class="detail-meta"><EntityLink v-if="selected.episode_id" type="episode" :id="selected.episode_id" :scene-id="selected.scene_id" label="关联对话轮次" /><EntityLink v-if="selected.job_id" type="job" :id="selected.job_id" :scene-id="selected.scene_id" label="关联信息工作" /></div></template>
        <TraceDetails v-else-if="tab==='turns'" :trace="selected" />
        <template v-else><MessageItem v-if="selected.display_kind!=='system'" :event="selected" @inspect="inspectEvent" /><ResourceViewer v-else :title="eventLabel(selected.event_type)" :content="selected.payload" /><v-expansion-panels><v-expansion-panel title="完整受控事件字段"><v-expansion-panel-text><ResourceViewer title="事件记录" :content="selected" /></v-expansion-panel-text></v-expansion-panel></v-expansion-panels></template>
        <section class="relations-section">
          <h3>持久关联</h3><p class="muted">仅显示记录中明确保存的关联。同轮读取的其他原话可能属于独立请求，各项工作与表达的来源分别列出。</p>
          <v-alert v-if="relationError" type="error" variant="tonal">关联读取失败：{{ relationError }}</v-alert><p v-if="!relations && !detailLoading && !relationError" class="muted">没有可用于定位的轮次、工作或事件关联。</p>
          <template v-if="relations">
            <v-alert v-if="Object.values(relations.truncated).some(Boolean)" type="info" variant="tonal">关联较多，当前显示各类别的受限结果；可从对象详情继续读取。</v-alert>
            <p v-if="relations.source_handling?.pending"><StatusBadge domain="attention" status="pending" /> 当前查看的来源仍在待处理唤醒中。</p>
            <section v-if="relations.turns?.length" class="turn-relations"><h4>已提交轮次的读取与处理</h4><article v-for="turn in relations.turns" :key="turn.event_id" class="turn-relation"><EntityLink type="event" :id="turn.event_id" :scene-id="selected.scene_id" label="查看此轮提交" /><p>实际读取 {{ turn.read_source_event_ids.length }} 条原话；{{ turn.handled_source_event_ids===null?'未单独记录处理来源':`明确处理 ${turn.handled_source_event_ids.length} 条来源` }}。</p><div class="detail-meta"><EntityLink v-for="id in turn.handled_source_event_ids || []" :key="id" type="event" :id="id" :scene-id="selected.scene_id" label="本轮处理的来源" /></div><SourceOutcomes :items="turn.source_outcomes" :scene-id="selected.scene_id" /><details v-if="turn.read_source_event_ids.length"><summary>本轮实际读取的原话</summary><div class="detail-meta"><EntityLink v-for="id in turn.read_source_event_ids" :key="id" type="event" :id="id" :scene-id="selected.scene_id" :label="id" /></div></details></article></section>
            <div class="relation-grid">
              <section><h4>原始事件</h4><div v-for="item in relations.events" :key="item.id" class="relation-row"><EntityLink type="event" :id="item.id" :scene-id="item.scene_id" :label="eventLabel(item.event_type)" :copyable="false" /><span class="clamp-2">{{ eventSummary(item) }}</span></div><p v-if="!relations.events.length" class="muted">未关联事件</p></section>
              <section><h4>工作与调用</h4><div v-for="item in relations.jobs" :key="item.id" class="relation-row"><EntityLink type="job" :id="item.id" :scene-id="item.scene_id" :label="item.goal" :copyable="false" /><span>请求者 QQ {{ item.requester_qq_uid || '未记录' }} · 目标 v{{ item.revision }}</span><EntityLink v-if="item.request_source_event_id" type="event" :id="item.request_source_event_id" :scene-id="item.scene_id" label="这项工作的请求原话" /><span v-else class="muted">未单独保存请求来源</span><StatusBadge domain="job_execution" :status="item.execution_status" /><StatusBadge domain="job_delivery" :status="item.status" /></div><div v-for="item in relations.calls" :key="item.id" class="relation-row"><EntityLink type="call" :id="item.id" :scene-id="item.scene_id" :label="`${purposeLabel(item.purpose)} · ${item.model}`" :copyable="false" /><StatusBadge domain="call" :status="item.status" /></div><p v-if="!relations.jobs.length && !relations.calls.length" class="muted">未关联工作或调用</p></section>
              <section><h4>资料</h4><div v-for="item in relations.tool_results" :key="item.id" class="relation-row"><EntityLink type="result" :id="item.id" :scene-id="item.scene_id" :label="`${item.tool_name} · ${item.content_length} 字符`" :copyable="false" /><StatusBadge domain="observation" :status="item.status" /><code v-if="item.error_code">{{ item.error_code }}</code><span class="clamp-2">{{ item.coverage }}</span></div><p v-if="!relations.tool_results.length" class="muted">未关联工具资料</p></section>
              <OperationReceipts :items="relations.operation_receipts || []" :scene-id="selected.scene_id" /><section><h4>行动与回执</h4><div v-for="item in relations.actions" :key="item.id" class="relation-row"><span class="entity-id">{{ item.id }}</span><v-chip v-if="item.simulated" size="small" label>模拟记录</v-chip><v-chip v-if="item.acknowledges_task_id" size="small" variant="tonal">创建确认</v-chip><v-chip v-if="item.fulfils_task_id" size="small" variant="tonal">履约表达</v-chip><v-chip v-if="item.operation_ref" size="small" variant="tonal">操作确认 {{ item.operation_ref }}</v-chip><EntityLink v-if="item.operation_receipt?.commit_event_id" type="event" :id="item.operation_receipt.commit_event_id" :scene-id="item.scene_id" label="已提交的对应操作" /><span>{{ publicationActionLabel(item.publication_status) }}</span><StatusBadge domain="delivery" :status="item.delivery_status" /><span v-if="item.job_revision">工作 v{{ item.job_revision }}</span><span v-if="item.requester_qq_uid">请求者 QQ {{ item.requester_qq_uid }}</span><EntityLink v-if="item.origin_event_id" type="event" :id="item.origin_event_id" :scene-id="item.scene_id" label="本条表达对应的来源" /><EntityLink v-else-if="item.request_source_event_id" type="event" :id="item.request_source_event_id" :scene-id="item.scene_id" label="待交付工作的请求原话" /><span v-else class="muted">独立表达来源未记录</span><EntityLink v-for="id in item.receipt_event_ids" :key="id" type="event" :id="id" :scene-id="item.scene_id" label="读取回执" :copyable="false" /><span v-if="!item.receipt_event_ids.length" class="muted">尚无已保存回执</span></div><p v-if="!relations.actions.length" class="muted">未关联行动或回执</p></section>
            </div>
          </template>
        </section>
      </template>
    </v-card-text></v-card></v-dialog>
    <v-dialog :model-value="!!resourceId" :fullscreen="smAndDown" max-width="960" scrollable @update:model-value="!$event && navigate({result:undefined})"><v-card><v-card-title class="detail-heading"><span>原始工具资料</span><v-btn :icon="mdiClose" variant="text" aria-label="关闭原始资料" @click="navigate({result:undefined})" /></v-card-title><v-card-text><ResourceViewer title="资料正文" :content="resource?.content" :loading="resourceLoading" :error="resourceError" /><ObservationDetails v-if="resource" :observation="resource" :scene-id="scene" class="mt-4" /><v-btn v-if="resource?.next_offset!==null && resource?.next_offset!==undefined" :loading="resourceLoading" variant="outlined" class="mt-4" @click="loadResource(resource.next_offset,true)">继续读取已保存正文</v-btn></v-card-text></v-card></v-dialog>
  </div>
</template>
<style scoped>
.turn-relations{margin:20px 0;font-size:13px}.turn-relations h4{font-size:13px}.turn-relation{padding:14px 0;border-bottom:1px solid var(--line)}.turn-relation p{line-height:1.7}.turn-relation summary{cursor:pointer;color:rgb(var(--v-theme-primary));font-size:12px}.turn-relation details[open]>summary{margin-bottom:12px}
.usage-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px}.usage-grid span,.usage-grid strong,.usage-grid small{display:block}.usage-grid span{font-size:12px;color:var(--muted)}.usage-grid strong{font-size:26px;font-weight:650;margin:8px 0;letter-spacing:-.03em}.usage-grid small{font-size:11px;color:var(--muted)}.usage-note,.range-note{font-size:12px;line-height:1.7;margin:0}.activity-list{min-width:0}.list-heading{background:#f8fafc;padding:12px 20px;font-size:11px;color:var(--muted);border-bottom:1px solid var(--line)}.record-row{padding:18px 20px;border-bottom:1px solid var(--line);gap:16px;align-items:start;font-size:13px;min-width:0}.record-row:last-child{border-bottom:0}.record-row>div{min-width:0}.calls-grid{display:grid;grid-template-columns:minmax(160px,2fr) minmax(100px,1fr) 105px 110px 145px 50px;gap:16px}.turns-grid,.events-grid{display:grid;grid-template-columns:minmax(180px,1fr) minmax(130px,.35fr) 145px 100px}.record-main strong{font-size:13px;font-weight:600;overflow-wrap:anywhere}.record-main p{font-size:12px;color:var(--muted);margin:6px 0 0;line-height:1.6}.record-row time{font-size:11px;line-height:1.8;color:var(--muted);display:block;white-space:nowrap}.usage-cell span,.usage-cell small{display:block;font-size:12px}.usage-cell small{font-size:11px;color:var(--muted);margin-top:4px}.minor{font-size:11px;margin:6px 0 0;color:var(--muted)}.record-row .v-btn{justify-self:end}.event-heading{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.event-origin{display:grid;gap:6px;font-size:12px;overflow-wrap:anywhere}.cursor-actions{display:flex;gap:12px;flex-wrap:wrap}.log-grid{display:grid;grid-template-columns:155px minmax(0,1fr)}.log-grid time{margin-top:8px}.log-grid summary{cursor:pointer;font-size:13px;margin-top:8px;color:var(--muted)}.sample-time{font-size:11px;color:var(--muted);margin:0}.detail-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;font-size:17px;white-space:normal;border-bottom:1px solid var(--line)}.detail-body{display:grid;gap:24px;min-width:0}.detail-meta{display:flex;gap:12px;align-items:center;flex-wrap:wrap;min-width:0}.call-facts{display:grid;grid-template-columns:1fr 1fr;gap:20px;min-width:0}.call-facts div{min-width:0}.call-facts span{display:block;color:var(--muted);font-size:12px;margin-bottom:6px}.call-facts strong{font-size:13px;font-weight:500;overflow-wrap:anywhere}.relations-section h3{font-size:15px}.relations-section>.muted{font-size:12px;line-height:1.7}.relation-grid{display:grid;grid-template-columns:1fr 1fr;gap:24px}.relation-grid h4{font-size:13px;margin:0 0 12px}.relation-row{display:flex;align-items:flex-start;gap:6px;flex-wrap:wrap;font-size:12px;padding:10px 0;border-bottom:1px solid var(--line);min-width:0}.relation-row .clamp-2{flex-basis:100%;color:var(--muted)}
@media(max-width:1300px){.calls-grid{grid-template-columns:minmax(130px,1fr) minmax(100px,1fr) 95px 100px 120px 50px;gap:10px}.record-row{padding:16px}.record-row time{white-space:normal}.list-heading{padding:12px 16px}}
@media(max-width:1000px){.calls-grid,.turns-grid,.events-grid{grid-template-columns:minmax(0,1fr) auto;gap:10px 16px}.list-heading{display:none}.record-main{grid-column:1/-1}.record-row>.v-btn{grid-column:2;grid-row:auto}.usage-grid{grid-template-columns:1fr 1fr}.event-origin{grid-column:1}.record-row time{align-self:center}.record-row{padding:16px 20px}}
@media(max-width:600px){.usage-grid{gap:12px}.usage-grid strong{font-size:22px}.usage-grid .v-card-text{padding:16px}.call-facts,.relation-grid{grid-template-columns:1fr}.log-grid{grid-template-columns:1fr}.record-row{padding:16px}.detail-heading{font-size:16px}.record-row time{white-space:normal}.filters{gap:12px}}
</style>
