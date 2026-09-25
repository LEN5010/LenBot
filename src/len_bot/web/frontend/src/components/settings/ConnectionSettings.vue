<script setup>
const props = defineProps({state: {type: Object, required: true}, page: {type: Object, required: true}})
const { busy, currentSaveOutcome, conflicts } = props.page
const { onebot, platform, connection, connectionNeedsReadback, connectionDirty, saveConnection,
  checkHttp, readVersion } = props.state
</script>
<template>
  <v-card class="pa-5 form-card">
    <div class="section-header">
      <h2>连接 OneBot</h2>
      <v-chip :color="onebot.connected?'success':'warning'">
        {{ onebot.connected?'已连接':'未连接' }}
      </v-chip>
    </div>
    <p class="muted my-3">
      {{ onebot.connected?'已取得 OneBot 连接。':onebot.active_connection?.connection_mode==='forward_ws'?'当前运行方式为主动连接；尚未连接，请核对最近错误。':onebot.active_connection?.connection_mode==='reverse_ws'?'当前运行方式等待 OneBot 主动接入。':'尚未取得当前运行连接方式；下方仅是已保存配置。' }}<span v-if="onebot.self_id"> 已识别账号：{{ onebot.self_id }}</span>
    </p>
    <v-alert v-if="onebot.last_error" type="error" variant="tonal" class="mb-4">
      {{ onebot.last_error }}
    </v-alert>
    <v-form
      :disabled="!!currentSaveOutcome||!!busy||connectionNeedsReadback"
      class="form-grid"
      @submit.prevent="saveConnection()"
    >
      <v-select
        v-model="connection.connection_mode"
        label="消息连接方式"
        :items="[{title:'主动连接 OneBot',value:'forward_ws'},{title:'等待 OneBot 连接',value:'reverse_ws'}]"
        class="wide"
      />
      <v-text-field
        v-if="connection.connection_mode==='forward_ws'"
        v-model="connection.ws_url"
        label="WebSocket 端点"
        placeholder="ws://127.0.0.1:13001/"
        class="wide"
        required
      />
      <template v-else>
        <v-text-field v-model="connection.host" label="监听地址" required />
        <v-text-field
          v-model.number="connection.port"
          type="number"
          min="1"
          max="65535"
          label="监听端口"
          required
        />
      </template>
      <v-select
        v-model="connection.action_transport"
        label="发送传输"
        :items="[{title:'使用 WebSocket',value:'websocket'},{title:'使用 HTTP',value:'http'}]"
      />
      <v-text-field
        v-model="connection.http_url"
        label="HTTP 接口地址"
        :required="connection.action_transport==='http'"
      />
      <v-select
        v-model="connection.access_token_action"
        :items="[{title:'保留当前令牌',value:'keep'},{title:'替换令牌',value:'replace'},{title:'清除令牌',value:'clear'}]"
        label="访问令牌操作"
        class="wide"
      />
      <v-text-field
        v-if="connection.access_token_action==='replace'"
        v-model="connection.access_token"
        type="password"
        autocomplete="new-password"
        label="访问令牌"
        :placeholder="onebot.access_token_set?'已保存，留空保留':'填写 OneBot 访问令牌'"
        class="wide"
      />
      <div class="actions wide">
        <v-btn
          type="submit"
          color="primary"
          :loading="busy==='connection'"
          :disabled="!!currentSaveOutcome||!!busy||connectionNeedsReadback||!!conflicts.entries.connection||!connectionDirty"
        >保存连接配置</v-btn>
        <v-btn variant="outlined" :loading="busy==='http'" :disabled="!!busy" @click="checkHttp">检查当前 HTTP 连接</v-btn>
        <v-btn
          variant="outlined"
          :loading="busy==='version'"
          :disabled="!!busy"
          @click="readVersion"
        >读取平台实现与版本</v-btn>
      </div>
      <p class="muted wide">连接配置保存后需手动重启服务生效。HTTP 检查只读取当前运行连接的状态。版本读取走当前发送传输，只读，不发送任何群消息。</p>
      <div v-if="platform" class="wide">
        <v-alert type="info" variant="tonal">
          <p>当前连接报告：{{ platform.app_name || '未提供实现名' }} · {{ platform.app_version || '未提供版本' }} · 协议 {{ platform.protocol_version ?? '未提供' }}（经 {{ platform.transport === 'http' ? 'HTTP' : 'WebSocket' }}）</p>
          <p v-if="!platform.configured_upload" class="mt-2">根配置尚未声明 onebot_file_upload；先核对当前实现与所选文件动作，版本仅作可选现场记录。</p>
          <template v-else>
            <p class="mt-2">已声明：{{ platform.configured_upload.implementation }} · 现场版本 {{ platform.configured_upload.version || '未记录' }} · 配置标签 {{ platform.configured_upload.protocol }} · 部署核验标记 {{ platform.configured_upload.deployment_verified }}
            </p>
            <p v-if="!platform.configured_upload.name_matches" class="mt-2">实现名与现场报告不一致，不要把配置标签改成另一实现。</p>
            <p v-else class="mt-2">实现名一致；文件动作和资产目录只读挂载仍需在主机侧核对，版本仅供现场记录。</p>
          </template>
          <p class="mt-2">{{ platform.message }}</p>
        </v-alert>
      </div>
    </v-form>
  </v-card>
</template>
<style scoped>
.form-card{max-width:1000px;width:100%}
.section-header{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}
.section-header h2,.form-card>h2{font-size:20px}
.form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;align-items:start}
.wide{grid-column:1/-1}
.form-grid>.v-btn{justify-self:start}
.actions,.meta,.delivery-state,.saved-scenes{display:flex;gap:8px 12px;flex-wrap:wrap;align-items:center}
.settings-view p{line-height:1.7}
@media(max-width:650px){
  .form-grid{grid-template-columns:minmax(0,1fr)}
  .section-header{align-items:flex-start}
}
</style>
