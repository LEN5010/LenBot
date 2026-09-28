<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api.js'
const period=ref('day'),result=ref(null),busy=ref(false),error=ref('')
async function read(){if(busy.value)return;busy.value=true;try{result.value=await api(`/api/host/usage?period=${period.value}`);error.value=''}catch(e){error.value=e.message}finally{busy.value=false}}
onMounted(read)
</script>
<template><section class="surface"><h2>模型费用汇总</h2><div class="controls"><v-select v-model="period" :disabled="busy" :items="[{title:'今天',value:'day'},{title:'本月',value:'month'}]" label="统计范围" /><v-btn :loading="busy" @click="read">读取费用</v-btn></div><v-alert v-if="error" type="error">{{error}}</v-alert>
<template v-if="result"><p>{{result.period==='day'?'今天':'本月'}} · {{result.timezone}} · {{result.calls}} 次调用 · 费用未知 {{result.unknown_calls}} 次 · 未结束 {{result.unfinished_calls}} 次</p><p>已知估算：{{JSON.stringify(result.known_amounts)}}（不是服务商账单）</p><p v-for="source in result.unmetered_sources" :key="source">未计量：{{source}}</p>
<div class="table"><table><thead><tr><th>场景</th><th>用途</th><th>次数</th><th>估算</th><th>未知费用次数</th></tr></thead><tbody><tr v-for="row in result.groups" :key="`${row.scene}/${row.role}`"><td>{{row.scene}}</td><td>{{row.role}}</td><td>{{row.calls}}</td><td>{{JSON.stringify(row.known_amounts)}}</td><td>{{row.unknown_calls}}</td></tr></tbody></table></div></template></section></template>
<style scoped>.controls{display:flex;gap:16px;align-items:center}.controls .v-select{max-width:220px}.table{overflow:auto}table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:10px;border-bottom:1px solid var(--line)}</style>
