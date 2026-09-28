<script setup>
import { computed,onMounted,ref } from 'vue'
import { api,fmtTime } from '../api.js'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.js'
const snapshot=ref(null),status=ref(null),draft=ref(null),busy=ref(false),error=ref(''),notice=ref('')
const daily=ref(''),hourly=ref('')
function adopt(value){snapshot.value=value;draft.value=JSON.parse(JSON.stringify(value.saved.limits));daily.value=JSON.stringify(draft.value.scene_daily_model_cost,null,2);hourly.value=JSON.stringify(draft.value.scene_messages_per_hour,null,2)}
const dirty=computed(()=>snapshot.value&&(JSON.stringify(draft.value)!==JSON.stringify(snapshot.value.saved.limits)||daily.value!==JSON.stringify(snapshot.value.saved.limits.scene_daily_model_cost,null,2)||hourly.value!==JSON.stringify(snapshot.value.saved.limits.scene_messages_per_hour,null,2)))
useUnsavedChanges(dirty)
async function read(){if(busy.value||(dirty.value&&!window.confirm('放弃额度设置草稿？')))return;busy.value=true;try{const [settings,current]=await Promise.all([api('/api/host/settings'),api('/api/host/limits')]);adopt(settings);status.value=current;error.value=''}catch(e){error.value=e.message}finally{busy.value=false}}
async function save(){if(busy.value)return;busy.value=true;notice.value='';try{const body={...draft.value,scene_daily_model_cost:JSON.parse(daily.value),scene_messages_per_hour:JSON.parse(hourly.value)};adopt(await api('/api/host/settings/limits',{method:'PUT',body:JSON.stringify(body)}));error.value='';notice.value='额度已保存，重启生效；当前运行上限未改变。'}catch(e){error.value=`未保存或结果未确认：${e.message}；请重读核对，草稿保留。`}finally{busy.value=false}}
onMounted(read)
</script>
<template><section class="surface"><div class="heading"><h2>模型预算与发言额度</h2><v-btn :loading="busy" @click="read">读取额度状态</v-btn></div><v-alert v-if="error" type="error">{{error}}</v-alert><v-alert v-if="notice" type="success">{{notice}}</v-alert>
<p class="muted">按已结算费用限制后续请求，存在在途超支窗口，不是供应商账单硬上限。金额预算要求所有模型配置同币种价格；费用未知暂停，不计为零。ASR须单独配置同币种价格；响应缺必要计量时费用仍未知。OpenViking内部调用不受宿主金额额度控制。</p>
<template v-if="status"><p v-for="item in status.scenes" :key="item.scene"><strong>{{item.scene}}</strong>：{{item.blocked?item.reason:'当前未触及额度'}}<span v-if="item.until"> · 截至 {{fmtTime(item.until)}}</span></p></template>
<form v-if="draft" @submit.prevent="save"><fieldset :disabled="busy"><div class="grid"><v-text-field v-model="draft.currency" label="计价币种（如 USD）" /><v-text-field :model-value="draft.daily_model_cost??''" label="全局每日模型金额（留空不限，0暂停）" @update:model-value="v=>draft.daily_model_cost=v===''?null:v" /><v-text-field :model-value="draft.messages_per_hour??''" type="number" step="1" label="默认每群每小时条数（留空不限）" @update:model-value="v=>draft.messages_per_hour=v===''?null:Number(v)" /></div>
<v-textarea v-model="daily" label="按场景日金额（JSON对象）" hint='例如 {"group:80001":"2.50"}；金额用十进制文本' persistent-hint /><v-textarea v-model="hourly" label="按群小时条数覆盖（JSON对象）" hint='例如 {"group:80001":20}；null表示不限' persistent-hint /></fieldset><p>发言按UTC自然小时统计；固定额度说明每个阻断时段最多一次，不调用表达器。</p><p v-if="snapshot.restart_required.limits">保存值待重启。</p><v-btn type="submit" color="primary" :disabled="busy||!dirty">保存额度设置</v-btn></form></section></template>
<style scoped>.heading{display:flex;gap:16px;justify-content:space-between;flex-wrap:wrap}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px}fieldset{border:0;padding:0}p{overflow-wrap:anywhere}</style>
