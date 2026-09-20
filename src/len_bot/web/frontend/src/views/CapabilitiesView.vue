<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, fmtTime, queryString } from '../api.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'
import PageHeader from '../components/PageHeader.vue'
import ScopeSelect from '../components/ScopeSelect.vue'
import CapabilityCards from '../components/CapabilityCards.vue'
const route=useRoute(),router=useRouter()
const data=ref(null),loading=ref(false),error=ref(''),participants=ref([]),participantError=ref('')
const scene=computed(()=>typeof route.query.scene==='string'?route.query.scene:'')
const requester=computed(()=>typeof route.query.requester==='string'?route.query.requester:'')
const selection=computed(()=>JSON.stringify([route.query.scene ?? '',route.query.requester ?? '']))
const invalidSelection=computed(()=>['scene','requester'].some(key=>route.query[key]!=null&&typeof route.query[key]!=='string'))
const loadedSelection=ref(null), participantScene=ref(null), participantReadAt=ref(null), participantLoading=ref(false)
const guard=useRequestGuard(()=>`${route.name}:${selection.value}`), participantGuard=useRequestGuard(()=>`${route.name}:${scene.value}`)
const unknownRequester=computed(()=>requester.value&&participantReadAt.value&&!participants.value.some(item=>item.value===requester.value))
async function load(){
  const fresh=guard(), target=selection.value;loading.value=true;error.value=''
  if(route.name!=='capabilities'){loading.value=false;return}
  if(loadedSelection.value!==target)data.value=null
  if(invalidSelection.value){data.value=null;error.value='场景和请求者须各有一个明确参数，不能使用重复参数；未改为查询全部范围。';loading.value=false;return}
  try{
    const result=await api('/api/overview/capabilities?'+queryString({scene_id:scene.value,requester:requester.value}))
    if(fresh()){data.value=result;loadedSelection.value=target}
  }catch(e){if(fresh())error.value=e.message}finally{if(fresh())loading.value=false}
}
async function loadParticipants(){
  const fresh=participantGuard(), target=scene.value
  participantError.value='';participantLoading.value=false
  if(route.name!=='capabilities')return
  if(participantScene.value!==target){participants.value=[];participantReadAt.value=null}
  if(!target)return
  participantLoading.value=true
  try{const result=await api(`/api/cockpit/scenes/${encodeURIComponent(target)}`)
    if(!fresh())return
    participants.value=Object.entries(result.session?.participants||{}).filter(([id])=>/^user:[1-9]\d*$/.test(id)).map(([id,item])=>({title:`${item.card||item.nickname||id} · ${id.slice(5)}`,value:id.slice(5)}))
    participantScene.value=target;participantReadAt.value=Date.now()/1000
  }catch(e){if(fresh())participantError.value=e.message}
  finally{if(fresh())participantLoading.value=false}
}
async function refresh(){await Promise.all([load(),loadParticipants()])}
function selectScene(scene){router.replace({name:'capabilities',query:{scene:scene||undefined,return_to:route.query.return_to}})}
function selectRequester(requester){router.replace({name:'capabilities',query:{...route.query,requester:requester||undefined}})}
watch(scene,loadParticipants,{immediate:true})
watch(selection,load,{immediate:true})
</script>
<template>
  <div class="page-stack">
    <PageHeader title="工具能力" description="分别核对部署、保存值、当前运行、使用资格和真实记录。">
      <v-btn variant="outlined" :loading="loading||participantLoading" @click="refresh">刷新事实</v-btn>
    </PageHeader>
    <div class="filters"><ScopeSelect :model-value="scene" label="查看哪个群的能力" @update:model-value="selectScene" /><v-select :model-value="requester||null" :items="participants" label="从已保存成员选择请求者（可选）" clearable :loading="participantLoading" :disabled="!scene||participantLoading" :error-messages="participantError" @update:model-value="selectRequester" /></div>
    <p v-if="participantReadAt" class="muted">成员读取于 {{ fmtTime(participantReadAt) }}{{ participantError ? '，本群刷新失败，保留该次列表' : '' }}；选择账号只预览当前授予，不构成实际工作来源。</p>
    <v-alert v-if="unknownRequester" type="info" variant="tonal">地址中选择的账号未出现在本次成员列表中。下方仅按此账号核对授予，不证明它实际发起过工作。</v-alert>
    <v-alert v-if="error" type="error" variant="tonal">{{ error }}<span v-if="data"> · 下方保留 {{ fmtTime(data.sampled_at) }} 的结果</span></v-alert>
    <v-progress-linear v-if="loading" indeterminate />
    <template v-if="data"><p class="muted">{{ data.evidence_note }} · 读取于 {{ fmtTime(data.sampled_at) }}</p><v-alert v-if="data.requires_restart" type="info" variant="tonal">另有已保存配置等待手动重启；每项能力的装载状态分别列出。</v-alert><CapabilityCards :data="data" /></template>
  </div>
</template>
<style scoped>.filters{display:flex;gap:16px;align-items:flex-start;flex-wrap:wrap}.filters>.v-select{max-width:360px;min-width:230px}</style>
