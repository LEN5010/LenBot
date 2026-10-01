<script setup>
import { fmtTime } from '../../api.js'
import ConfigConflictBanner from '../ConfigConflictBanner.vue'
import ResourceViewer from '../ResourceViewer.vue'
const props = defineProps({state: {type: Object, required: true}, page: {type: Object, required: true}})
const { busy, loading, error, writeHeld, data, refresh, resolveConflict, providerById, message,
  saveOutcome, outcomeReadAt, adoptUnknownOutcome, readbackPending } = props.page
const { providerOpen, editingProvider, providerForm, providerRemoved, providerConflict,
  providerKeepBlocked, canSaveProvider, closeProvider, saveProvider } = props.state
</script>
<template>
  <v-dialog
    :model-value="providerOpen"
    max-width="650"
    scrollable
    :persistent="!!busy"
    @update:model-value="value=>!value&&closeProvider()"
  >
    <v-card>
      <v-card-title class="dialog-title">
        {{ editingProvider?'编辑供应商':'添加供应商' }}<v-btn variant="text" :disabled="!!busy" @click="closeProvider">关闭</v-btn>
      </v-card-title>
      <v-card-text>
        <v-alert v-if="saveOutcome" type="warning" variant="tonal" class="mb-4">
          <p>模型配置操作 {{ saveOutcome.kind }}<span v-if="saveOutcome.providerId"> · {{ saveOutcome.providerId }}</span> 结果未知：{{ saveOutcome.message }}不能直接重交原草稿或删除。</p>
          <p v-if="outcomeReadAt!==null">下面是 {{ fmtTime(outcomeReadAt) }} 读到的值。</p>
          <ResourceViewer v-if="outcomeReadAt!==null" title="当前保存与运行投影（不是原草稿）" :content="data" />
          <v-btn variant="text" :disabled="!!busy||loading" @click="refresh">读取当前保存值</v-btn>
          <v-btn
            variant="text"
            :disabled="!!busy||loading||outcomeReadAt===null"
            @click="adoptUnknownOutcome"
          >采用当前值继续操作</v-btn>
        </v-alert>
        <v-alert v-if="message" type="info" variant="tonal" class="mb-4">{{ message }}</v-alert>
        <v-alert
          v-if="providerRemoved&&!providerConflict"
          type="warning"
          variant="tonal"
          class="mb-4"
        >这个供应商已经被删了，修改保存不了。需要的话重新添加一个。</v-alert>
        <v-alert v-if="readbackPending" type="warning" variant="tonal" class="mb-4">已写入配置，待读回真实值。<v-btn variant="text" :disabled="!!busy" :loading="loading" @click="refresh">重新读取保存值</v-btn>
        </v-alert>
        <ConfigConflictBanner
          :conflict="providerConflict?.problem"
          :current="providerConflict?.snapshot"
          :path-label="providerConflict?.problem.path.join(' → ')"
          :read-at="providerConflict?.readAt"
          :read-error="providerConflict?.readError"
          :keep-disabled="providerKeepBlocked"
          :busy="!!busy||loading||writeHeld"
          @keep="resolveConflict('provider',true)"
          @take="resolveConflict('provider',false)"
          @reload="refresh"
        >
          <template #current>
            <ResourceViewer
              v-if="providerConflict?.snapshot?.provider"
              :content="providerConflict.snapshot.provider"
              title="本次保存的供应商（不含密钥）"
            />
            <p v-else>这个名称当前没有供应商。</p>
          </template>
        </ConfigConflictBanner>
        <p v-if="providerKeepBlocked" class="muted mb-4">
          {{ editingProvider ? '这个供应商已经被删了，请关掉后重新添加。' : '已经有同名的供应商了，换个名字，或者去编辑那个。' }}
        </p>
        <v-form
          v-if="providerForm"
          :disabled="!!busy||loading||writeHeld"
          class="config-form"
          @submit.prevent="saveProvider"
        >
          <v-alert v-if="error" type="error" variant="tonal">{{ error }}</v-alert>
          <v-text-field
            v-model="providerForm.id"
            label="供应商名称"
            :disabled="!!editingProvider||!!busy||loading||writeHeld"
            required
          />
          <v-select
            v-model="providerForm.api_style"
            label="接口协议"
            :items="[{title:'OpenAI 兼容接口',value:'openai'}]"
            required
          />
          <v-text-field
            v-model="providerForm.base_url"
            label="接口地址"
            placeholder="https://example.com/v1"
            required
          />
          <p v-if="editingProvider" class="muted">已保存密钥：{{ providerById(editingProvider)?.api_key_masked || '未设置' }}
          </p>
          <v-select
            v-model="providerForm.api_key_action"
            label="密钥操作"
            :items="[{title:'保留当前密钥',value:'keep'},{title:'替换密钥',value:'replace'},{title:'清除密钥',value:'clear'}]"
          />
          <v-text-field
            v-if="providerForm.api_key_action==='replace'"
            v-model="providerForm.api_key"
            label="接口密钥"
            type="password"
            autocomplete="new-password"
            placeholder="替换时必须填写新密钥；留空不会保存"
          />
          <v-text-field
            v-model.number="providerForm.timeout_seconds"
            type="number"
            min="0.1"
            step="0.1"
            label="超时时间（秒）"
            required
          />
          <v-textarea v-model="providerForm.models" label="常用模型名称（每行一项，可留空）" rows="4" />
          <v-switch v-model="providerForm.enabled" label="启用供应商" color="primary" />
          <v-btn
            type="submit"
            color="primary"
            :loading="busy==='provider'"
            :disabled="!!busy||loading||writeHeld||!!providerConflict||!canSaveProvider"
          >保存供应商</v-btn>
        </v-form>
      </v-card-text>
    </v-card>
  </v-dialog>
</template>
<style scoped>
.role-title,.provider-heading,.dialog-title{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
.config-form{display:grid;gap:8px}
.config-form>.v-btn{justify-self:start}
</style>
