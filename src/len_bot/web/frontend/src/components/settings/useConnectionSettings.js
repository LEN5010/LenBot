import { computed, ref } from 'vue'
import { api } from '../../api.js'

// Connection tab state and operations. The page owns request guards, the
// shared read and conflict flow; this keeps what only the connection tab edits.
export function useConnectionSettings({
  busy, error, message, saveOutcomes, conflicts, beginOperation, saveDraft, saveError, load
}) {
  const connectionNeedsReadback = ref(false)
  const onebot = ref(null)
  const platform = ref(null)
  const connection = ref(null)
  const connectionOriginal = ref('')
  const connectionDirty = computed(()=>!!connection.value&&JSON.stringify(connection.value)!==connectionOriginal.value)
  const connectionForm = settings => ({connection_mode:settings.connection_mode,action_transport:settings.action_transport,
    ws_url:settings.ws_url,http_url:settings.http_url,host:settings.host,port:settings.port,access_token:'',access_token_action:'keep'})
  async function saveConnection() {
    if (busy.value || connectionNeedsReadback.value || saveOutcomes.value.connection||conflicts.entries.connection) return
    const fresh = beginOperation('connection')
    const progress={submitted:false,confirmed:false}
    try {
      const body = {...connection.value, access_token:connection.value.access_token.trim() || null}
      const result = await saveDraft('connection','/api/websocket/config',body,'POST',progress)
      if (!fresh()) return
      connectionNeedsReadback.value = true
      message.value = result.requires_restart ? `${result.message}；请手动重启服务后使用新连接配置` : result.message
      await load({accept:fresh})
    } catch (e) {
      await saveError(e,'connection',fresh,progress)
    } finally {
      if(fresh())busy.value = ''
    }
  }
  async function checkHttp() {
    if (busy.value) return
    const fresh = beginOperation('http')
    try {
      const result = await api('/api/websocket/test-http', {method:'POST'})
      if(fresh())message.value = `当前运行连接：${result.message}`
    } catch (e) {
      if(fresh())error.value = e.message
    } finally {
      if(fresh())busy.value = ''
    }
  }
  async function readVersion() {
    if (busy.value) return
    const fresh = beginOperation('version')
    platform.value = null
    try {
      const result = await api('/api/websocket/read-version', {method:'POST'})
      if(fresh())platform.value = result
    } catch (e) {
      if(fresh())error.value = e.message
    } finally {
      if(fresh())busy.value = ''
    }
  }
  return {
    onebot, platform, connection, connectionOriginal, connectionNeedsReadback, connectionDirty,
    connectionForm, saveConnection, checkHttp, readVersion
  }
}
