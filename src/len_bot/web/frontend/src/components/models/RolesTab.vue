<script setup>
import { roles } from '../../domain/roles.js'
const props = defineProps({state: {type: Object, required: true}, page: {type: Object, required: true}})
const { busy, loaded, writeHeld, data, providerById } = props.page
const { testConfirm, testResult, profile } = props.state
</script>
<template>
  <div v-if="loaded" class="role-grid">
    <v-card v-for="role in roles" :key="role.key" class="pa-5 role-card">
      <div class="role-title">
        <h2>{{ role.name }}</h2>
        <v-chip size="small" :color="data.routing?.[role.key] ? 'primary' : 'default'">
          {{ data.routing?.[role.key] ? '已配置' : '未配置' }}
        </v-chip>
      </div>
      <p class="muted role-description">{{ role.description }}</p>
      <template v-if="data.routing?.[role.key]">
        <dl>
          <dt>供应商</dt>
          <dd>{{ data.routing[role.key].provider_id }}</dd>
          <dt>模型</dt>
          <dd>{{ data.routing[role.key].model }}</dd>
          <dt>推理强度</dt>
          <dd>{{ data.routing[role.key].reasoning_effort || '模型默认' }}</dd>
          <dt>当前运行</dt>
          <dd>{{ data.effective?.routing?.[role.key]?.model || '未绑定' }}</dd>
        </dl>
        <v-alert
          v-if="!providerById(data.routing[role.key].provider_id)?.enabled"
          type="warning"
          variant="tonal"
          density="compact"
        >当前供应商未启用</v-alert>
        <v-btn
          class="mt-auto"
          variant="outlined"
          :disabled="!!busy || writeHeld || !providerById(data.routing[role.key].provider_id)?.enabled"
          @click="testConfirm={name:role.name,profile:{...data.routing[role.key]}}"
        >主动检查能力</v-btn>
      </template>
      <p v-else class="muted">
        {{ role.key==='maintenance' ? '维护未配置，历史维护与工作压缩尚未就绪。' : '该职责尚未配置。' }}
      </p>
    </v-card>
  </div>
  <v-alert type="info" variant="tonal">浏览、刷新和选择模型不会发起模型请求。能力检查需要你主动确认；模型目录中的名称不代表已通过检查。</v-alert>
  <v-card v-if="testResult" class="pa-5">
    <div class="role-title">
      <h2>{{ testResult.name }}能力检查</h2>
      <v-chip :color="testResult.success?'success':'error'">
        {{ testResult.success?'检查通过':'检查失败' }}
      </v-chip>
    </div>
    <p class="my-3">{{ testResult.model }} · {{ testResult.latency_ms }} 毫秒</p>
    <div class="check-list">
      <p
        v-for="(name,key) in {image_reading:'原图识别',forced_tool:'指定工具调用',tool_continuation:'原生工具续接'}"
        :key="key"
      >
        {{ name }}：{{ testResult.checks[key] ? '通过' : '未通过或未执行' }}
      </p>
    </div>
    <v-alert v-if="testResult.error" type="error" variant="tonal" class="mt-3">
      {{ testResult.error }}
    </v-alert>
  </v-card>
  <RouterLink :to="{name:'activity',query:{tab:'calls'}}">前往运行记录查看持久调用账与用量</RouterLink>
</template>
<style scoped>
.role-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}
.role-card{display:flex;flex-direction:column;gap:16px;min-width:0}
.role-title,.provider-heading,.dialog-title{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
.role-title h2,.provider-heading h2{font-size:19px}
.role-description{min-height:3.5em;line-height:1.7}
.role-card dl{display:grid;grid-template-columns:75px minmax(0,1fr);gap:12px;font-size:14px}
.role-card dt{color:var(--text-secondary)}
.role-card dd{margin:0;overflow-wrap:anywhere}
.check-list{display:flex;flex-wrap:wrap;gap:8px 20px}
@media(max-width:1100px){
  .role-grid{grid-template-columns:minmax(0,1fr)}
}
@media(max-width:600px){
  .role-description{min-height:0}
}
</style>
