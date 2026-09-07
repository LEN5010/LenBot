<script setup>
import { computed, ref } from 'vue'
import { mdiContentCopy } from '@mdi/js'
import { sceneName } from '../api.js'
const props = defineProps({ type:{type:String,required:true}, id:String, sceneId:String, label:String, version:[String,Number], copyable:{type:Boolean,default:true} })
const copied=ref(false), error=ref('')
const to = computed(() => {
  const query={}
  if(props.sceneId)query.scene=props.sceneId
  if(props.id)query.id=props.id
  if(props.version)query.version=String(props.version)
  if(props.type==='scene')return {name:'scene',params:{sceneId:props.id}}
  if(props.type==='job')return {name:'job',params:{jobId:props.id},query:props.sceneId?{scene:props.sceneId}:{}}
  if(props.type==='task')return {name:'tasks',query:{...query,tab:'reminders'}}
  if(props.type==='memory')return {name:'memories',query}
  if(props.type==='skill')return {name:'skills',query}
  if(props.type==='media')return {name:'media',query}
  if(props.type==='event' && props.sceneId)return {name:'scene',params:{sceneId:props.sceneId},query:{event:props.id}}
  if(props.type==='event')return {name:'activity',query:{...query,tab:'events'}}
  if(props.type==='call')return {name:'activity',query:{...query,tab:'calls'}}
  if(props.type==='episode')return {name:'activity',query:{...query,tab:'turns',episode:props.id,id:undefined}}
  if(props.type==='result')return {name:'activity',query:{scene:props.sceneId,tab:'events',result:props.id}}
  return {name:'activity',query:{...query,tab:'events'}}
})
const text = computed(()=>props.label || (props.type==='scene'?sceneName(props.id):props.id))
async function copy() { try { await navigator.clipboard.writeText(props.id); copied.value=true;error.value='' } catch { error.value='未能复制，请在详情中选择编号复制' } }
</script>
<template>
  <span v-if="id" class="entity-link">
    <router-link class="entity-link__label" :to="to" :title="text">{{ text }}</router-link>
    <v-btn v-if="copyable" :icon="mdiContentCopy" variant="text" density="compact" size="x-small" :aria-label="copied?'已复制编号':`复制 ${id}`" @click="copy" />
    <span v-if="error" role="alert" class="copy-error">{{ error }}</span>
  </span>
  <span v-else class="muted">未关联</span>
</template>
<style scoped>
.entity-link{display:inline-flex;align-items:center;gap:4px;max-width:100%;min-width:0;vertical-align:middle}.entity-link__label{display:block;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.copy-error{font-size:12px;color:rgb(var(--v-theme-error));white-space:normal}
</style>
