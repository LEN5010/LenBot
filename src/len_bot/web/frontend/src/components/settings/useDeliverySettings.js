import { ref } from 'vue'
import { api } from '../../api.js'

// The global Shadow switch.
export function useDeliverySettings({busy, error, message, beginOperation}) {
  const shadow = ref(null)
  async function toggleShadow() {
    if(busy.value||!shadow.value)return
    const enabled=!shadow.value.enabled
    if(!window.confirm(enabled?'开启 Shadow？保留原观察行为，但不再真实发送消息。':'关闭 Shadow？机器人将按各群当前启用、聊天、命令和公告配置实际发送。'))return
    const fresh = beginOperation('shadow')
    try{
      const result=await api('/api/cockpit/shadow/toggle',{method:'POST',body:JSON.stringify({enabled})})
      if (!fresh()) return
      shadow.value={...shadow.value,enabled:result.shadow_mode};
      message.value=result.shadow_mode?'已开启 Shadow，不实际发送':'已关闭 Shadow，按当前群规则发送'
    }catch(e){
      if(fresh())error.value=e.message
    }finally{
      if(fresh())busy.value=''
    }
  }
  return {
    shadow, toggleShadow
  }
}
