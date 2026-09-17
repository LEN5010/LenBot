<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'
import { mdiViewDashboardOutline, mdiForumOutline, mdiBriefcaseSearchOutline, mdiCalendarClockOutline, mdiBookOpenPageVariantOutline, mdiLightbulbOutline, mdiImageOutline, mdiChip, mdiPuzzleOutline, mdiCogOutline, mdiChartTimelineVariant, mdiMenu, mdiClose, mdiLogout, mdiRefresh, mdiChevronDown } from '@mdi/js'
import { useAppState,refreshStatus } from '../composables/useAppState.js'
import { logout } from '../composables/useAuth.js'
import { fmtTime } from '../api.js'
import markUrl from '../assets/lenbot-mark.svg'
const route=useRoute(),router=useRouter(),app=useAppState(),{mobile}=useDisplay()
const drawer=ref(!mobile.value),busy=ref(false),error=ref('')
const sections=[
  {id:'overview',label:'总览',icon:mdiViewDashboardOutline,to:{name:'overview'},items:[]},
  {id:'scenes',label:'群聊',icon:mdiForumOutline,to:{name:'scenes'},items:[]},
  {id:'agent',label:'Agent',icon:mdiChip,to:{name:'capabilities'},items:[
    ['工具能力',{name:'capabilities'}],['启用向导',{name:'setup'}],['人格与表达',{name:'agent-settings',query:{tab:'persona'}}],
    ['参与方式',{name:'agent-settings',query:{tab:'attention'}}],['睡眠与时间',{name:'agent-settings',query:{tab:'time'}}]]},
  {id:'work',label:'工作与交付',icon:mdiBriefcaseSearchOutline,to:{name:'jobs'},items:[
    ['信息工作与文件',{name:'jobs'}],['提醒与等待',{name:'tasks'}]]},
  {id:'memory',label:'记忆与资料',icon:mdiBookOpenPageVariantOutline,to:{name:'memories'},items:[
    ['认识与公共兴趣',{name:'memories'}],['方法技能',{name:'skills'}],['媒体资产',{name:'media'}]]},
  {id:'system',label:'系统',icon:mdiCogOutline,to:{name:'models'},items:[
    ['模型与额度',{name:'models'}],['连接与设置',{name:'settings',query:{tab:'connection'}}],
    ['插件与开发',{name:'plugins'}],['运行诊断',{name:'activity'}]]},
]
const activeSection=computed(()=>{
  if(route.name==='overview')return 'overview'
  if(['scenes','scene'].includes(route.name))return 'scenes'
  if(['capabilities','agent-settings','setup'].includes(route.name))return 'agent'
  if(['jobs','job','tasks'].includes(route.name))return 'work'
  if(['memories','skills','media'].includes(route.name))return 'memory'
  return 'system'
})
const section=computed(()=>sections.find(item=>item.id===activeSection.value))
const statusText=computed(()=>app.error?'状态读取失败':!app.status?'读取状态中':app.status.running?'运行时已启动':'运行时已停止')
watch(mobile,value=>drawer.value=!value)
watch(()=>route.fullPath,()=>{if(mobile.value)drawer.value=false})
function visible(){if(document.visibilityState==='visible')refreshStatus()}
onMounted(()=>{refreshStatus();document.addEventListener('visibilitychange',visible)})
onUnmounted(()=>document.removeEventListener('visibilitychange',visible))
async function exit(){busy.value=true;error.value='';try{await logout();await router.replace({name:'login'})}catch(e){error.value=e.message}finally{busy.value=false}}
</script>
<template>
  <v-navigation-drawer v-model="drawer" :permanent="!mobile" :temporary="mobile" :width="224" aria-label="主导航" class="app-navigation">
    <div class="app-brand"><img class="app-mark" :src="markUrl" alt="LenBot" /><div><strong>LenBot</strong><span>运行管理中心</span></div><v-btn v-if="mobile" :icon="mdiClose" variant="text" aria-label="关闭导航" @click="drawer=false" /></div>
    <nav class="nav-groups"><v-list nav density="compact"><v-list-item v-for="item in sections" :key="item.id" :to="item.to" :active="activeSection===item.id" color="primary" :prepend-icon="item.icon" :title="item.label" /></v-list></nav>
    <template #append><div class="navigation-footer"><v-btn :prepend-icon="mdiLogout" block variant="text" :loading="busy" @click="exit">退出登录</v-btn></div></template>
  </v-navigation-drawer>
  <v-app-bar flat :height="64" class="app-toolbar">
    <v-btn v-if="mobile" :icon="mdiMenu" variant="text" aria-label="打开导航" @click="drawer=true" />
    <v-app-bar-title><span class="toolbar-title">{{ route.meta.title }}</span></v-app-bar-title>
    <v-menu location="bottom end" :close-on-content-click="false">
      <template #activator="{props}"><v-btn v-bind="props" variant="text" :append-icon="mdiChevronDown" class="toolbar-status" aria-label="查看当前运行状态"><span class="status-indicator" :class="{healthy:!app.error && app.status?.running}"></span>{{ statusText }}</v-btn></template>
      <v-card width="340" max-width="calc(100vw - 32px)"><v-card-title>当前状态</v-card-title><v-card-text class="status-details">
        <v-alert v-if="app.error" type="error" variant="tonal">刷新失败：{{ app.error }}。下方为上次读取结果。</v-alert>
        <template v-if="app.status"><p>运行时：{{ app.status.running?'已启动':'已停止' }}</p><p>OneBot：{{ app.status.onebot.connected?'已连接':'未连接' }}</p>
          <p>发送方式：{{ app.status.shadow_mode?'Shadow · 仅记录候选':'按各群规则实际发送' }}</p>
          <p>业务时间：{{ app.status.business_timezone || '未填写' }}</p>
          <p class="muted">采样于 {{ fmtTime(app.status.sampled_at) }}</p></template>
        <p v-else class="muted">尚无状态样本</p>
      </v-card-text><v-card-actions><v-btn :prepend-icon="mdiRefresh" :loading="app.loading" @click="refreshStatus">刷新状态</v-btn></v-card-actions></v-card>
    </v-menu>
    <v-divider vertical class="toolbar-divider" />
    <v-chip v-if="app.status" size="small" variant="tonal" :color="app.status.shadow_mode?'secondary':'warning'" class="mode-chip">{{ app.status.shadow_mode?'Shadow':'按群规则发送' }}</v-chip>
  </v-app-bar>
  <v-main tag="div"><main class="app-page" id="main-content"><v-alert v-if="error" type="error" variant="tonal" class="mb-4">{{ error }}</v-alert><nav v-if="section?.items.length" class="section-navigation" aria-label="当前区域"><v-btn v-for="[label,to] in section.items" :key="label" :to="to" variant="text" size="small">{{ label }}</v-btn></nav><slot /></main></v-main>
