<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter, onBeforeRouteUpdate } from 'vue-router'
import { api, fmtTime } from '../api.js'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'
import { useConfigConflicts } from '../composables/useConfigConflicts.js'
import { rebaseConfigDraft } from '../lib/configDraft.js'
import ConfigConflictBanner from '../components/ConfigConflictBanner.vue'
import PageHeader from '../components/PageHeader.vue'
import ResourceViewer from '../components/ResourceViewer.vue'
import AttentionSettings from '../components/agent/AttentionSettings.vue'
import ExampleDialog from '../components/agent/ExampleDialog.vue'
import MediaPickerDialog from '../components/agent/MediaPickerDialog.vue'
import PersonaSettings from '../components/agent/PersonaSettings.vue'
import PresetDialog from '../components/agent/PresetDialog.vue'
import TimeSettings from '../components/agent/TimeSettings.vue'
import { useAttentionSettings } from '../components/agent/useAttentionSettings.js'
import { usePersonaSettings } from '../components/agent/usePersonaSettings.js'
import { useTimeSettings } from '../components/agent/useTimeSettings.js'
const route = useRoute()
const router = useRouter()
const tabs = [
  {value:'persona',title:'人格与表达'},
  {value:'attention',title:'参与方式'},
  {value:'time',title:'睡眠与时间'}
]
const tab = computed(() => tabs.some(item=>item.value===route.query.tab) ? route.query.tab : 'persona')
const baselines = ref({})
const conflicts = useConfigConflicts()
const currentConflict = computed(()=>conflicts.entries[tab.value])
const saveOutcomes=ref({}), currentSaveOutcome=computed(()=>saveOutcomes.value[tab.value])
async function saveDraft(domain,path,values,method,progress) {
  const body=JSON.stringify({baseline:baselines.value[domain],values})
  progress.submitted=true
  const result=await api(path,{method,body})
  if(result?.config_saved!==true||typeof result.message!=='string'||
    (domain==='persona'?result.success!==true:!Object.hasOwn(result,'settings')))
    throw new Error('配置响应缺少本次写入确认，结果待核对，未采用新基线。')
  progress.confirmed=true
  return result
}
const loading = ref(false)
const error = ref('')
const message = ref('')
const readAt = ref({})
const busy = ref('')
const selection = () => `${route.name}:${tab.value}`
const readGuard = useRequestGuard(selection),
  operationGuard = useRequestGuard(selection),
  presetGuard = useRequestGuard(selection),
  exampleListGuard = useRequestGuard(selection)
function beginOperation(kind) {
  const fresh = operationGuard()
  readGuard();
  loading.value = false
  presetGuard();
  presetLoading.value = false
  exampleListGuard();
  examplesLoading.value = false
  busy.value = kind;
  error.value = '';
  message.value = ''
  return fresh
}
const clone = value => JSON.parse(JSON.stringify(value))
// Each tab's draft and operations live in its own composable but are created
// here, so drafts outlive tab switches and every response still answers to
// this page's guards. Tab components render that state and own no requests.
const context = {busy, error, message, readAt, saveOutcomes, conflicts, clone, beginOperation, saveDraft, saveError,
  adoptSnapshot, formValues, load, selection, exampleListGuard, presetGuard}
const personaTab = usePersonaSettings(context)
const attentionTab = useAttentionSettings(context)
const timeTab = useTimeSettings(context)
const shared = {route, busy, loading, error, currentSaveOutcome, conflicts, saveOutcomes}
const { personaNeedsReadback, persona, personaOriginal, examplesLoading, exampleOpen, example, sourceExample, preset,
  presetLoading, mediaOpen, mediaGuard, personaLabels, personaDirty, exampleDirty, loadExamples } = personaTab
