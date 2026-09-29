<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import { api, sceneName } from '../api.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.js'
const route=useRoute(),router=useRouter(),beginRead=useRequestGuard()
const options=ref([]),snapshot=ref(null),draft=ref(null),busy=ref(false),error=ref(''),notice=ref('')
const scene=computed(()=>typeof route.query.scene==='string'?route.query.scene:null)
const dirty=computed(()=>snapshot.value!==null&&JSON.stringify(draft.value)!==JSON.stringify(snapshot.value.saved))
useUnsavedChanges(dirty)
const identityFields=[['admins','管理员'],['whitelist','白名单'],['blacklist','黑名单']]
const roles=[{title:'主人（含本能力范围主人）',value:'owner'},{title:'管理员',value:'admin'},{title:'群管理',value:'group_manager'},{title:'白名单',value:'whitelist'},{title:'群友',value:'member'}]
const capabilities=[['delegate','委托任务'],['task_manage','管理他人任务'],['long_running','执行超过 30 分钟的任务'],['own_reminder','给自己定提醒'],['other_reminder','替他人定提醒'],['reminder_manage','管理他人安排'],['chat_control','聊天内临时安静/恢复']]
function clone(value){return JSON.parse(JSON.stringify(value))}
async function read(){if(!scene.value)return;const fresh=beginRead();error.value='';try{const data=await api(`/api/host/permissions?${new URLSearchParams({scene:scene.value})}`);if(fresh()){snapshot.value=data;draft.value=clone(data.saved)}}catch(e){if(fresh())error.value=e.message}}
async function reread(){if(dirty.value&&!window.confirm('放弃权限草稿并重读根配置？'))return;await read()}
async function select(value){await router.push({name:'host-permissions',query:{scene:value}})}
async function save(){if(busy.value)return;busy.value=true;error.value='';notice.value='';beginRead();try{const data=await api(`/api/host/permissions?${new URLSearchParams({scene:scene.value})}`,{method:'PUT',body:JSON.stringify(draft.value)});snapshot.value=data;draft.value=clone(data.saved);notice.value='已保存。全局名单影响所有场景，矩阵只影响所选场景；当前进程不热换，重启后生效。'}catch(e){error.value=e.message}finally{busy.value=false}}
onBeforeRouteUpdate(to=>!busy.value && (to.query.scene===route.query.scene || !dirty.value || window.confirm('放弃权限草稿并切换场景？')))
watch(scene,()=>{snapshot.value=null;draft.value=null;notice.value='';read()})
onMounted(async()=>{try{const state=await api('/api/host/state');options.value=state.scenes.map(item=>({title:sceneName(item.scene),value:item.scene}));if(!scene.value&&options.value.length)await router.replace({name:'host-permissions',query:{scene:options.value[0].value}});else await read()}catch(e){error.value=e.message}})
</script>
<template><div class="page-stack permission-page"><section class="surface"><h1>身份与能力权限</h1><p class="muted">身份按真实 QQ；请求归属由模型理解。这里编辑现有任务/安排规则，不生成消息授权编号或审批事务。</p>
<v-select :model-value="scene" :items="options" label="当前场景" :disabled="busy" @update:model-value="select" hide-details />
<v-alert v-if="error" type="error" variant="tonal">{{ error }}</v-alert><p v-if="notice" role="status">{{ notice }}</p><v-btn variant="outlined" :disabled="busy" @click="reread">重读根配置</v-btn></section>
<form v-if="draft && snapshot" class="page-stack" @submit.prevent="save"><section class="surface"><h2>全局身份名单</h2><p>当前根主人：{{ snapshot.owner_qq || '未配置（连接与运行页设置）' }}。账号浏览和插件账号操作仅根主人可用，不随下方矩阵扩权。</p>
<div class="fields"><v-combobox v-for="[field,label] in identityFields" :key="field" v-model="draft.global_identities[field]" :label="`全局${label} QQ`" multiple chips closable-chips :disabled="busy" hide-details /></div></section>
<section class="surface"><h2>本场景附加名单</h2><p class="muted">与全局名单合并，黑名单不叫醒大脑或插件命令，但保存原消息。已有本人任务/安排仍能取消。其他 Bot 名单仍在群聊的参与设置。</p>
<v-switch :model-value="draft.scene_identities!==null" label="为本场景补充身份名单" :disabled="busy" @update:model-value="value=>draft.scene_identities=value?{admins:[],whitelist:[],blacklist:[]}:null" hide-details />
<div v-if="draft.scene_identities" class="fields"><v-combobox v-for="[field,label] in identityFields" :key="field" v-model="draft.scene_identities[field]" :label="`本场景${label} QQ`" multiple chips closable-chips :disabled="busy" hide-details /></div>
<details><summary>现有任务/安排的局部身份（仍有效）</summary><pre>{{ JSON.stringify(snapshot.scoped_identities,null,2) }}</pre><p>局部名单只授予对应能力；可在群聊设置中编辑，不自动变成根主人。</p></details></section>
<section class="surface"><h2>本场景能力矩阵</h2><div class="fields"><v-select v-for="[field,label] in capabilities" :key="field" v-model="draft.matrix[field]" :label="label" :items="roles" multiple chips closable-chips :disabled="busy" hide-details /></div>
<p class="muted">任务和安排仍需在群聊页启用；角色工具许可仍独立生效。长任务权限只允许使用已配置的全局时限，不自动延长。普通角色每次最多 30 分钟。聊天控制不使用任务/安排的局部主人名单。</p>
<v-btn type="submit" color="primary" :disabled="busy || !dirty" :loading="busy">保存权限，重启生效</v-btn><p>{{ snapshot.restart_required?'保存值与运行值不同':'保存值与运行值一致' }}</p></section></form></div></template>
<style scoped>.permission-page{max-width:1100px;margin:auto}.fields{display:grid;gap:18px;margin:16px 0}.surface{min-width:0;overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere}summary{min-height:44px;cursor:pointer}.v-btn{margin-top:16px}h1{margin-top:0}</style>
