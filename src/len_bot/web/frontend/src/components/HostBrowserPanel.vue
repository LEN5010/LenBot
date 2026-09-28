<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../api.js'
const emit = defineEmits(['dirty'])
const snapshot=ref(null), draft=ref(null), enabled=ref(false), busy=ref(false), error=ref(''), notice=ref('')
const live=ref(null), devices=ref([]), pair=ref(''), session=ref('')
const blank=()=>({socket:'',browser_instance_id:null,binary:'',home:'',timeout_seconds:65,max_response_bytes:16000000})
const dirty=computed(()=>snapshot.value!==null && JSON.stringify(enabled.value?draft.value:null)!==JSON.stringify(snapshot.value.saved))
watch(dirty,value=>emit('dirty',value))
async function action(fn){if(busy.value)return;busy.value=true;error.value='';notice.value='';try{await fn()}catch(e){error.value=e.message}finally{busy.value=false}}
async function read(){if(dirty.value&&!window.confirm('放弃账号浏览器草稿并重读？'))return;await action(async()=>{snapshot.value=await api('/api/host/browser');enabled.value=snapshot.value.saved!==null;draft.value=JSON.parse(JSON.stringify(snapshot.value.saved??blank()))})}
async function save(){await action(async()=>{snapshot.value=await api('/api/host/browser',{method:'PUT',body:JSON.stringify({settings:enabled.value?{...draft.value,browser_instance_id:draft.value.browser_instance_id||null}:null})});notice.value='已保存根配置；当前进程绑定不变，需要自行重启。'})}
async function status(){await action(async()=>{live.value=await api('/api/host/browser/status');devices.value=(await api('/api/host/browser/devices')).devices})}
async function pairing(){await action(async()=>{pair.value=(await api('/api/host/browser/pair',{method:'POST'})).pairing_link})}
async function revoke(id){if(!window.confirm('吊销此设备？正在进行的浏览器操作将失去连接，已经执行的操作不会撤回。'))return;await action(async()=>{await api(`/api/host/browser/devices/${encodeURIComponent(id)}`,{method:'DELETE'});devices.value=(await api('/api/host/browser/devices')).devices;notice.value='已吊销设备授权。'})}
async function release(item){if(!window.confirm('仅清理这个已结束任务占用的专用浏览器会话？创建结果未知时请先从实际状态中选择会话ID。'))return;await action(async()=>{await api('/api/host/browser/release',{method:'POST',body:JSON.stringify({scene:item.scene,task_id:item.id,session_id:session.value||null})});snapshot.value=await api('/api/host/browser');notice.value='已确认专用浏览器无残留会话并释放本任务占用。';live.value=null})}
onMounted(read)
</script>
<template><section class="surface browser-panel"><h2>专用账号浏览器</h2>
<p class="muted">需要单独部署浏览器守护进程及专用配置中的扩展。这里不会安装、启动守护进程或借用日常浏览器；首次配对需由主人在扩展里完成。远程上传下载不支持。</p>
<v-alert v-if="error" type="error" variant="tonal">{{ error }}</v-alert><p v-if="notice" role="status">{{ notice }}</p>
<v-btn variant="outlined" :disabled="busy" @click="read">重读保存配置</v-btn>
<form v-if="snapshot && draft" @submit.prevent="save"><v-switch v-model="enabled" label="启用账号浏览配置" :disabled="busy" hide-details />
<div v-if="enabled" class="browser-fields"><v-text-field v-model="draft.socket" label="守护进程 Unix socket 绝对路径" :disabled="busy" hide-details />
<v-text-field v-model="draft.browser_instance_id" label="专用 browser_instance_id（配对前留空，读取实际连接后明确填写）" :disabled="busy" hide-details />
<v-text-field v-model="draft.binary" label="浏览器 CLI 绝对路径" :disabled="busy" hide-details /><v-text-field v-model="draft.home" label="专用守护进程 home 绝对路径" :disabled="busy" hide-details />
<v-text-field v-model.number="draft.timeout_seconds" type="number" label="单次操作超时（秒）" :disabled="busy" hide-details />
<v-text-field v-model.number="draft.max_response_bytes" type="number" label="单次响应字节上限" :disabled="busy" hide-details /></div>
<v-btn type="submit" color="primary" :disabled="busy || !dirty">保存，重启生效</v-btn><p>当前进程：{{ snapshot.running ? (snapshot.running.browser_instance_id || '已启用，尚未绑定实例') : '未启用' }} · {{ snapshot.restart_required ? '保存值待重启' : '与保存值一致' }}</p></form>
<div class="actions"><v-btn :disabled="busy || !snapshot?.running" variant="outlined" @click="status">读取实际连接与设备</v-btn><v-btn :disabled="busy || !snapshot?.running" variant="outlined" @click="pairing">生成一次性配对链接</v-btn></div>
<div v-if="pair"><p>链接仅交给主人在专用浏览器扩展中粘贴，生成链接不代表已连接：</p><code>{{ pair }}</code><v-btn variant="text" @click="pair=''">隐藏链接</v-btn></div>
<details v-if="live" open><summary>本次读取的守护进程实际状态</summary><pre>{{ JSON.stringify(live,null,2) }}</pre></details>
<ul><li v-for="device in devices" :key="device.device_id">{{ device.label }} · {{ device.device_id }}<v-btn variant="text" :disabled="busy" @click="revoke(device.device_id)">吊销</v-btn></li></ul>
<div v-if="snapshot?.occupied.length"><h3>仍占用专用配置的任务</h3><v-text-field v-model="session" label="创建结果未知时：从实际状态中填写待清理会话ID" hide-details />
<ul><li v-for="item in snapshot.occupied" :key="item.id">{{ item.scene }} · {{ item.goal }} · {{ item.status }}<p>{{ item.browser_session || '创建结果未知，不能认为没有会话' }}</p><v-btn variant="outlined" :disabled="busy || !['done','failed','cancelled'].includes(item.status)" @click="release(item)">明确清理残留会话</v-btn></li></ul></div>
</section></template>
<style scoped>.browser-panel{min-width:0;overflow-wrap:anywhere}.browser-fields{display:grid;gap:14px;margin:16px 0}.actions{display:flex;flex-wrap:wrap;gap:12px;margin:16px 0}pre,code{white-space:pre-wrap;overflow-wrap:anywhere}pre{max-height:400px;overflow:auto}summary{min-height:44px;cursor:pointer}</style>