const { attention, attentionOriginal, attentionDirty } = attentionTab
const { timeDraft, timeOriginal, timeConfigured, timeLoaded, timeRestart, timeDirty } = timeTab
const dirty = computed(()=>personaDirty.value||attentionDirty.value||timeDirty.value||exampleDirty.value||Object.values(sourceExample.value).some(Boolean))
useUnsavedChanges(dirty)
onBeforeRouteUpdate((to,from)=>to.query.tab===from.query.tab||!exampleDirty.value||window.confirm('放弃尚未保存的表达样例并切换设置？其他标签中的配置草稿会保留。'))
function formValues(domain) {
  if(domain==='persona') {
    const {addressNames,...fields}=persona.value
    return {
      ...fields,
      address_names:[
        ...new Set(addressNames.split(/[\n,，、]+/).map(item=>item.trim()).filter(Boolean))
      ]
    }
  }
  if(domain==='attention') {
    const {keywords,...fields}=attention.value
    return {
      ...fields,
      attention_keywords:[...new Set(keywords.split('\n').map(item=>item.trim()).filter(Boolean))]
    }
  }
  return timeDraft.value===null?null:{
    ...timeDraft.value,
    sleep_start:timeDraft.value.sleep_start||null,
    sleep_end:timeDraft.value.sleep_end||null
  }
}
function writeForm(domain, settings) {
  if(domain==='persona') {
    persona.value={
      ...Object.fromEntries(Object.keys(personaLabels).map(key=>[key,settings[key]])),
      addressNames:settings.address_names.join('、')
    }
  } else if(domain==='attention') {
    const {attention_keywords,...rest}=settings
    attention.value={...rest,keywords:attention_keywords.join('\n')}
  } else timeDraft.value=clone(settings)
}
function adoptSnapshot(domain, snapshot) {
  writeForm(domain,snapshot.saved);
  baselines.value[domain]=clone(snapshot.baseline)
  if(domain==='persona'){
    personaOriginal.value=JSON.stringify(persona.value);
    personaNeedsReadback.value=false
  }
  else if(domain==='attention')attentionOriginal.value=JSON.stringify(attention.value)
  else timeOriginal.value=JSON.stringify(timeDraft.value)
}
async function saveError(problem,domain,fresh,progress) {
  if(!fresh())return
  if(problem.details?.config_saved===false&&conflicts.mark(domain,problem)){
    await load({accept:fresh});
    return
  }
  const rejected=problem.details?.config_saved===false||(problem.status===422&&Array.isArray(problem.details))
  if(progress.submitted&&!rejected){
    saveOutcomes.value[domain]={confirmed:progress.confirmed||problem.details?.config_saved===true,
      message:problem.message,snapshot:null,readAt:null}
    await load({accept:fresh})
    if(!fresh())return
  }
  error.value=problem.message+(error.value?'；当前读取：'+error.value:'')
}
function adoptSaveOutcome() {
  const domain=tab.value,outcome=currentSaveOutcome.value
  if(busy.value||loading.value||!outcome?.snapshot)return
  if(!window.confirm('放弃本标签原配置草稿，采用本次读取值继续编辑？不会重新保存、填入模板或重试运行应用。'))return
  try {
    adoptSnapshot(domain,outcome.snapshot)
    delete saveOutcomes.value[domain];
    conflicts.clear(domain);
    error.value=''
    message.value='已采用当前保存值，未重发保存或采用模板；未知的旧请求没有因此变成成功。'
  }catch(problem){
    error.value=problem.message
  }
}
function resolveConflict(keep) {
  const domain=tab.value,snapshot=currentConflict.value?.snapshot
  if(busy.value||loading.value||currentSaveOutcome.value||!snapshot)return
  if(!keep&&!window.confirm('放弃本标签未保存的配置，采用本次读取的保存值？其他标签草稿不变。'))return
  try {
    const next=keep?rebaseConfigDraft(baselines.value[domain],formValues(domain),snapshot.saved):null
    adoptSnapshot(domain,snapshot)
    if(keep)writeForm(domain,next)
    conflicts.clear(domain);
    error.value=''
    message.value=keep?'已保留实际改动，其余字段采用现值；请核对后保存本标签配置。':'已采用本次读取的保存值，没有再次保存。'
  }catch(problem){
    error.value=problem.message
  }
}
async function load({accept=()=>true}={}) {
  const currentTab = tab.value, current = readGuard(), fresh=()=>current()&&accept()
  loading.value = true;
  error.value = ''
  conflicts.beginRead(currentTab)
  if(saveOutcomes.value[currentTab]){
    saveOutcomes.value[currentTab].snapshot=null;
    saveOutcomes.value[currentTab].readAt=null
  }
  try {
    const snapshot=await api(`/api/settings/draft/${currentTab}`)
    if(!fresh())return
    if(currentTab==='time') {
      timeLoaded.value = true
      timeConfigured.value = snapshot.saved !== null
      timeRestart.value=snapshot.requires_restart
    }
    const dirtyNow={persona:personaDirty.value,attention:attentionDirty.value,time:timeDirty.value}[currentTab]
    if(saveOutcomes.value[currentTab]){
      saveOutcomes.value[currentTab].snapshot=snapshot;
      saveOutcomes.value[currentTab].readAt=Date.now()/1000
    }else if(!conflicts.capture(currentTab,snapshot)&&(!dirtyNow||(currentTab==='persona'&&personaNeedsReadback.value))) {
      adoptSnapshot(currentTab,snapshot)
    }
    readAt.value[currentTab]=Date.now()/1000
  } catch(e){
    if(fresh()){
      error.value=e.message;
      conflicts.readFailed(currentTab,e)
    }
  }
  finally{
    if(fresh())loading.value=false
  }
}
async function refresh() {
  if(busy.value)return
  await Promise.all([load(), ...(tab.value==='persona'?[loadExamples()]:[])])
}
watch(tab,()=>{
  operationGuard();
  presetGuard();
  exampleListGuard();
  mediaGuard()
  busy.value='';
  message.value='';
  presetLoading.value=false;
  examplesLoading.value=false
  exampleOpen.value=false;
  example.value=null;
  mediaOpen.value=false;
  preset.value=null
  if(route.name==='agent-settings')refresh()
},{immediate:true,flush:'sync'})
</script>
<template>
  <div class="page-stack settings-view">
    <PageHeader title="人格与参与" description="各配置节分别保存到根参数文件，刷新保留未保存的草稿。">
      <v-btn
        variant="outlined"
        :loading="loading||examplesLoading"
        :disabled="!!busy"
        @click="refresh"
      >刷新当前设置</v-btn>
    </PageHeader>
    <v-alert v-if="error" type="error" variant="tonal">
      {{ error }}<span v-if="readAt[tab]"> · 上次读取 {{ fmtTime(readAt[tab]) }}</span>
    </v-alert>
    <v-alert v-if="message" type="success" variant="tonal" closable @click:close="message=''">
      {{ message }}
    </v-alert>
    <p class="muted">切换标签保留配置草稿，但不继续跟踪旧操作；已提交的保存不会因此取消，请回原对象刷新核对。</p>
    <v-alert v-if="tab==='persona'&&personaNeedsReadback" type="warning" variant="tonal">人格已保存，但尚未读回保存值；草稿基线没有更新，请刷新核对后再编辑。</v-alert>
    <v-alert v-if="currentSaveOutcome" type="warning" variant="tonal">
      <p>
        {{ currentSaveOutcome.confirmed?'配置已取得写入确认，后续结果仍需核对。':'本次配置保存结果未知。' }}{{ currentSaveOutcome.message }}不要重复提交原草稿。</p>
      <p v-if="currentSaveOutcome.snapshot">当前保存值读取于 {{ fmtTime(currentSaveOutcome.readAt) }}；不代表原操作回执。</p>
      <ResourceViewer
        v-if="currentSaveOutcome.snapshot"
        title="当前保存值（不是草稿）"
        :content="currentSaveOutcome.snapshot.saved"
      />
      <v-btn variant="text" :disabled="!!busy||loading" @click="load">读取当前保存值</v-btn>
      <v-btn
        variant="text"
        :disabled="!!busy||loading||!currentSaveOutcome.snapshot"
        @click="adoptSaveOutcome"
      >采用当前值继续编辑</v-btn>
    </v-alert>
    <ConfigConflictBanner
      :conflict="currentConflict?.problem"
      :current="currentConflict?.snapshot"
      :path-label="currentConflict?.problem.path?.join('.')"
      :busy="!!busy||loading||!!currentSaveOutcome"
      :read-error="currentConflict?.readError"
      :read-at="currentConflict?.readAt"
      @keep="resolveConflict(true)"
      @take="resolveConflict(false)"
      @reload="load"
    >
      <template #current>
        <p v-if="currentConflict?.snapshot?.saved===null">本次读取明确为未配置，不是读取失败。</p>
        <ResourceViewer v-else title="本次读取的保存值（不是草稿）" :content="currentConflict?.snapshot?.saved" />
      </template>
    </ConfigConflictBanner>
    <v-tabs
      :model-value="tab"
      color="primary"
      show-arrows
      @update:model-value="value=>router.push({name:'agent-settings',query:{tab:value}})"
    >
      <v-tab v-for="item in tabs" :key="item.value" :value="item.value">
        {{ item.title }}<span v-if="conflicts.entries[item.value]"> · 待处理冲突</span>
        <span v-if="saveOutcomes[item.value]"> · 保存待核对</span>
      </v-tab>
    </v-tabs>
    <v-progress-linear v-if="loading" indeterminate />
    <PersonaSettings v-if="tab==='persona'" :state="personaTab" :page="shared" />
    <AttentionSettings v-if="tab==='attention'&&attention" :state="attentionTab" :page="shared" />
    <TimeSettings v-if="tab==='time'&&timeLoaded" :state="timeTab" :page="shared" />
    <ExampleDialog :state="personaTab" :page="shared" />
    <MediaPickerDialog :state="personaTab" />
    <PresetDialog :state="personaTab" :page="shared" />
  </div>
</template>
<style scoped>
.settings-view p{line-height:1.7}
</style>
