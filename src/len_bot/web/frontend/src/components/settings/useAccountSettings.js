import { ref } from 'vue'
import { api } from '../../api.js'
import { logout, useAuth } from '../../composables/useAuth.js'

const RESET_HELP = `Reset 是独立的破坏性管理动作，需要当次明确授权。

会删除：全部群聊和私聊的原话与会话、摘要与自动认识、自动技能与其候选、工作与检查点、任务与等待、工具资料、调用账、聊天图片和场景上下文。执行前先停止认知、维护、工作和投递，并在管理记录里追加一条操作事件。

会保留：登录、根配置、人工表达样例、运营表情库及其来源、人格、Shadow、QQ 回复白名单、各群设置与能力授予。人工样例只重置使用计数。

不处理：usage_reservations、execution_runs、execution_events 不在清理表列表内。既有额度预占和执行记录会留下，页面也不能证明外部容器已经结束；这些行需要另行核对归属与处置。`

// Login account, sign-out and the destructive data reset.
export function useAccountSettings({
  busy, error, message, beginOperation, load, confirmLeave, router, leavingAfterLogout
}) {
  const me = ref(null)
  const passwords = ref({current_password:'',new_password:''})
  const resetConfirm = ref(false)
  async function changePassword() {
    if(busy.value)return
    const fresh = beginOperation('password')
    try{
      await api('/api/auth/change_password',{method:'POST',body:JSON.stringify(passwords.value)});
      if(!fresh())return;
      passwords.value={current_password:'',new_password:''};
      message.value='访问密码已修改';
      await load()
    }
    catch(e){
      if(fresh())error.value=e.message
    }finally{
      if(fresh())busy.value=''
    }
  }
  async function signOut() {
    if(busy.value||!confirmLeave())return
    const fresh = beginOperation('logout')
    try{
      await logout();
      if(useAuth().status!=='unauthenticated')return;
      leavingAfterLogout.value=true;
      await router.replace({name:'login'})
    }catch(e){
      if(fresh())error.value=e.message
    }finally{
      if(fresh())busy.value=''
    }
  }
  async function resetData(){
    if(busy.value)return
    const fresh = beginOperation('reset')
    try{
      await api('/api/settings/reset',{method:'POST'});
      if(!fresh())return;
      resetConfirm.value=false;
      message.value='对话数据已按上面列出的范围清空；配置与运营资料保留，额度预占和执行记录仍待单独核对';
      await load()
    }
    catch(e){
      if(fresh())error.value=e.message
    }finally{
      if(fresh())busy.value=''
    }
  }
  return {
    RESET_HELP, me, passwords, resetConfirm, changePassword, signOut, resetData
  }
}
