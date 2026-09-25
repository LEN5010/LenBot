<script setup>
import { computed,onMounted,reactive,ref } from 'vue'
import { mdiRefresh,mdiArrowRight,mdiForumOutline,mdiDatabaseOutline,mdiClockOutline } from '@mdi/js'
import { api,fmtTime,sceneName } from '../api.js'
import { useAppState,refreshStatus } from '../composables/useAppState.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'
import { useGuardedRead } from '../composables/useGuardedRead.js'
import { roleNames } from '../domain/roles.js'
import PageHeader from '../components/PageHeader.vue'
import EntityLink from '../components/EntityLink.vue'
import StatusBadge from '../components/StatusBadge.vue'
import CapabilityCards from '../components/CapabilityCards.vue'
const app=useAppState(),data=ref(null),error=ref(''),statsLoading=ref(false)
const statsGuard=useRequestGuard(), pluginGuard=useRequestGuard()
const issueDefinitions=[
  {
    key:'review',
    title:'待核对工作',
    type:'job',
    path:'/api/cockpit/jobs?status=review_required&page_size=4',
    to:{name:'jobs',query:{status:'review_required'}}
  },
  {
    key:'jobs',
    title:'工作送达未知',
    type:'job',
    path:'/api/cockpit/jobs?status=delivery_unknown&page_size=4',
    to:{name:'jobs',query:{status:'delivery_unknown'}}
  },
  {
    key:'reminders',
    title:'提醒送达未知',
    type:'task',
    path:'/api/cockpit/tasks?kind=reminder&status=delivery_unknown&page_size=4',
    to:{name:'tasks',query:{tab:'reminders',status:'delivery_unknown'}}
  },
  {
    key:'deferred',
    title:'延期送达未知',
    type:'task',
    path:'/api/cockpit/tasks?kind=deferred&status=delivery_unknown&page_size=4',
    to:{name:'tasks',query:{tab:'deferred',status:'delivery_unknown'}}
  },
  {
    key:'calls',
    title:'已记录失败请求',
    type:'call',
    path:'/api/models/usage?status=failed&page_size=4',
    to:{name:'activity',query:{tab:'calls',status:'failed'}}
  },
]
const issues=reactive(Object.fromEntries(issueDefinitions.map(item=>[item.key,{data:null,error:'',loading:false,readAt:null}])))
const issueGuards=Object.fromEntries(issueDefinitions.map(item=>[item.key,useRequestGuard()]))
const readStats=useGuardedRead(statsGuard,statsLoading,error)
function load(){
  return readStats(()=>api('/api/overview/stats'),result=>{
    data.value=result
  })
}
async function loadIssue(item){
  const fresh=issueGuards[item.key](), source=issues[item.key]
  source.loading=true;
  source.error=''
  try{
    const result=await api(item.path);
    if(fresh()){
      source.data=result;
      source.readAt=Date.now()/1000
    }
  }
  catch(e){
    if(fresh())source.error=e.message
  }finally{
    if(fresh())source.loading=false
  }
}
async function refresh(){
  await Promise.all([load(),loadPlugins(),refreshStatus(),...issueDefinitions.map(loadIssue)])
}
const issuesKnown=computed(()=>Object.values(issues).every(item=>item.data&&!item.error&&!item.loading))
const hasIssues=computed(()=>Object.values(issues).some(item=>item.data?.total>0))
const issueLabel=(item,type)=>type==='job'?item.goal:type==='task'?item.description:`${item.purpose} · ${item.error_type || '请求失败'}`
// A first configuration needs to see, from one place, which dependency is
// actually missing and where it is filled in.  Every row below is derived from
// records already saved — the plugin list, the role projection and the scene
// counts — and none of them calls an external service, so refreshing the page
// never probes a source, sends a message or switches anything on.  The plugin
// list is fetched separately from the overview stats so a plugin read failure
// cannot blank the rest of the page.
const capabilities=ref(null),pluginError=ref(''),pluginLoading=ref(false)
const loading=computed(()=>statsLoading.value||pluginLoading.value||Object.values(issues).some(item=>item.loading))
const readPlugins=useGuardedRead(pluginGuard,pluginLoading,pluginError)
function loadPlugins(){
  return readPlugins(()=>api('/api/overview/capabilities'),result=>{
    capabilities.value=result
  })
}
const plugins=computed(()=>[...new Map((capabilities.value?.items||[])
  .flatMap(card=>card.plugins).filter(plugin=>plugin.state!=='absent')
  .map(plugin=>[plugin.id,plugin])).values()])
