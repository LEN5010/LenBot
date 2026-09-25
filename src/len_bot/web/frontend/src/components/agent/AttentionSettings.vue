<script setup>
const props = defineProps({state: {type: Object, required: true}, page: {type: Object, required: true}})
const { busy, currentSaveOutcome, conflicts } = props.page
const { attention, attentionDirty, saveAttention } = props.state
</script>
<template>
  <v-card class="pa-5 form-card">
    <h2>注意力与旁听</h2>
    <p class="muted my-3">普通原话有截止时间，按容量分批读取；真实搭话和短时观察使用较短合并等待。模型可以沉默；没有新输入不调用。观察间隔不是总调用次数或回复延迟上限。</p>
    <v-form
      :disabled="!!currentSaveOutcome||!!busy"
      class="form-grid"
      @submit.prevent="saveAttention"
    >
      <v-switch
        v-model="attention.attention_observation_enabled"
        label="启用普通消息的周期观察"
        color="primary"
        hint="关闭不影响真实搭话、名称/关键词的独立机会和短时观察期"
        persistent-hint
        class="wide"
      />
      <v-textarea v-model="attention.keywords" label="运营关键词（每行一项）" rows="4" class="wide" />
      <v-text-field
        v-model.number="attention.attention_observation_interval_seconds"
        type="number"
        min="0.1"
        step="0.1"
        label="普通观察间隔（秒）"
        required
      />
      <v-text-field
        v-model.number="attention.attention_focus_seconds"
        type="number"
        min="1"
        step="1"
        label="短时观察期（秒）"
        hint="真实搭话开启；模型可依据本次原话申请继续，沉默可保留，Bot 发言不自动续期"
        persistent-hint
        required
      />
      <v-text-field
        v-model.number="attention.scene_hourly_message_limit"
        type="number"
        min="0"
        step="10"
        label="每群每小时发言上限（0 为不限）"
        hint="达到后闲聊与主动分享不再进入模型；日程命令与直播推送不受影响。沉默调用仍有成本"
        persistent-hint
        required
      />
      <v-text-field
        v-model.number="attention.user_hourly_message_limit"
        type="number"
        min="0"
        step="1"
        label="每人每小时回复上限（0 为不限）"
        hint="达到后该成员的闲聊不再进入模型；额度状态在面板查看，不自动发群提示"
        persistent-hint
        required
      />
      <v-expansion-panels class="wide">
        <v-expansion-panel title="合并与读取参数">
          <v-expansion-panel-text>
            <div class="form-grid">
              <v-text-field
                v-model.number="attention.addressed_debounce_idle_ms"
                type="number"
                min="1"
                step="100"
                label="@ / 回复合并等待（毫秒）"
                required
              />
              <v-text-field
                v-model.number="attention.addressed_debounce_max_ms"
                type="number"
                min="1"
                step="100"
                label="@ / 回复最大合并等待（毫秒）"
                required
              />
              <v-text-field
                v-model.number="attention.observing_debounce_idle_ms"
                type="number"
                min="1"
                step="100"
                label="观察期合并等待（毫秒）"
                required
              />
              <v-text-field
                v-model.number="attention.observing_debounce_max_ms"
                type="number"
                min="1"
                step="100"
                label="观察期最大合并等待（毫秒）"
                required
              />
              <v-text-field
                v-model.number="attention.attention_keyword_cooldown_seconds"
                type="number"
                min="0"
                step="1"
                label="名称与关键词提速冷却（秒）"
                required
              />
              <v-text-field
                v-model.number="attention.conversation_recent_tokens"
                type="number"
                min="500"
                step="100"
                label="近期原话预算（文本 token）"
                required
              />
            </div>
          </v-expansion-panel-text>
        </v-expansion-panel>
      </v-expansion-panels>
      <v-btn
        type="submit"
        color="primary"
        :loading="busy==='attention'"
        :disabled="!!currentSaveOutcome||!!busy||!!conflicts.entries.attention||!attentionDirty"
      >保存注意力参数</v-btn>
    </v-form>
  </v-card>
</template>
<style scoped>
.form-card{max-width:1000px;width:100%}
.section-header h2,.form-card>h2{font-size:20px}
.form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;align-items:start}
.wide{grid-column:1/-1}
.form-grid>.v-btn{justify-self:start}
.settings-view p{line-height:1.7}
@media(max-width:650px){
  .form-grid{grid-template-columns:minmax(0,1fr)}
}
</style>
