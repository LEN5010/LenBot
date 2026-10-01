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
    { path:'/host/chat-test', name:'host-trials', component:()=>import('../host/pages/trial/TrialPage.vue'), meta:{title:'对话测试'} },
    { path:'/host/logs', name:'host-logs', component:()=>import('../host/pages/logs/LogsPage.vue'), meta:{title:'日志'} },
    { path:'/host/scenes', name:'host-scenes', component:()=>import('../host/pages/scenes/ScenesPage.vue'), meta:{title:'群聊'} },
    {
      path:'/host/tasks',
      name:'host-tasks',
      component:()=>import('../host/pages/tasks/TasksPage.vue'),
      meta:{title:'任务'}
    },
    {
      path:'/host/memory',
      name:'host-memory',
      component:()=>import('../host/pages/memory/MemoryPage.vue'),
      meta:{title:'记忆'}
    },
    {
      path:'/host/overview',
      name:'host-overview',
      component:()=>import('../host/pages/home/HomePage.vue'),
      meta:{title:'首页'}
    },
    {
      path:'/host/capabilities',
      name:'host-capabilities',
      component:()=>import('../host/pages/capabilities/CapabilitiesPage.vue'),
      meta:{title:'能力'}
    },
    {
      path:'/host/models',
      name:'host-models',
      component:()=>import('../host/pages/models/ModelsPage.vue'),
      meta:{title:'模型'}
    },
    {
      path:'/host/system',
      name:'host-system',
      component:()=>import('../host/pages/settings/SettingsPage.vue'),
      meta:{title:'设置'}
    },
    {
      path:'/host/persona',
      name:'host-persona',
      component:()=>import('../host/pages/persona/PersonaPage.vue'),
      meta:{title:'角色'}
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