const pluginGaps=computed(()=>{
  const rows=[]
  for(const plugin of plugins.value){
    const reason=!plugin.enabled?null:!plugin.configured?'已保存启用，但缺全局参数':plugin.last_error&&['error','failed'].includes(plugin.state)?'加载或运行报错':!plugin.active_enabled?'已保存启用，但尚未装载':null
    if(reason)rows.push({id:plugin.id,name:plugin.name,reason})
  }
  return rows
})
const readiness=computed(()=>{
  const rows=[],status=app.status,stats=data.value?.stats
  if(status){
    const missing=Object.entries(status.roles).filter(([,role])=>!role.ready)
      .map(([key,role])=>`${roleNames[key]}（${role.reason}）`)
    rows.push({key:'models',label:'模型角色',ok:!missing.length,
      text:missing.length?`还有 ${missing.length} 个角色不能用：${missing.join('、')}`:'对话、工作、维护三个角色都已就绪',
      to:{name:'models',query:{tab:'roles'}}})
    rows.push({key:'time',label:'业务时间',ok:!!status.business_timezone,
      text:status.business_timezone?`按 ${status.business_timezone} 解释日期与自然周`:'尚未填写时区；依赖时间口径的插件不能启用',
      to:{name:'settings',query:{tab:'time'}}})
    rows.push({key:'scenes',label:'可用群',ok:status.scene_counts.enabled>0,
      text:status.scene_counts.enabled?`已启用 ${status.scene_counts.enabled} 个群，其中 ${status.scene_counts.chat_enabled} 个开放普通聊天`:'还没有启用的群；在群聊里按群号填写本群设置',
      to:{name:'scenes'}})
  }
  if(stats)rows.push({key:'onebot',label:'OneBot 连接',ok:stats.websocket_connected,
    text:stats.websocket_connected?'连接已建立；每条消息是否送达仍看真实回执':'当前样本没有可确认的连接；输入处理与发送分别核对',
    to:{name:'settings',query:{tab:'connection'}}})
  const gaps=pluginGaps.value
  rows.push({key:'plugins',label:'插件参数',ok:!!capabilities.value&&!pluginError.value&&plugins.value.length>0&&!gaps.length,
    text:pluginError.value?`插件清单读取失败：${pluginError.value}`
      :!capabilities.value?'正在读取插件状态'
      :!plugins.value.length?'当前能力清单没有已装入的插件'
      :gaps.length?`${gaps.length} 个插件需要处理：${gaps.slice(0,3).map(item=>`${item.name}（${item.reason}）`).join('、')}${gaps.length>3?` 等 ${gaps.length} 项`:''}`
      :`已保存启用的插件没有上述加载缺项；${plugins.value.filter(item=>!item.enabled).length} 个未启用项保持可选，不代表运行故障`,
    to:{name:'plugins'}})
  return rows
})
onMounted(refresh)
</script>
<template>
  <div class="page-stack">
    <PageHeader title="运行概览" description="先看当前连接与待处理事项，再查看对话、工作和持久资料。">
      <v-btn :prepend-icon="mdiRefresh" variant="outlined" :loading="loading" @click="refresh">刷新</v-btn>
    </PageHeader>
    <v-alert v-if="error" type="error" variant="tonal">读取失败：{{ error }}<span v-if="data">。下方保留 {{ fmtTime(data.sampled_at) }} 的结果。</span>
    </v-alert>
    <v-progress-linear v-if="loading && !data" indeterminate color="primary" />
    <v-card v-if="readiness.length" class="readiness-card">
      <v-card-text>
        <div class="section-heading">
          <div>
            <h2>当前依赖</h2>
            <p class="muted">下面每一项都来自已保存的记录，刷新不会去请求外部服务、发送消息或打开任何能力；点击跳到填写位置。</p>
          </div>
          <v-btn
            :prepend-icon="mdiRefresh"
            variant="text"
            color="primary"
            size="small"
            @click="refresh"
          >重新读取</v-btn>
        </div>
        <ul class="readiness-list">
          <li v-for="row in readiness" :key="row.key" class="readiness-row">
            <span class="readiness-mark" :class="{ok:row.ok}">{{ row.ok?'已记录条件':'待核对' }}</span>
            <div class="readiness-body">
              <strong>{{ row.label }}</strong>
              <span class="muted">{{ row.text }}</span>
            </div>
            <router-link :to="row.to">查看对应配置</router-link>
          </li>
        </ul>
      </v-card-text>
    </v-card>
    <v-expansion-panels v-if="capabilities">
      <v-expansion-panel title="当前能力与验证记录">
        <v-expansion-panel-text>
          <p class="muted mb-4">
            {{ capabilities.evidence_note }} · 能力样本 {{ fmtTime(capabilities.sampled_at) }}{{ pluginError ? '（刷新失败，保留旧结果）' : '' }}
          </p>
          <CapabilityCards :data="capabilities" compact />
          <v-btn class="mt-4" variant="tonal" :to="{name:'capabilities'}">选择群与发起者，查看完整能力状态</v-btn>
        </v-expansion-panel-text>
      </v-expansion-panel>
    </v-expansion-panels>
    <v-card>
      <v-card-text>
        <div class="section-heading">
          <div>
            <h2>需要留意</h2>
            <p class="muted">各列表独立读取，每项最多展示四条。未知送达不自动重发；历史请求失败不等于当前仍有故障，普通沉默不计为异常。</p>
          </div>
          <v-chip v-if="issuesKnown&&!hasIssues" variant="tonal" size="small">本次各列表均无相关记录</v-chip>
        </div>
        <div class="issues-grid">
          <section v-for="source in issueDefinitions" :key="source.key">
            <div class="issue-title">
              <strong>{{ source.title }}</strong>
              <span>{{ issues[source.key].data?.total ?? '未读取' }}</span>
            </div>
            <v-progress-linear
              v-if="issues[source.key].loading"
              indeterminate
              :aria-label="'正在读取' + source.title"
            />
            <v-alert v-if="issues[source.key].error" type="error" variant="tonal" density="compact">
              {{ issues[source.key].error }}<v-btn
                size="small"
                variant="text"
                :loading="issues[source.key].loading"
                @click="loadIssue(source)"
              >重读此列表</v-btn>
            </v-alert>
            <p v-if="issues[source.key].readAt" class="muted">
              {{ issues[source.key].error ? '保留上次读取' : '读取于' }}
              {{ fmtTime(issues[source.key].readAt) }}
            </p>
            <div
              v-for="item in issues[source.key].data?.items || []"
              :key="item.id"
              class="issue-item"
            >
              <EntityLink
                :type="source.type"
                :id="item.id"
                :scene-id="item.scene_id"
                :task-kind="item.payload?.kind"
                :label="issueLabel(item,source.type)"
                :copyable="false"
              />
            </div>
            <p v-if="issues[source.key].data?.total===0&&!issues[source.key].error" class="muted">本次范围没有此类记录。</p>
            <p
              v-if="!issues[source.key].data&&!issues[source.key].loading&&!issues[source.key].error"
              class="muted"
            >尚未取得此列表，不能判断数量。</p>
            <router-link :to="source.to">打开对应列表</router-link>
          </section>
        </div>
      </v-card-text>
    </v-card>
    <template v-if="data">
      <div class="connection-grid">
        <v-card class="connection-card">
          <v-card-text>
            <div class="eyebrow">ONEBOT 连接</div>
            <div class="connection-value">
              <span class="connection-dot" :class="{connected:data.stats.websocket_connected}"></span>{{ data.stats.websocket_connected?'已连接':'未连接' }}
            </div>
            <p class="muted">
              {{ data.stats.websocket_connected?'连接已建立；送达仍以每条回执为准。':'当前没有可确认的 OneBot 连接。' }}
            </p>
            <v-btn
              variant="text"
              color="primary"
              size="small"
              :append-icon="mdiArrowRight"
              :to="{name:'settings',query:{tab:'connection'}}"
            >连接设置</v-btn>
          </v-card-text>
        </v-card>
        <v-card class="connection-card">
          <v-card-text>
            <div class="eyebrow">发送方式</div>
            <div class="connection-value">{{ data.stats.shadow_mode?'Shadow 观察':'按各群规则发送' }}</div>
            <p class="muted">
              {{ data.stats.shadow_mode?'表达只记录候选，不实际发送。':'通过 Gate 的表达按本群聊天、命令或公告资格投递。' }}
            </p>
            <p v-if="app.status" class="muted">已启用 {{ app.status.scene_counts.enabled }} 个群，其中 {{ app.status.scene_counts.chat_enabled }} 个开放普通聊天。</p>
            <v-btn
              variant="text"
              color="primary"
              size="small"
              :append-icon="mdiArrowRight"
              :to="{name:'settings',query:{tab:'delivery'}}"
            >发送设置</v-btn>
          </v-card-text>
        </v-card>
        <v-card class="connection-card">
          <v-card-text>
            <div class="eyebrow">运行记录与用量</div>
            <div class="connection-value">费用未核实</div>
            <p class="muted">所有用途统一查看，未知 usage 单独保留。</p>
            <v-btn
              variant="text"
              color="primary"
              size="small"
              :append-icon="mdiArrowRight"
              :to="{name:'activity',query:{tab:'calls'}}"
            >打开调用账</v-btn>
          </v-card-text>
        </v-card>
      </div>
      <section v-if="app.status" class="role-grid">
        <v-card v-for="(role,key) in app.status.roles" :key="key">
          <v-card-text>
            <div class="role-heading">
              <h2>{{ roleNames[key] }}</h2>
              <StatusBadge
                domain="provider"
                :status="role.ready?'ready':role.configured?'unavailable':'missing'"
              />
            </div>
            <p class="role-model">{{ role.profile?.model || '尚未指定模型' }}</p>
            <p class="muted">
              {{ role.profile?`${role.profile.provider_id} · ${role.profile.reasoning_effort}`:role.reason }}
            </p>
            <router-link :to="{name:'models',query:{tab:'roles'}}">查看角色配置</router-link>
          </v-card-text>
        </v-card>
      </section>
      <div class="overview-bottom">
        <v-card>
          <v-card-text>
            <div class="section-heading">
              <div>
                <h2><v-icon :icon="mdiForumOutline" size="20" /> 本次运行中的互动</h2>
                <p class="muted">
                  {{ fmtTime(data.runtime_interval.since) }} 至 {{ fmtTime(data.runtime_interval.until) }}
                </p>
              </div>
            </div>
            <div class="interaction-stats">
              <div><strong>{{ data.social_metrics.human_messages }}</strong><span>收到消息</span></div>
              <div>
                <strong>{{ data.social_metrics.social_cognition }}</strong>
                <span>对话轮次</span>
              </div>
              <div>
                <strong>{{ data.social_metrics.intentional_silence }}</strong>
                <span>模型沉默</span>
              </div>
              <div>
                <strong>{{ data.social_metrics.visible_messages }}</strong>
                <span>实际发言</span>
              </div>
            </div>
            <p class="muted footnote">这些内存计数随本次进程运行累计，与持久调用账的历史范围不同。</p>
          </v-card-text>
        </v-card>
        <v-card>
          <v-card-text>
            <h2><v-icon :icon="mdiDatabaseOutline" size="20" /> 持久资料</h2>
            <div class="persistent-stats">
              <span>原始事件<strong>{{ data.stats.total_events }}</strong></span>
              <span>已有场景<strong>{{ data.stats.active_scenes }}</strong></span>
              <span>有效认识<strong>{{ data.stats.memory_beliefs_count }}</strong></span>
              <span>等待回应<strong>{{ data.stats.active_open_loops }}</strong></span>
            </div>
          </v-card-text>
        </v-card>
      </div>
      <v-card>
        <v-card-text>
          <div class="section-heading">
            <div>
              <h2>最近活动场景</h2>
              <p class="muted">显示最近 {{ Math.min(6,data.scenes.length) }} 个场景；完整列表与本群设置在群聊工作台中浏览。</p>
            </div>
            <v-btn
              variant="text"
              color="primary"
              :append-icon="mdiArrowRight"
              :to="{name:'scenes'}"
            >打开群聊工作台</v-btn>
          </div>
          <div class="recent-scene-list">
            <router-link
              v-for="scene in data.scenes.slice(0,6)"
              :key="scene.scene_id"
              class="recent-scene"
              :to="{name:'scene',params:{sceneId:scene.scene_id}}"
            >
              <div class="scene-initial">{{ scene.scene_id.startsWith('private:')?'私':'群' }}</div>
              <div class="scene-summary">
                <strong>{{ scene.display_name || sceneName(scene.scene_id) }}</strong>
                <span>{{ scene.participant_count }} 位参与者 · {{ scene.active_job_count }} 项工作</span>
              </div>
              <div class="scene-timing">
                <span v-if="scene.pending_wake_count" class="pending-count">
                  {{ scene.pending_wake_count }} 待处理</span>
                <time>{{ fmtTime(scene.last_event_at) }}</time>
              </div>
            </router-link>
            <p v-if="!data.scenes.length" class="empty-state">还没有保存的场景消息</p>
          </div>
        </v-card-text>
      </v-card>
      <p class="sample-note">
        <v-icon :icon="mdiClockOutline" size="14" /> 读取于 {{ fmtTime(data.sampled_at) }}
      </p>
    </template>
  </div>
