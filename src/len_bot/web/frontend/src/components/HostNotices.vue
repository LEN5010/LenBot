<script setup>
import { ref, watch } from 'vue'
import { api, fmtTime } from '../api.js'
const props=defineProps({scene:{type:String,required:true}})
const result=ref(null),busy=ref(false),error=ref('');let generation=0
const labels={group_recall:'群消息撤回',friend_recall:'私聊撤回',group_increase:'成员加入',group_decrease:'成员离开',group_ban:'禁言变化',group_card:'群名片变化',notify:'平台通知',group_upload:'群文件上传'}
async function read(more=false){if(busy.value)return;const id=++generation;busy.value=true;try{const value=await api(`/api/host/scenes/${encodeURIComponent(props.scene)}/notices${more?`?before=${result.value.next_before}`:''}`);if(id!==generation)return;result.value=more?{...value,items:[...result.value.items,...value.items]}:value;error.value=''}catch(e){if(id===generation)error.value=e.message}finally{if(id===generation)busy.value=false}}
watch(()=>props.scene,()=>{++generation;busy.value=false;result.value=null;error.value='';read()},{immediate:true})
</script>
<template><section class="surface"><h2>平台通知</h2><v-btn :loading="busy" @click="read(false)">刷新通知</v-btn><v-alert v-if="error" type="error">{{error}}</v-alert><p class="muted">保留真实平台通知；撤回只标记，不删除原文，不自动唤醒模型。</p><p v-if="result&&!result.items.length">暂无已保存通知。</p><article v-for="item in result?.items||[]" :key="item.id"><strong>{{labels[item.kind]||item.kind}}</strong> · {{fmtTime(item.time)}}<p v-if="item.raw.user_id">用户 {{item.raw.user_id}}<span v-if="item.raw.operator_id"> · 操作者 {{item.raw.operator_id}}</span></p><p v-if="item.platform_id">消息 {{item.platform_id}}</p><details><summary>平台原文</summary><pre>{{JSON.stringify(item.raw,null,2)}}</pre></details></article><v-btn v-if="result?.next_before" :disabled="busy" @click="read(true)">更早通知</v-btn></section></template>
<style scoped>article{padding:12px 0;border-bottom:1px solid var(--line)}pre{white-space:pre-wrap;overflow-wrap:anywhere}summary{cursor:pointer;padding:10px 0}</style>
