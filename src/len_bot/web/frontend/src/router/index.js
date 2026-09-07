import { createRouter, createWebHashHistory } from 'vue-router'
import { ensureAuth, useAuth, clearAuth } from '../composables/useAuth.js'
import { setUnauthorizedHandler } from '../api.js'

export function returnPath(value) {
  return typeof value === 'string' && value.startsWith('/') && !value.startsWith('//') && !value.startsWith('/login') ? value : '/overview'
}
const router = createRouter({
  history:createWebHashHistory(),
  routes:[
    {path:'/',redirect:{name:'overview'}},
    {path:'/login',name:'login',component:()=>import('../views/LoginView.vue'),meta:{public:true,title:'登录'}},
    {path:'/overview',name:'overview',component:()=>import('../views/OverviewView.vue'),meta:{title:'运行概览'}},
    {path:'/scenes',name:'scenes',component:()=>import('../views/ScenesView.vue'),meta:{title:'场景消息'}},
    {path:'/scenes/:sceneId',name:'scene',component:()=>import('../views/ScenesView.vue'),meta:{title:'场景消息'}},
    {path:'/jobs',name:'jobs',component:()=>import('../views/JobsView.vue'),meta:{title:'信息工作'}},
    {path:'/jobs/:jobId',name:'job',component:()=>import('../views/JobsView.vue'),meta:{title:'工作详情'}},
    {path:'/tasks',name:'tasks',component:()=>import('../views/TasksLoopsView.vue'),meta:{title:'提醒与等待'}},
    {path:'/memories',name:'memories',component:()=>import('../views/MemoryView.vue'),meta:{title:'认识与记忆'}},
    {path:'/skills',name:'skills',component:()=>import('../views/SkillsView.vue'),meta:{title:'程序性技能'}},
    {path:'/media',name:'media',component:()=>import('../views/MediaView.vue'),meta:{title:'图片与表情'}},
    {path:'/models',name:'models',component:()=>import('../views/ModelsView.vue'),meta:{title:'模型配置'}},
    {path:'/plugins',name:'plugins',component:()=>import('../views/PluginsView.vue'),meta:{title:'插件与能力'}},
    {path:'/settings',name:'settings',component:()=>import('../views/SettingsView.vue'),meta:{title:'系统设置'}},
    {path:'/activity',name:'activity',component:()=>import('../views/ActivityView.vue'),meta:{title:'运行记录'}},
    {path:'/:pathMatch(.*)*',name:'not-found',component:()=>import('../views/NotFoundView.vue'),meta:{title:'页面未找到'}},
  ],
  scrollBehavior(to,from,savedPosition) {
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
  if(!to.meta.public && auth.status!=='authenticated')return {name:'login',query:{redirect:to.fullPath}}
  if(to.name==='login' && auth.status==='authenticated')return returnPath(to.query.redirect)
})
router.afterEach(to=>{document.title=`${to.meta.title || '管理中心'} · LenBot`})
setUnauthorizedHandler(()=>{
  const wasAuthenticated=useAuth().status==='authenticated'
  clearAuth()
  if(wasAuthenticated && router.currentRoute.value.name!=='login')router.replace({name:'login',query:{redirect:router.currentRoute.value.fullPath}})
})
export default router
