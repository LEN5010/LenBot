<script setup>
import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, fmtTime, queryString } from '../api.js'
import PageHeader from '../components/PageHeader.vue'
import ScopeSelect from '../components/ScopeSelect.vue'
import CapabilityCards from '../components/CapabilityCards.vue'
const route=useRoute(),router=useRouter()
const data=ref(null),loading=ref(false),error=ref(''),participants=ref([]),participantError=ref('')
let sequence=0
async function load(){
  const own=++sequence;loading.value=true;error.value=''
  try{
    const result=await api('/api/overview/capabilities?'+queryString({scene_id:route.query.scene,requester:route.query.requester}))
    if(own===sequence)data.value=result
  }catch(e){if(own===sequence)error.value=e.message}finally{if(own===sequence)loading.value=false}
}
async function loadParticipants(scene){
  participants.value=[];participantError.value=''
  if(!scene)return
  try{const result=await api(`/api/cockpit/scenes/${encodeURIComponent(scene)}`)
    if(route.query.scene!==scene)return
    participants.value=Object.entries(result.session?.participants||{}).filter(([id])=>/^user:[1-9]\d*$/.test(id)).map(([id,item])=>({title:`${item.card||item.nickname||id} · ${id.slice(5)}`,value:id.slice(5)}))
  }catch(e){if(route.query.scene===scene)participantError.value=e.message}
}
function selectScene(scene){router.replace({name:'capabilities',query:scene?{scene}:{}})}
function selectRequester(requester){router.replace({name:'capabilities',query:{...route.query,requester:requester||undefined}})}
watch(()=>route.query.scene,loadParticipants,{immediate:true})
watch(()=>[route.query.scene,route.query.requester],load,{immediate:true})
</script>
<template>
  <div class="page-stack">
    <PageHeader title="工具能力" description="分别核对部署、保存值、当前运行、使用资格和真实记录。"><v-btn variant="outlined" :loading="loading" @click="load">刷新事实</v-btn></PageHeader>
    <div class="filters"><ScopeSelect :model-value="route.query.scene||''" label="查看哪个群的能力" @update:model-value="selectScene" /><v-select :model-value="route.query.requester||null" :items="participants" label="本群实际发起者（可选）" clearable :disabled="!route.query.scene" :error-messages="participantError" @update:model-value="selectRequester" /></div>
    <v-alert v-if="error" type="error" variant="tonal">{{ error }}<span v-if="data"> · 下方保留 {{ fmtTime(data.sampled_at) }} 的结果</span></v-alert>
    <v-progress-linear v-if="loading" indeterminate />
    <template v-if="data"><p class="muted">{{ data.evidence_note }} · 读取于 {{ fmtTime(data.sampled_at) }}</p><v-alert v-if="data.requires_restart" type="info" variant="tonal">另有已保存配置等待手动重启；每项能力的装载状态分别列出。</v-alert><CapabilityCards :data="data" /></template>
  </div>
</template>
<style scoped>.filters{display:flex;gap:16px;align-items:flex-start;flex-wrap:wrap}.filters>.v-select{max-width:360px;min-width:230px}</style>
