import { createRouter, createWebHashHistory } from 'vue-router'
import { ensureAuth, useAuth, clearAuth } from '../composables/useAuth.js'
import { setUnauthorizedHandler } from '../api.js'
import { internalPath } from './navigation.js'
import { hostPageNames, hostPaths } from './hostNavigation.js'

const trialPageNames = ['chat-test', 'chat-test-settings']
const trialPaths = ['/chat-test', '/chat-test/settings']

export function returnPath(value) {
  const context = useAuth().panelContext
  const path = internalPath(value)
  const paths = context.mode === 'isolated' ? trialPaths : hostPaths
  return path && paths.includes(path.split(/[?#]/, 1)[0]) ? path : context.home
}
const router = createRouter({
  history:createWebHashHistory(),
  routes:[
    {path:'/',redirect:{name:'host-overview'}},
    { path:'/host/chat-test', name:'host-trials', component:()=>import('../host/pages/trial/TrialPage.vue'), meta:{title:'对话测试'} },
    { path:'/host/logs', name:'host-logs', component:()=>import('../host/pages/logs/LogsPage.vue'), meta:{title:'日志',scene:route=>route.query.tab!=='system'} },
    { path:'/host/scenes', name:'host-scenes', component:()=>import('../host/pages/scenes/ScenesPage.vue'), meta:{title:'群聊',scene:true,sceneSwitch:false} },
    {
      path:'/host/tasks',
      name:'host-tasks',
      component:()=>import('../host/pages/tasks/TasksPage.vue'),
      meta:{title:'任务',scene:true}
    },
    { path:'/host/resources', name:'host-resources', component:()=>import('../host/pages/resources/ResourcesPage.vue'), meta:{title:'资源',scene:true} },
    {
      path:'/host/memory',
      name:'host-memory',
      component:()=>import('../host/pages/memory/MemoryPage.vue'),
      meta:{title:'记忆',scene:route=>route.query.tab!=='settings'}
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
      meta:{title:'能力',scene:route=>['tools',undefined].includes(route.query.tab),sceneMode:'role'}
    },
    {
      path:'/host/plugins',
      name:'host-plugins',
      component:()=>import('../host/pages/capabilities/PluginsPage.vue'),
      meta:{title:'插件'}
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
      meta:{title:'角色',scene:true,sceneMode:'role'}
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
      path:'/:pathMatch(.*)*',
      name:'not-found',
      component:()=>import('../views/NotFoundView.vue'),
      meta:{title:'页面未找到'}
    },
  ],
  scrollBehavior(to,from,savedPosition) {
    if(savedPosition)return savedPosition
    if(to.name===from.name && (to.query.id!==from.query.id || to.query.result!==from.query.result))return false
    return {top:0}
  },
})
router.beforeEach(async to => {
  await ensureAuth()
  const auth = useAuth()
  if (auth.status === 'error') return true
  const pages = auth.panelContext.mode === 'isolated' ? trialPageNames : hostPageNames
  if (!to.meta.public && auth.status !== 'authenticated') {
    return { name: 'login', query: { redirect: returnPath(to.fullPath) } }
  }
  if (to.name === 'login' && auth.status === 'authenticated') return returnPath(to.query.redirect)
  if (!to.meta.public && to.name !== 'not-found' && !pages.includes(to.name)) return auth.panelContext.home
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
