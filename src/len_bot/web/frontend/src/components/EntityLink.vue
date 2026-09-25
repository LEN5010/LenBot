<script setup>
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { mdiContentCopy } from '@mdi/js'
import { sceneName } from '../api.js'
import { withReturn } from '../router/navigation.js'
const props = defineProps({ type:{type:String,required:true}, id:String, sceneId:String, jobId:String, taskKind:String, label:String, version:[String,Number], span:Object, copyable:{type:Boolean,default:true} })
const copied=ref(false), error=ref('')
const route = useRoute()
const destination = computed(() => {
  const query={}
  const sceneQuery = route.name === 'scene' && route.params.sceneId === props.sceneId ? route.query : {}
  if(props.sceneId)query.scene=props.sceneId
  if(props.id)query.id=props.id
  if(props.version)query.version=String(props.version)
  if(props.type==='scene')return /^(group|private):/.test(props.id||'')
    ? {name:'scene',params:{sceneId:props.id},query:route.name==='scene'&&route.params.sceneId===props.id?route.query:{}}
    : {name:'activity',query:{scene:props.id,tab:'events'}}
  if(props.type==='job')return {name:'job',params:{jobId:props.id},query:props.sceneId?{scene:props.sceneId}:{}}
  if(props.type==='file' && props.jobId)return {name:'job',params:{jobId:props.jobId},query:{
    ...(route.name==='job' && route.params.jobId===props.jobId ? route.query : {}),scene:props.sceneId,tab:'progress',file:props.id}}
  if(props.type==='task')return {name:'tasks',query:{...query,tab:{deferred_delivery:'deferred',heartbeat:'system',heartbeat_occupancy:'system',interest_share:'system'}[props.taskKind] || 'reminders'}}
  if(props.type==='memory')return {name:'memories',query:route.name==='memories'
    ? {...route.query,...query,tab:'social',list_scene:route.query.list_scene ?? route.query.scene ?? ''}
    : query}
  if(props.type==='skill')return {name:'skills',query:route.name==='skills'
    ? {...route.query,...query,list_scene:route.query.list_scene ?? route.query.scene ?? ''}
    : query}
  if(props.type==='media')return {name:'media',query:route.name==='media'
    ? {...route.query,...query,list_scene:route.query.list_scene ?? route.query.scene ?? 'global-safe'}
    : query}
  if(props.type==='event' && /^(group|private):/.test(props.sceneId||''))return {name:'scene',params:{sceneId:props.sceneId},query:{...sceneQuery,event:props.id}}
  if(props.type==='event')return {name:'activity',query:{...query,tab:'events'}}
  if(props.type==='call')return {name:'activity',query:{...query,tab:'calls'}}
  if(props.type==='trace')return {name:'activity',query:{...query,tab:'turns'}}
  if(props.type==='episode')return {name:'activity',query:{...query,tab:'turns',episode:props.id,id:undefined}}
  if(props.type==='result')return {name:'activity',query:{scene:props.sceneId,tab:'events',result:props.id,
    result_start:props.span?.start,result_end:props.span?.end,result_unit:props.span?.coordinate_unit}}
  return {name:'activity',query:{...query,tab:'events'}}
})
const to = computed(() => props.type==='file' && route.name==='job' && route.params.jobId===props.jobId
  ? destination.value : withReturn(route, destination.value))
const text = computed(()=>props.label || (props.type==='scene'?sceneName(props.id):props.id))
async function copy() {
  try {
    await navigator.clipboard.writeText(props.id);
    copied.value=true;
    error.value=''
  } catch {
    error.value='未能复制，请在详情中选择编号复制'
  }
}
</script>
<template>
  <span v-if="id" class="entity-link">
    <router-link class="entity-link__label" :to="to" :title="text">{{ text }}</router-link>
    <v-btn
      v-if="copyable"
      :icon="mdiContentCopy"
      variant="text"
      density="compact"
      size="x-small"
      :aria-label="copied?'已复制编号':`复制 ${id}`"
      @click="copy"
    />
    <span v-if="error" role="alert" class="copy-error">{{ error }}</span>
  </span>
  <span v-else class="muted">未关联</span>
</template>
<style scoped>
.entity-link{display:inline-flex;align-items:center;gap:4px;max-width:100%;min-width:0;vertical-align:middle}
.entity-link__label{display:block;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.copy-error{font-size:12px;color:rgb(var(--v-theme-error));white-space:normal}
</style>
