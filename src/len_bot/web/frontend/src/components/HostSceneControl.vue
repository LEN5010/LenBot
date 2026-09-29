<script setup>
import {onMounted,ref} from 'vue'
import {api} from '../api.js'
import {useRequestGuard} from '../composables/useRequestGuard.js'
const props=defineProps({scene:{type:String,required:true}})
const state=ref(null),minutes=ref(60),direct=ref('allow'),busy=ref(false),error=ref(''),notice=ref('')
const guard=useRequestGuard()
const endpoint=()=>`/api/host/scenes/${encodeURIComponent(props.scene)}/control`
function time(value){return new Date(value*1000).toLocaleString('zh-CN',{timeZone:state.value.timezone,hour12:false})}
async function read(){if(busy.value)return;const fresh=guard();busy.value=true;try{const result=await api(endpoint());if(fresh()){state.value=result;error.value=''}}catch(e){if(fresh())error.value=e.message}finally{if(fresh())busy.value=false}}
async function change(action){if(busy.value)return;const seconds=Number(minutes.value)*60;if(action==='quiet'&&(!Number.isInteger(seconds)||seconds<1||seconds>604800)){error.value='请填写1秒至7天范围内、可换算为整数秒的时长。';return}
 const fresh=guard();busy.value=true;error.value='';notice.value='';try{const result=await api(`${endpoint()}/${action}`,{method:'POST',...(action==='quiet'?{body:JSON.stringify({seconds,direct:direct.value})}:{})});if(fresh()){state.value=result;notice.value=action==='quiet'?'临时安静已生效；不改根配置。':'临时安静已结束；根配置安静时段仍然有效。'}}catch(e){if(fresh())error.value=`操作失败或结果未确认：${e.message}。请重读状态；不会自动重试。`}finally{if(fresh())busy.value=false}}
onMounted(read)
</script>
<template><section class="surface"><div class="heading"><h2>临时参与控制</h2><v-btn :disabled="busy" @click="read">读取临时状态</v-btn></div>
<v-alert v-if="error" type="error" role="alert">{{error}}</v-alert><v-alert v-if="notice" type="info" role="status">{{notice}}</v-alert>
<template v-if="state"><p>{{state.scope}}</p><p>{{state.quiet_until===null?'当前不在安静时段':`当前安静边界：${time(state.quiet_until)}`}}；直接消息：{{({allow:'允许参与',defer:'延后处理',notice:'固定说明'})[state.direct]}}。</p>
<p v-if="state.temporary_quiet">最近临时安静：{{time(state.temporary_quiet.started)}} 至 {{time(state.temporary_quiet.until)}} · {{state.temporary_quiet.requester===null?'面板操作':`请求人 QQ ${state.temporary_quiet.requester}`}}。</p>
<form @submit.prevent="change('quiet')"><fieldset :disabled="busy"><v-text-field v-model="minutes" type="number" step="any" label="临时安静分钟数" />
<v-select v-model="direct" label="安静期间的直接消息" :items="[{title:'允许本场景直接消息',value:'allow'},{title:'一并延后（仅面板可提前恢复）',value:'defer'}]" /></fieldset>
<v-btn type="submit" :disabled="busy" color="primary">开始临时安静</v-btn> <v-btn type="button" :disabled="busy||state.temporary_quiet===null" variant="outlined" @click="change('resume')">结束临时安静</v-btn></form>
<p>消息与到期事项保留；不结束任务、不清空历史。长期节奏与权限仍从设置页保存，重启生效。</p></template></section></template>
<style scoped>.heading{display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap}fieldset{border:0;padding:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}p{overflow-wrap:anywhere}.v-btn{margin-bottom:8px}</style>
