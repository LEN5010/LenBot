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
const groups=[
  {label:'运行',items:[['overview','运行概览',mdiViewDashboardOutline],['scenes','场景消息',mdiForumOutline],['jobs','信息工作',mdiBriefcaseSearchOutline],['tasks','提醒与等待',mdiCalendarClockOutline]]},
  {label:'资料',items:[['memories','认识与记忆',mdiBookOpenPageVariantOutline],['skills','程序性技能',mdiLightbulbOutline],['media','图片与表情',mdiImageOutline]]},
  {label:'配置',items:[['models','模型',mdiChip],['plugins','插件与能力',mdiPuzzleOutline],['settings','系统设置',mdiCogOutline]]},
  {label:'排查',items:[['activity','运行记录',mdiChartTimelineVariant]]},
]
const active=computed(()=>route.name==='scene'?'scenes':route.name==='job'?'jobs':route.name)
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
    <nav class="nav-groups"><div v-for="group in groups" :key="group.label"><p class="nav-section">{{ group.label }}</p><v-list nav density="compact"><v-list-item v-for="[name,label,icon] in group.items" :key="name" :to="{name}" :active="active===name" color="primary" :prepend-icon="icon" :title="label" /></v-list></div></nav>
    <template #append><div class="navigation-footer"><v-btn :prepend-icon="mdiLogout" block variant="text" :loading="busy" @click="exit">退出登录</v-btn></div></template>
  </v-navigation-drawer>
  <v-app-bar flat :height="64" class="app-toolbar">
    <v-btn v-if="mobile" :icon="mdiMenu" variant="text" aria-label="打开导航" @click="drawer=true" />
    <v-app-bar-title><span class="toolbar-title">{{ route.meta.title }}</span></v-app-bar-title>
    <v-menu location="bottom end" :close-on-content-click="false">
      <template #activator="{props}"><v-btn v-bind="props" variant="text" :append-icon="mdiChevronDown" class="toolbar-status" aria-label="查看当前运行状态"><span class="status-indicator" :class="{healthy:!app.error && app.status?.running}"></span>{{ statusText }}</v-btn></template>
      <v-card width="340" max-width="calc(100vw - 32px)"><v-card-title>当前状态</v-card-title><v-card-text class="status-details">
        <v-alert v-if="app.error" type="error" variant="tonal">刷新失败：{{ app.error }}。下方为上次读取结果。</v-alert>
        <template v-if="app.status"><p>运行时：{{ app.status.running?'已启动':'已停止' }}</p><p>OneBot：{{ app.status.onebot.connected?'已连接':'未连接' }}</p><p>发送方式：{{ app.status.shadow_mode?'Shadow · 仅记录候选':'实发 · 按各群规则' }}</p><p class="muted">采样于 {{ fmtTime(app.status.sampled_at) }}</p></template>
        <p v-else class="muted">尚无状态样本</p>
      </v-card-text><v-card-actions><v-btn :prepend-icon="mdiRefresh" :loading="app.loading" @click="refreshStatus">刷新状态</v-btn></v-card-actions></v-card>
    </v-menu>
    <v-divider vertical class="toolbar-divider" />
    <v-chip v-if="app.status" size="small" variant="tonal" :color="app.status.shadow_mode?'secondary':'warning'" class="mode-chip">{{ app.status.shadow_mode?'Shadow':'名单实发' }}</v-chip>
  </v-app-bar>
  <v-main tag="div"><main class="app-page" id="main-content"><v-alert v-if="error" type="error" variant="tonal" class="mb-4">{{ error }}</v-alert><slot /></main></v-main>
</template>
<style scoped>
.app-navigation{border-right:1px solid var(--line)}.app-brand{display:flex;gap:12px;align-items:center;padding:24px 20px 16px}.app-brand strong{display:block;letter-spacing:-.03em;font-size:21px}.app-brand span:not(.app-mark){font-size:12px;color:var(--muted)}.app-mark{width:38px;height:38px;border-radius:10px;display:block;flex:none}.nav-groups{padding:4px 12px 12px}.nav-section{font-size:11px;letter-spacing:.08em;color:var(--muted);margin:16px 12px 2px;font-weight:600}.nav-groups :deep(.v-list){padding-top:4px;padding-bottom:0}.nav-groups :deep(.v-list-item){min-height:42px;border-radius:8px;margin-bottom:3px}.nav-groups :deep(.v-list-item__prepend > .v-icon){margin-inline-end:14px;font-size:20px;opacity:.85}.nav-groups :deep(.v-list-item-title){font-size:14px}.navigation-footer{padding:12px;border-top:1px solid var(--line)}.app-toolbar{border-bottom:1px solid var(--line)}.toolbar-title{font-size:14px;font-weight:600}.toolbar-status{font-size:12px;color:var(--muted)}.status-indicator{width:6px;height:6px;border-radius:50%;background:#bd8340;display:inline-block;margin-right:8px}.status-indicator.healthy{background:#16845c}.mode-chip{margin-inline:16px 24px}.toolbar-divider{height:20px;align-self:center;margin-left:8px}.status-details p{margin:0 0 12px;line-height:1.6}.status-details{display:grid;gap:4px}
@media(max-width:600px){.mode-chip,.toolbar-divider{display:none}.toolbar-title{font-size:13px}.toolbar-status{margin-right:8px}.app-brand{padding:20px 16px}.app-brand .v-btn{margin-left:auto}}
</style>