</template>
<style scoped>
.section-navigation{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:24px;padding-bottom:12px;border-bottom:1px solid var(--line)}.app-navigation{border-right:1px solid var(--line)}.app-brand{display:flex;gap:12px;align-items:center;padding:24px 20px 16px}.app-brand strong{display:block;letter-spacing:-.03em;font-size:21px}.app-brand span:not(.app-mark){font-size:12px;color:var(--muted)}.app-mark{width:38px;height:38px;border-radius:10px;display:block;flex:none}.nav-groups{padding:4px 12px 12px}.nav-section{font-size:11px;letter-spacing:.08em;color:var(--muted);margin:16px 12px 2px;font-weight:600}.nav-groups :deep(.v-list){padding-top:4px;padding-bottom:0}.nav-groups :deep(.v-list-item){min-height:42px;border-radius:8px;margin-bottom:3px}.nav-groups :deep(.v-list-item__prepend > .v-icon){margin-inline-end:14px;font-size:20px;opacity:.85}.nav-groups :deep(.v-list-item-title){font-size:14px}.navigation-footer{padding:12px;border-top:1px solid var(--line)}.app-toolbar{border-bottom:1px solid var(--line)}.toolbar-title{font-size:14px;font-weight:600}.toolbar-status{font-size:12px;color:var(--muted)}.status-indicator{width:6px;height:6px;border-radius:50%;background:#bd8340;display:inline-block;margin-right:8px}.status-indicator.healthy{background:#16845c}.mode-chip{margin-inline:16px 24px}.toolbar-divider{height:20px;align-self:center;margin-left:8px}.status-details p{margin:0 0 12px;line-height:1.6}.status-details{display:grid;gap:4px}
@media(max-width:600px){.mode-chip,.toolbar-divider{display:none}.toolbar-title{font-size:13px}.toolbar-status{margin-right:8px}.app-brand{padding:20px 16px}.app-brand .v-btn{margin-left:auto}}
</style>