</template>
<style scoped>
.connection-grid,.role-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:20px}
.connection-card .v-card-text{padding:24px}
.eyebrow{font-size:11px;font-weight:650;letter-spacing:.06em;color:var(--muted)}
.connection-value{font-size:24px;font-weight:650;letter-spacing:-.03em;margin:16px 0 10px;display:flex;align-items:center;gap:10px}
.connection-dot{width:10px;height:10px;border-radius:50%;background:var(--status-idle)}
.connection-dot.connected{background:var(--success)}
.connection-card p{min-height:44px;line-height:1.7;font-size:13px;margin-bottom:12px}
h2{font-size:16px;line-height:1.4;font-weight:650;margin:0 0 8px}
.section-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:20px}
.section-heading p{font-size:12px;margin:0;line-height:1.6}
.issues-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:24px}
.issues-grid>section{min-width:0}
.issue-title{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;font-size:13px}
.issue-title span{background:var(--chip-bg);padding:0 8px;border-radius:5px;font-size:12px}
.issue-item{padding-block:8px;border-top:1px solid var(--line);font-size:13px}
.issues-grid p,.issues-grid a{font-size:12px}
.role-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:8px;flex-wrap:wrap}
.role-model{font-size:16px;font-weight:600;margin:16px 0 6px;overflow-wrap:anywhere}
.role-grid .muted,.role-grid a{font-size:12px}
.overview-bottom{display:grid;grid-template-columns:1.4fr 1fr;gap:20px}
.interaction-stats{display:grid;grid-template-columns:repeat(4,1fr);gap:16px}
.interaction-stats strong{display:block;font-size:28px;font-weight:650}
.interaction-stats span{font-size:12px;color:var(--muted)}
.persistent-stats{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:24px}
.persistent-stats span{display:flex;justify-content:space-between;font-size:13px}
.footnote{font-size:11px;margin:20px 0 0}
.recent-scene{display:flex;align-items:center;gap:12px;padding:14px 0;border-top:1px solid var(--line);color:var(--ink);text-decoration:none}
.recent-scene:hover{color:var(--primary)}
.scene-initial{display:grid;place-items:center;width:34px;height:34px;border-radius:9px;background:var(--scene-avatar-bg);color:var(--scene-avatar-text);flex:none;font-size:12px}
.scene-summary{min-width:0;flex:1}
.scene-summary strong{font-size:13px;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.scene-summary span,.scene-timing{font-size:11px;color:var(--muted)}
.scene-timing{display:grid;gap:4px;text-align:right}
.pending-count{color:var(--info)}
.sample-note{font-size:11px;color:var(--muted);margin:0;display:flex;align-items:center;gap:6px}
.readiness-list{list-style:none;margin:0;padding:0}
.readiness-row{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:14px;align-items:baseline;padding:12px 0;border-top:1px solid var(--line)}
.readiness-row:first-child{border-top:0}
.readiness-mark{font-size:11px;font-weight:650;padding:2px 8px;border-radius:5px;background:var(--warning-bg);color:var(--warning-text);white-space:nowrap}
.readiness-mark.ok{background:var(--success-bg);color:var(--success)}
.readiness-body{min-width:0;font-size:13px;line-height:1.7}
.readiness-body strong{display:block}
.readiness-body .muted{font-size:12px}
.readiness-row a{font-size:12px;white-space:nowrap}
@media(max-width:1200px){
  .connection-grid{grid-template-columns:1fr 1fr}
  .connection-grid>:last-child{grid-column:1/-1}
  .role-grid{gap:12px}
  .overview-bottom{grid-template-columns:1fr}
}
@media(max-width:700px){
  .connection-grid,.role-grid,.issues-grid{grid-template-columns:1fr}
  .connection-grid>:last-child{grid-column:auto}
  .connection-card p{min-height:0}
  .connection-card .v-card-text{padding:20px}
  .connection-value{font-size:22px}
  .interaction-stats{grid-template-columns:1fr 1fr}
  .scene-timing time{display:none}
  .issues-grid{gap:20px}
  .persistent-stats{grid-template-columns:1fr 1fr}
}
</style>
