<script setup>
import AdvancedSection from '../AdvancedSection.vue'
import HelpHint from '../HelpHint.vue'
const props = defineProps({state: {type: Object, required: true}, page: {type: Object, required: true}})
const { busy, currentSaveOutcome, conflicts } = props.page
const { runtimeText, runtimeRestart, runtimeSavedBudgets, runtimeEffectiveBudgets,
  executionBudgets, budgetText, runtimeBudgetValue, setRuntimeBudget, heartbeatDraft,
  setHeartbeat, UPLOAD_HELP, fileUpload, setFileUpload, runtimeDirty, saveRuntime } = props.state
</script>
<template>
  <v-card class="pa-5 form-card">
    <h2>运行参数</h2>
    <section class="upload-block">
      <div class="section-header">
        <h3>群文件上传平台<HelpHint :text="UPLOAD_HELP" /></h3>
        <v-chip size="small" :color="fileUpload?.deployment_verified ? 'success' : 'warning'">
          {{ fileUpload ? (fileUpload.deployment_verified ? '已核对' : '未核对，无法上传') : '未声明' }}
        </v-chip>
      </div>
      <p v-if="!fileUpload" class="muted my-3">
        未声明上传平台，群文件只能在工作面板下载。
        <v-btn
          size="small"
          variant="tonal"
          color="primary"
          class="ml-2"
          :disabled="!!currentSaveOutcome||!!busy"
          @click="setFileUpload({})"
        >声明上传平台</v-btn>
      </p>
      <template v-else>
        <div class="form-grid my-3">
          <v-select
            :model-value="fileUpload.implementation"
            label="实现"
            :items="[{title:'SnowLuma',value:'snowluma'},{title:'NapCat',value:'napcat'}]"
            @update:model-value="value=>setFileUpload({implementation:value})"
          />
          <v-text-field
            :model-value="fileUpload.version || ''"
            label="现场版本（可选记录）"
            placeholder="可从连接页读取"
            hint="仅供现场记录，不作为协议准入条件"
            persistent-hint
            @update:model-value="value=>setFileUpload({version:value.trim() || null})"
          />
          <v-text-field :model-value="fileUpload.protocol" label="配置／回执标签（由实现决定）" readonly />
          <v-text-field :model-value="fileUpload.export_mount_path" label="只读挂载点" readonly />
          <p class="muted wide">两种实现均调用 upload_group_file；标签和部署核验标记不等于平台取得文件，成功仍看真实 FILE_UPLOADED 与 file_id。</p>
        </div>
        <v-switch
          :model-value="fileUpload.deployment_verified"
          label="已人工核对实现、文件动作与只读挂载"
          hint="打开后仍需真实授权、资产审查与平台 file_id 回执"
          persistent-hint
          @update:model-value="value=>setFileUpload({deployment_verified:!!value})"
        />
        <div class="actions">
          <v-btn
            size="small"
            variant="text"
            color="error"
            :disabled="!!currentSaveOutcome||!!busy"
            @click="setFileUpload(null)"
          >取消声明</v-btn>
        </div>
      </template>
    </section>
    <p class="muted my-3">下面对照根配置已保存值与运行时当前发布值。编辑中的 JSON 尚未保存，不计入这两列。</p>
    <div class="budget-table-wrap">
      <table class="budget-table">
        <caption>执行预算</caption>
        <thead>
          <tr><th scope="col">范围</th><th scope="col">已保存</th><th scope="col">当前发布</th></tr>
        </thead>
        <tbody>
          <tr v-for="item in executionBudgets" :key="item.key">
            <th scope="row">{{ item.label }}</th>
            <td>{{ budgetText(runtimeSavedBudgets[item.key], item.unit) }}</td>
            <td>{{ budgetText(runtimeEffectiveBudgets[item.key], item.unit) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <p class="muted my-4">新对话与新建工作采用当前发布预算；已有工作及其恢复保留创建时的上限、期限和累计用量。改变设置不会重开已有结果或失败工作。</p>
    <v-alert v-if="runtimeRestart" type="info" variant="tonal" class="mb-4">另有需重建组件的配置等待手动重启；上表分别显示已保存值与本次读取到的运行值，未提供项不能据此判断已生效。</v-alert>
    <p class="muted my-3">常用执行预算用下面的数字框改；其余字段仍通过完整 JSON。留空表示该维度不设限。改这里会写进同一份草稿。</p>
    <v-form :disabled="!!currentSaveOutcome||!!busy" @submit.prevent="saveRuntime">
      <v-switch
        :model-value="heartbeatDraft.heartbeat_enabled || false"
        label="启用公共研究心跳"
        color="primary"
        @update:model-value="value=>setHeartbeat('heartbeat_enabled',value)"
      />
      <v-textarea
        :model-value="(heartbeatDraft.heartbeat_topics || []).join('\n')"
        label="公共研究主题（每行一项）"
        rows="3"
        hint="最多 20 项，每项 200 字；没有主题或有效兴趣时允许零研究。只保存研究结果与兴趣，不发布群消息。保存后需手动重启。"
        persistent-hint
        @update:model-value="value=>setHeartbeat('heartbeat_topics',value.split('\n').map(item=>item.trim()).filter(Boolean))"
      />
      <div class="form-grid mb-4">
        <v-text-field
          v-for="item in executionBudgets"
          :key="item.key"
          :model-value="runtimeBudgetValue(item.key)"
          :label="item.label+'（'+item.unit+'）'"
          type="number"
          :hint="'留空即不设限'"
          persistent-hint
          @update:model-value="value=>setRuntimeBudget(item.key,value)"
        />
      </div>
      <AdvancedSection title="其余运行参数（原始 JSON）" note="上面没有控件的字段在这里改">
        <v-textarea
          v-model="runtimeText"
          label="运行参数 JSON"
          rows="16"
          spellcheck="false"
          class="runtime-json"
        />
      </AdvancedSection>
      <div class="actions">
        <v-btn
          type="submit"
          color="primary"
          :loading="busy==='runtime'"
          :disabled="!!currentSaveOutcome||!!busy||!!conflicts.entries.runtime||!runtimeDirty"
        >保存运行参数</v-btn>
        <span v-if="runtimeDirty" class="muted">有未保存修改</span>
      </div>
    </v-form>
  </v-card>
</template>
<style scoped>
.budget-table-wrap{overflow-x:auto}
.budget-table{width:100%;border-collapse:collapse;text-align:left;font-size:14px}
.budget-table caption{text-align:left;font-weight:600;padding:8px 0 12px}
.budget-table th,.budget-table td{padding:12px;border-bottom:1px solid var(--line);white-space:nowrap}
.budget-table thead{background:rgb(var(--v-theme-surface-variant))}
.budget-table tbody th{font-weight:500}
.runtime-json :deep(textarea){font-family:monospace;font-size:13px;line-height:1.6}
.upload-block{border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin:16px 0}
.upload-block h3{display:flex;align-items:center;gap:2px;font-size:15px;font-weight:650;margin:0}
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
