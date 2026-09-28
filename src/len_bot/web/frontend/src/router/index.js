import { createRouter, createWebHashHistory } from 'vue-router'
import { ensureAuth, useAuth, clearAuth } from '../composables/useAuth.js'
import { setUnauthorizedHandler } from '../api.js'
import { internalPath } from './navigation.js'
import { sceneVisit } from '../composables/sceneVisits.js'
import { hostPageNames, hostPaths } from './hostNavigation.js'

export function returnPath(value) {
  const context = useAuth().panelContext
  if (context?.mode === 'isolated') return internalPath(value)==='/chat-test/settings' ? '/chat-test/settings' : '/chat-test'
  if (context?.mode === 'isolated-multi') {
    const path = internalPath(value)
    return path && hostPaths
      .includes(path.split(/[?#]/, 1)[0]) ? path : '/host/overview'
  }
  return internalPath(value) || context?.home || '/overview'
}
const router = createRouter({
  history:createWebHashHistory(),
  routes:[
    {path:'/',redirect:{name:'overview'}},
    { path:'/host/permissions', name:'host-permissions', component:()=>import('../views/HostPermissionsView.vue'), meta:{title:'设置 · 权限'} },
    { path:'/host/chat-test', name:'host-trials', component:()=>import('../views/HostTrialsView.vue'), meta:{title:'对话测试'} },
    { path:'/host/logs/system', name:'host-system-logs', component:()=>import('../views/HostSystemLogsView.vue'), meta:{title:'日志 · 系统'} },
    { path:'/host/logs', name:'host-logs', component:()=>import('../views/HostView.vue'), meta:{title:'日志'} },
    { path:'/host/scenes/learning', name:'host-scene-learning', component:()=>import('../views/HostLearningView.vue'), meta:{title:'群聊 · 学习'} },
    {
      path:'/host/tasks',
      name:'host-tasks',
      component:()=>import('../views/HostTasksView.vue'),
      meta:{title:'独立任务'}
    },
    {
      path:'/host/schedules',
      name:'host-schedules',
      component:()=>import('../views/HostSchedulesView.vue'),
      meta:{title:'场景安排'}
    },
    {
      path:'/host/memory',
      name:'host-memory',
      component:()=>import('../views/HostMemoryView.vue'),
      meta:{title:'宿主认识与记忆'}
    },
    {
      path:'/host/learning',
      name:'host-learning',
      component:()=>import('../views/HostLearningView.vue'),
      meta:{title:'群聊表达学习'}
    },
    {
      path:'/host/overview',
      name:'host-overview',
      component:()=>import('../views/HostOverviewView.vue'),
      meta:{title:'首页'}
    },
    {
      path:'/host/capabilities',
      name:'host-capabilities',
      component:()=>import('../views/HostCapabilitiesView.vue'),
      meta:{title:'能力'}
    },
    {
      path:'/host/models',
      name:'host-models',
      component:()=>import('../views/HostModelsView.vue'),
      meta:{title:'模型'}
    },
    {
      path:'/host/history',
      name:'host-history',
      component:()=>import('../views/HostHistoryView.vue'),
      meta:{title:'群聊 · 大脑'}
    },
    {
      path:'/host/system',
      name:'host-system',
      component:()=>import('../views/HostSystemView.vue'),
      meta:{title:'设置'}
    },
    {
      path:'/host/persona',
      name:'host-persona',
      component:()=>import('../views/HostPersonaView.vue'),
      meta:{title:'角色'}
    },
    {
      path:'/host/settings',
      name:'host-settings',
      component:()=>import('../views/HostSettingsView.vue'),
      meta:{title:'群聊 · 设置'}
    },
    {
      path:'/host',
      name:'host',
      component:()=>import('../views/HostView.vue'),
      meta:{title:'群聊 · 消息'}
    },
    {
      path:'/login',
      name:'login',
      component:()=>import('../views/LoginView.vue'),
      meta:{public:true,title:'登录'}
    },
    {
      path:'/chat-test/settings',
      name:'chat-test-settings',
      component:()=>import('../views/ChatTestSettingsView.vue'),
      meta:{title:'场景与角色'}
    },
    {
      path:'/chat-test',
      name:'chat-test',
      component:()=>import('../views/ChatTestView.vue'),
      meta:{title:'对话测试'}
    },
    {
      path:'/overview',
      name:'overview',
      component:()=>import('../views/OverviewView.vue'),
      meta:{title:'运行概览'}
    },
    {
      path:'/groups',
      name:'groups',
      redirect:to=>({
        name:'scenes',
        query:{
          ...to.query,
          type:to.query.type || 'group',
          query:to.query.query || to.query.search,
          search:undefined
        }
      })
    },
    {
      path:'/groups/:sceneId',
      name:'group',
      redirect:to=>({
        name:'scene',
        params:to.params,
        query:{...to.query,tab:'settings',event:undefined}
      })
    },
    {
      path:'/scenes',
      name:'scenes',
      component:()=>import('../views/ScenesView.vue'),
      meta:{title:'群聊工作台'}
    },
    {
      path:'/scenes/:sceneId',
      name:'scene',
      component:()=>import('../views/ScenesView.vue'),
      meta:{title:'群聊工作台'}
    },
    {
      path:'/jobs',
      name:'jobs',
      component:()=>import('../views/JobsView.vue'),
      meta:{title:'信息工作'}
    },
    {
      path:'/jobs/:jobId',
      name:'job',
      component:()=>import('../views/JobsView.vue'),
      meta:{title:'工作详情'}
    },
    {
      path:'/tasks',
      name:'tasks',
      component:()=>import('../views/TasksLoopsView.vue'),
      meta:{title:'提醒与等待'}
    },
    {
      path:'/memories',
      name:'memories',
      component:()=>import('../views/MemoryView.vue'),
      meta:{title:'认识与记忆'}
    },
    {
      path:'/skills',
      name:'skills',
      component:()=>import('../views/SkillsView.vue'),
      meta:{title:'程序性技能'}
    },
    {
      path:'/media',
      name:'media',
      component:()=>import('../views/MediaView.vue'),
      meta:{title:'媒体与素材'}
    },
    {
      path:'/models',
      name:'models',
      component:()=>import('../views/ModelsView.vue'),
      meta:{title:'模型配置'}
    },
    {
      path:'/agent/capabilities',
      name:'capabilities',
      component:()=>import('../views/CapabilitiesView.vue'),
      meta:{title:'工具能力'}
    },
    {
      path:'/plugins',
      name:'plugins',
      component:()=>import('../views/PluginsView.vue'),
      meta:{title:'插件与能力'}
    },
    {
      path:'/agent/settings',
      name:'agent-settings',
      component:()=>import('../views/AgentSettingsView.vue'),
      meta:{title:'人格与参与'}
    },
    {
      path:'/settings',
      name:'settings',
      component:()=>import('../views/SettingsView.vue'),
      meta:{title:'系统设置'}
    },
    {
      path:'/activity',
      name:'activity',
      component:()=>import('../views/ActivityView.vue'),
      meta:{title:'运行记录'}
    },
    {
      path:'/:pathMatch(.*)*',
      name:'not-found',
      component:()=>import('../views/NotFoundView.vue'),
      meta:{title:'页面未找到'}
    },
  ],
  scrollBehavior(to,from,savedPosition) {
    if(['scene','scenes'].includes(to.name) && sceneVisit(to))return false
    if(savedPosition)return savedPosition
    if(to.name===from.name && (to.query.id!==from.query.id || to.query.result!==from.query.result))return false
    if(to.name==='scene' && from.name==='scene' && to.params.sceneId===from.params.sceneId)return false
    return {top:0}
  },
})
router.beforeEach(async to=>{
  await ensureAuth()
  const auth=useAuth()
  if(auth.status==='error')return true
  if(auth.panelContext?.mode==='isolated-multi'){
    const hostPages=hostPageNames
    if(!to.meta.public && auth.status!=='authenticated')return {name:'login',query:{redirect:hostPages.includes(to.name)?to.fullPath:'/host/overview'}}
    if(to.name==='login' && auth.status==='authenticated')return returnPath(to.query.redirect)
    if(to.name!=='login' && !hostPages.includes(to.name))return {name:'host-overview'}
    return true
  }
  if(auth.panelContext?.mode==='isolated'){
    if(!to.meta.public && auth.status!=='authenticated')return {name:'login',query:{redirect:to.name==='chat-test-settings'?to.fullPath:'/chat-test'}}
    if(to.name==='login' && auth.status==='authenticated')return returnPath(to.query.redirect)
    if(to.name!=='login' && to.name!=='chat-test' && to.name!=='chat-test-settings')return {name:'chat-test'}
    return true
  }
  if([...hostPageNames,'chat-test','chat-test-settings'].includes(to.name))return {name:'overview'}
  if(to.name==='settings'&&['persona','attention','time'].includes(to.query.tab))return {name:'agent-settings',query:to.query}
  if(!to.meta.public && auth.status!=='authenticated')return {name:'login',query:{redirect:to.fullPath}}
  if(to.name==='login' && auth.status==='authenticated')return returnPath(to.query.redirect)
})
router.afterEach(to=>{
  document.title=`${to.meta.title || '管理中心'} · LenBot`
})
setUnauthorizedHandler(()=>{
  const wasAuthenticated=useAuth().status==='authenticated'
  clearAuth()
  if(wasAuthenticated && router.currentRoute.value.name!=='login')router.replace({name:'login',query:{redirect:router.currentRoute.value.fullPath}})
})
export default router
