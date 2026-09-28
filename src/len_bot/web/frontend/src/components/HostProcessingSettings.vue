<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api.js'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.js'
const snapshot=ref(null), draft=ref(null), busy=ref(false), error=ref(''), notice=ref('')
const labels={compaction:'上下文压缩',images:'图片处理',audio:'语音处理'}
const fields={trigger_ratio:'触发比例',max_output_tokens:'回想最大输出 token',keep_recent_entries:'保留近期条目数',max_bytes:'最大文件字节',max_pixels:'最大像素数',max_dimension:'最长边像素',timeout_seconds:'处理超时（秒）',max_seconds:'最大语音长度（秒）',wait_seconds:'入轮等待转写（秒）'}
const dirty=computed(()=>snapshot.value && JSON.stringify(draft.value)!==JSON.stringify(snapshot.value.saved.processing))
useUnsavedChanges(dirty)
function adopt(value){snapshot.value=value;draft.value=JSON.parse(JSON.stringify(value.saved.processing))}
async function read(){if(busy.value || (dirty.value&&!window.confirm('放弃处理参数草稿？')))return;busy.value=true;try{adopt(await api('/api/host/settings'));error.value=''}catch(e){error.value=e.message}finally{busy.value=false}}
async function save(){busy.value=true;notice.value='';try{adopt(await api('/api/host/settings/processing',{method:'PUT',body:JSON.stringify(draft.value)}));error.value='';notice.value='已保存，重启生效；当前处理参数未改变。'}catch(e){error.value=`保存未完成或结果未确认：${e.message}；草稿保留，请重读核对。`}finally{busy.value=false}}
function logging(value){draft.value.logging=value?{directory:'data/logs',retention_days:14,level:'INFO'}:null}
onMounted(read)
</script>
<template>
<section class="surface"><div class="heading"><h2>上下文、媒体与持久日志</h2><v-btn :disabled="busy" @click="read">重读处理设置</v-btn></div>
<p class="muted">仅保存根配置，重启生效。日志可选开启，按 UTC 日轮转；不改变聊天和模型快照的保存期限。</p>
<v-alert v-if="error" type="error">{{error}}</v-alert><v-alert v-if="notice" type="success">{{notice}}</v-alert>
<form v-if="draft" @submit.prevent="save"><fieldset :disabled="busy">
<div v-for="(title,key) in labels" :key="key"><h3>{{title}}</h3><div class="grid"><v-text-field v-for="(value,field) in draft[key]" :key="field" :label="fields[field]||field" type="number" step="any" :model-value="value" @update:model-value="v=>draft[key][field]=v===''?'':Number(v)" /></div></div>
<v-switch :model-value="draft.logging!==null" label="保存宿主日志到文件" @update:model-value="logging" />
<div v-if="draft.logging" class="grid"><v-text-field v-model="draft.logging.directory" label="实例内日志目录" /><v-text-field v-model.number="draft.logging.retention_days" type="number" label="保留日文件数" /><v-select v-model="draft.logging.level" :items="['INFO','WARNING','ERROR']" label="日志级别" /></div>
</fieldset><p v-if="snapshot.restart_required.processing">保存值与运行值不同，待重启。</p><v-btn type="submit" color="primary" :disabled="busy||!dirty">保存处理设置</v-btn>
<details><summary>当前运行参数</summary><pre>{{JSON.stringify(snapshot.running.processing,null,2)}}</pre></details></form></section>
</template>
<style scoped>.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}.heading{display:flex;justify-content:space-between;gap:16px}fieldset{border:0;padding:0}pre{white-space:pre-wrap}summary{padding:12px;cursor:pointer}</style>
