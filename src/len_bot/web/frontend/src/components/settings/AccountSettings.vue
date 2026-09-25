<script setup>
import { fmtTime } from '../../api.js'
import HelpHint from '../HelpHint.vue'
const props = defineProps({state: {type: Object, required: true}, page: {type: Object, required: true}})
const { busy, currentSaveOutcome } = props.page
const { RESET_HELP, me, passwords, resetConfirm, changePassword, signOut } = props.state
</script>
<template>
  <v-card class="pa-5 form-card">
    <div class="section-header">
      <h2>登录账户</h2>
      <v-chip :color="me.is_default_password?'warning':'default'">
        {{ me.is_default_password?'仍使用初始密码':'已修改初始密码' }}
      </v-chip>
    </div>
    <p class="my-4">{{ me.username }} · 上次登录 {{ fmtTime(me.last_login_at) }}</p>
    <v-form
      :disabled="!!currentSaveOutcome||!!busy"
      class="form-grid"
      @submit.prevent="changePassword"
    >
      <v-text-field
        v-model="passwords.current_password"
        type="password"
        autocomplete="current-password"
        label="当前密码"
        required
      />
      <v-text-field
        v-model="passwords.new_password"
        type="password"
        autocomplete="new-password"
        label="新密码（至少 6 位）"
        minlength="6"
        required
      />
      <div class="actions wide">
        <v-btn
          type="submit"
          color="primary"
          :loading="busy==='password'"
          :disabled="!!currentSaveOutcome||!!busy||!passwords.current_password||passwords.new_password.length<6"
        >更新密码</v-btn>
        <v-btn variant="outlined" :disabled="!!currentSaveOutcome||!!busy" @click="signOut">退出登录</v-btn>
      </div>
    </v-form>
  </v-card>
  <v-expansion-panels class="danger-zone">
    <v-expansion-panel title="破坏性操作：重置全部对话数据">
      <v-expansion-panel-text>
        <p class="mb-3">
          清空全部对话、认识、工作与任务；运行配置、人工样例与表情库保留。
          <strong>不要把它当作修复路径。</strong>
          <HelpHint :text="RESET_HELP" />
        </p>
        <v-btn
          color="error"
          variant="outlined"
          :disabled="!!currentSaveOutcome||!!busy"
          @click="resetConfirm=true"
        >Reset 对话数据</v-btn>
      </v-expansion-panel-text>
    </v-expansion-panel>
  </v-expansion-panels>
</template>
<style scoped>
.form-card{max-width:1000px;width:100%}
.section-header{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}
.section-header h2,.form-card>h2{font-size:20px}
.form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;align-items:start}
.wide{grid-column:1/-1}
.form-grid>.v-btn{justify-self:start}
.actions,.meta,.delivery-state,.saved-scenes{display:flex;gap:8px 12px;flex-wrap:wrap;align-items:center}
.danger-zone{max-width:1000px;margin-top:12px}
.settings-view p{line-height:1.7}
@media(max-width:650px){
  .form-grid{grid-template-columns:minmax(0,1fr)}
  .section-header{align-items:flex-start}
}
</style>
