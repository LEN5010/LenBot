<script setup>
import { fmtTime } from '../../api.js'
import { roles } from '../../domain/roles.js'
import ConfigConflictBanner from '../ConfigConflictBanner.vue'
import ResourceViewer from '../ResourceViewer.vue'
const props = defineProps({state: {type: Object, required: true}, page: {type: Object, required: true}})
const { busy, loading, error, writeHeld, data, refresh, resolveConflict, providerById,
  choicesFor, message, saveOutcome, outcomeReadAt, adoptUnknownOutcome, readbackPending } = props.page
const { routesOpen, routingForm, roleEnabled, testConfirm, testResult, routingConflict,
  routingDirty, canSaveRouting, changeRoutingBinding, closeRouting, profile, saveRouting } = props.state
</script>
<template>
  <v-dialog
    :model-value="routesOpen"
    max-width="900"
    scrollable
    :persistent="!!busy"
    @update:model-value="value=>!value&&closeRouting()"
  >
    <v-card>
      <v-card-title class="dialog-title">编辑职责配置<v-btn variant="text" :disabled="!!busy" @click="closeRouting">关闭</v-btn>
      </v-card-title>
      <v-card-text>
        <v-alert v-if="saveOutcome" type="warning" variant="tonal" class="mb-4">
          <p>模型配置操作 {{ saveOutcome.kind }}<span v-if="saveOutcome.providerId"> · {{ saveOutcome.providerId }}</span> 结果未知：{{ saveOutcome.message }}不能直接重交原草稿或删除。</p>
          <p v-if="outcomeReadAt!==null">当前保存与运行值已于 {{ fmtTime(outcomeReadAt) }} 读取；这不是旧操作回执。明确采用后关闭原编辑，重新进入当前对象。</p>
          <ResourceViewer v-if="outcomeReadAt!==null" title="当前保存与运行投影（不是原草稿）" :content="data" />
          <v-btn variant="text" :disabled="!!busy||loading" @click="refresh">读取当前保存值</v-btn>
          <v-btn
            variant="text"
            :disabled="!!busy||loading||outcomeReadAt===null"
            @click="adoptUnknownOutcome"
          >采用当前值继续操作</v-btn>
        </v-alert>
        <v-alert v-if="message" type="info" variant="tonal" class="mb-4">{{ message }}</v-alert>
        <v-alert v-if="error" type="error" variant="tonal" class="mb-4">{{ error }}</v-alert>
        <v-alert v-if="readbackPending" type="warning" variant="tonal" class="mb-4">已写入配置，待读回真实值。<v-btn variant="text" :disabled="!!busy" :loading="loading" @click="refresh">重新读取保存值</v-btn>
        </v-alert>
        <ConfigConflictBanner
          :conflict="routingConflict?.problem"
          :current="routingConflict?.snapshot"
          :path-label="routingConflict?.problem.path.join(' → ')"
          :read-at="routingConflict?.readAt"
          :read-error="routingConflict?.readError"
          :busy="!!busy||loading||writeHeld"
          @keep="resolveConflict('routing',true)"
          @take="resolveConflict('routing',false)"
          @reload="refresh"
        >
          <template #current>
            <ResourceViewer
              v-if="routingConflict?.snapshot?.values"
              :content="routingConflict.snapshot.values"
              title="本次保存的职责绑定"
            />
            <p v-else>当前没有配置职责路由。</p>
          </template>
        </ConfigConflictBanner>
        <p class="muted mb-3">切换供应商或模型会清除旧绑定的推理强度和视觉确认。冲突后不会将旧参数拼到他人刚更换的模型；明确改选绑定时，该项按整项保留。</p>
        <v-form
          v-if="routingForm"
          :disabled="!!busy||loading||writeHeld"
          @submit.prevent="saveRouting"
        >
          <section v-for="role in roles" :key="role.key" class="routing-section">
            <div class="role-title">
              <h3>{{ role.name }}</h3>
              <v-switch
                v-model="roleEnabled[role.key]"
                :label="`配置${role.name}模型`"
                color="primary"
                hide-details
              />
            </div>
            <template v-if="roleEnabled[role.key]">
              <div class="route-fields">
                <v-select
                  :model-value="routingForm[role.key].provider_id"
                  :items="data.providers.map(p=>({title:`${p.id}${p.enabled?'':'（停用）'}`,value:p.id}))"
                  label="供应商"
                  @update:model-value="value=>changeRoutingBinding(role.key,'provider_id',value)"
                />
                <v-combobox
                  :model-value="routingForm[role.key].model"
                  :items="choicesFor(routingForm[role.key].provider_id)"
                  label="模型名称"
                  @update:model-value="value=>changeRoutingBinding(role.key,'model',value)"
                />
                <v-text-field
                  v-model="routingForm[role.key].reasoning_effort"
                  label="推理强度（留空使用模型默认）"
                />
                <v-switch
                  v-model="routingForm[role.key].supports_vision"
                  label="已确认此绑定支持图片输入"
                  hint="视频采样帧只装配给已确认支持视觉的绑定；默认关闭。"
                  persistent-hint
                />
              </div>
              <p
                v-if="routingForm[role.key].provider_id&&!providerById(routingForm[role.key].provider_id)"
                class="text-error mb-3"
                role="alert"
              >原供应商 {{ routingForm[role.key].provider_id }} 已不在当前保存列表中；请明确选择，不自动迁移到其他接口。</p>
              <v-btn
                variant="text"
                color="primary"
                :disabled="!!busy||writeHeld||!providerById(routingForm[role.key].provider_id)?.enabled||!routingForm[role.key].model?.trim()"
                @click="testConfirm={name:role.name,profile:profile(role.key)}"
              >检查当前选择（不保存）</v-btn>
            </template>
            <p v-else class="muted mt-3">{{ role.name }}职责保持未配置。</p>
          </section>
          <v-alert
            v-if="testResult"
            :type="testResult.success?'success':'error'"
            variant="tonal"
            class="mb-4"
          >
            {{ testResult.name }} · {{ testResult.model }}：{{ testResult.success?'能力检查通过':testResult.error||'能力检查未通过' }}
          </v-alert>
          <v-btn
            type="submit"
            color="primary"
            :loading="busy==='routing'"
            :disabled="!!busy||loading||writeHeld||!!routingConflict||!canSaveRouting||!routingDirty"
          >保存职责配置</v-btn>
        </v-form>
      </v-card-text>
    </v-card>
  </v-dialog>
</template>
<style scoped>
.role-title,.provider-heading,.dialog-title{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
.routing-section{padding:18px 0;border-bottom:1px solid var(--line);margin-bottom:18px}
.route-fields{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.5fr);gap:12px;margin-top:16px}
.route-fields>:last-child{grid-column:1/-1}
@media(max-width:600px){
  .route-fields{grid-template-columns:minmax(0,1fr)}
}
</style>
