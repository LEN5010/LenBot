<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, fmtTime } from '../api.js'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'
import { useConfigConflicts } from '../composables/useConfigConflicts.js'
import { rebaseConfigDraft } from '../lib/configDraft.js'
import ConfigConflictBanner from '../components/ConfigConflictBanner.vue'
import PageHeader from '../components/PageHeader.vue'
import ResourceViewer from '../components/ResourceViewer.vue'
import AccessSettings from '../components/settings/AccessSettings.vue'
import AccountSettings from '../components/settings/AccountSettings.vue'
import ConnectionSettings from '../components/settings/ConnectionSettings.vue'
import DeliverySettings from '../components/settings/DeliverySettings.vue'
import MemberSettings from '../components/settings/MemberSettings.vue'
import QuotaSettings from '../components/settings/QuotaSettings.vue'
import ResetDataDialog from '../components/settings/ResetDataDialog.vue'
import RuntimeSettings from '../components/settings/RuntimeSettings.vue'
import { positiveInteger } from '../components/settings/fields.js'
import { useAccessSettings } from '../components/settings/useAccessSettings.js'
import { useAccountSettings } from '../components/settings/useAccountSettings.js'
import { useConnectionSettings } from '../components/settings/useConnectionSettings.js'
import { useDeliverySettings } from '../components/settings/useDeliverySettings.js'
import { useMemberSettings } from '../components/settings/useMemberSettings.js'
import { useQuotaSettings } from '../components/settings/useQuotaSettings.js'
import { useRuntimeSettings } from '../components/settings/useRuntimeSettings.js'
const route = useRoute()
const router = useRouter()
const tabs = [
  {value:'access',title:'QQ 回复资格'},
  {value:'resources',title:'额度策略'},
  {value:'members',title:'主播与订阅对象'},
  {value:'connection',title:'连接'},
  {value:'delivery',title:'发送'},
  {value:'runtime',title:'运行参数'},
  {value:'account',title:'账户'}
]
const tab = computed(() => tabs.some(item=>item.value===route.query.tab) ? route.query.tab : 'connection')
const baselines = ref({})
const conflicts = useConfigConflicts()
const currentConflict = computed(()=>conflicts.entries[tab.value])
const saveOutcomes=ref({}), currentSaveOutcome=computed(()=>saveOutcomes.value[tab.value])
const clone = value => JSON.parse(JSON.stringify(value))
async function saveDraft(domain,path,values,method,progress) {
  const body=JSON.stringify({baseline:baselines.value[domain],values})
  progress.submitted=true
  const result=await api(path,{method,body})
  if(!result||result.config_saved!==true||typeof result.message!=='string'||
    (domain==='connection'?(result.success!==true||typeof result.requires_restart!=='boolean'):!Object.hasOwn(result,'settings')))
    throw new Error('配置响应缺少本次保存所需字段，结果待核对，未采用新基线。')
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
  pageGuard = useRequestGuard(selection)
let currentPage = () => false
function beginOperation(kind) {
  const fresh = operationGuard()
  readGuard();
  loading.value = false
  busy.value = kind;
  error.value = '';
  message.value = ''
  return fresh
}
const leavingAfterLogout = ref(false)
// Each tab's draft and operations live in its own composable but are created
// here, so drafts outlive tab switches and every response still answers to
// this page's guards. Tab components render that state and own no requests.
const context = {busy, error, message, readAt, baselines, saveOutcomes, conflicts, beginOperation, saveDraft,
  saveError, adoptSnapshot, formValues, load, router, leavingAfterLogout,
  currentPage: () => currentPage, confirmLeave: () => confirmLeave()}
const connectionTab = useConnectionSettings(context)
const accessTab = useAccessSettings(context)
const quotaTab = useQuotaSettings(context)
const memberTab = useMemberSettings(context)
const deliveryTab = useDeliverySettings(context)
const runtimeTab = useRuntimeSettings(context)
const accountTab = useAccountSettings(context)
const shared = {busy, error, currentSaveOutcome, conflicts}
const { onebot, connection, connectionOriginal, connectionNeedsReadback, connectionDirty, connectionForm } = connectionTab
const { accessText, accessOriginal, grants, grantsOriginal, referenceError, loadReferences, loadCapabilityChoices,
  loadParticipants, accessProblems, accessDirty, grantEditor, editableAccess, editedGrant, withCurrentGrants } = accessTab
const { quotaText, quotaOriginal, quotaDirty, policyRows, quotaRows, quotaProblems, quotaRecord, quotaValues } = quotaTab
const { members, membersOriginal, membersRestart, membersDirty } = memberTab
const { shadow } = deliveryTab
const { runtimeText, runtimeOriginal, runtimeRestart, runtimeSavedBudgets, runtimeEffectiveBudgets, runtimeDirty } = runtimeTab
const { me, passwords, resetConfirm } = accountTab
const dirty = computed(()=>!leavingAfterLogout.value&&(runtimeDirty.value||connectionDirty.value||accessDirty.value||quotaDirty.value||membersDirty.value||!!passwords.value.current_password||!!passwords.value.new_password))
const { confirmLeave } = useUnsavedChanges(dirty)
function formValues(domain) {
  if(domain==='access')return {
    qq_reply_whitelist:accessText.value.split(/[,，\s]+/).filter(Boolean).map(value=>positiveInteger(value,'QQ 账号')),
    capability_grants:grants.value.map(editedGrant)
  }
  if(domain==='resources')return quotaValues()
  if(domain==='members')return members.value.map(item=>({
    name:item.name.trim(),
    aliases:item.aliasText.split(/[\n,，、]+/).map(value=>value.trim()).filter(Boolean),
    bilibili_uid:positiveInteger(item.bilibili_uid,'B 站 UID'),
    room_id:positiveInteger(item.room_id,'直播间号')
  }))
  if(domain==='connection')return clone(connection.value)
  const settings=JSON.parse(runtimeText.value)
  if(settings===null||Array.isArray(settings)||typeof settings!=='object')throw new Error('运行参数须填写 JSON 对象')
  return settings
}
function writeForm(domain, values) {
  if(domain==='access'){
    accessText.value=values.qq_reply_whitelist.join('\n');
    grants.value=values.capability_grants.map(grantEditor)
  }
  else if(domain==='resources')quotaRows.value=policyRows(values.policies)
  else if(domain==='members')members.value=values.map(item=>({...item,aliases:[...item.aliases],aliasText:item.aliases.join('、')}))
  else if(domain==='connection')connection.value=clone(values)
  else runtimeText.value=JSON.stringify(values,null,2)
}
function adoptSnapshot(domain, snapshot) {
  writeForm(domain,domain==='connection'?connectionForm(snapshot.saved):snapshot.saved)
  baselines.value[domain]=clone(snapshot.baseline)
  if(domain==='access'){
    accessOriginal.value=accessText.value;
    grantsOriginal.value=JSON.stringify(grants.value);
    accessProblems.value=[]
  }
  else if(domain==='resources'){
    quotaRecord.value=clone(snapshot.saved.policies);
    quotaText.value=JSON.stringify(snapshot.saved.policies,null,2)
    quotaOriginal.value=JSON.stringify(quotaRows.value);
    quotaProblems.value=[]
  } else if(domain==='members')membersOriginal.value=JSON.stringify(members.value)
  else if(domain==='connection'){
    connectionOriginal.value=JSON.stringify(connection.value);
    connectionNeedsReadback.value=false
  }
  else runtimeOriginal.value=runtimeText.value
}
function captureSaveOutcome(domain,snapshot) {
  const outcome=saveOutcomes.value[domain]
  if(!outcome)return false
  outcome.snapshot=snapshot;
  outcome.readAt=Date.now()/1000
  return true
}
async function saveError(problem, domain, fresh, progress) {
  if(!fresh())return
  if(problem.details?.config_saved===false&&conflicts.mark(domain,problem)){
    await load({accept:fresh});
    return
  }
  const rejected=problem.details?.config_saved===false || (problem.status===422&&Array.isArray(problem.details))
  if(progress.submitted&&!rejected){
    saveOutcomes.value[domain]={confirmed:progress.confirmed||problem.details?.config_saved===true,
      message:problem.message,snapshot:null,readAt:null}
    await load({accept:fresh})
    if(!fresh())return
  }
  error.value=problem.message+(error.value?'；读取当前值：'+error.value:'')
}
function adoptSaveOutcome() {
  const domain=tab.value, outcome=currentSaveOutcome.value
  if(busy.value||loading.value||!outcome?.snapshot)return
  if(!window.confirm('放弃本标签原草稿（包括未确认的令牌替换或新增授予），采用本次读取值继续编辑？这不重发、撤销或追认原请求，也不重试运行应用。'))return
  try {
    adoptSnapshot(domain,outcome.snapshot)
    delete saveOutcomes.value[domain];
    conflicts.clear(domain);
    error.value=''
    message.value='已采用当前保存值继续编辑；没有再次保存或重试应用。原操作结果仍按原回执与运行记录核对。'
  }catch(problem){
    error.value=problem.message
  }
}
function resolveConflict(keep) {
  const domain=tab.value,snapshot=currentConflict.value?.snapshot
  if(busy.value||loading.value||currentSaveOutcome.value||!snapshot)return
  if(!keep&&!window.confirm('放弃本标签未保存的配置，采用本次读取的保存值？其他标签草稿不变。'))return
  try {
    let next
    if(keep){
      const own=formValues(domain)
      if(domain==='resources'&&own===null)throw new Error('请先修正额度策略字段，再选择保留改动；原草稿与基线未变。')
      const previous=domain==='access'?editableAccess(baselines.value.access):domain==='connection'?JSON.parse(connectionOriginal.value):baselines.value[domain]
      const current=domain==='access'?editableAccess(snapshot.saved):domain==='connection'?connectionForm(snapshot.saved):snapshot.saved
      next=rebaseConfigDraft(previous,own,current)
      if(domain==='access')next=withCurrentGrants(next,snapshot.saved)
    }
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
    if (currentTab==='access') {
      referenceError.value = ''
      const [snapshot] = await Promise.all([
        api('/api/settings/draft/access'),
        loadCapabilityChoices(fresh),
        loadReferences(fresh)
      ])
      if (!fresh()) return
      if (!captureSaveOutcome('access',snapshot)&&!conflicts.capture('access',snapshot)&&!accessDirty.value)adoptSnapshot('access',snapshot)
      await Promise.all([
        ...new Set(grants.value.filter(grant=>grant.principal_type!=='system').map(grant=>grant.scene_id))
      ]
        .map(sceneId=>loadParticipants(sceneId, fresh)))
    } else if (currentTab==='resources') {
      const snapshot = await api('/api/settings/draft/resources');
      if (!fresh()) return
      if (!captureSaveOutcome('resources',snapshot)&&!conflicts.capture('resources',snapshot)&&!quotaDirty.value)adoptSnapshot('resources',snapshot)
    } else if (currentTab==='members') {
      const snapshot = await api('/api/settings/draft/members');
      if (!fresh()) return
      membersRestart.value=snapshot.requires_restart
      if (!captureSaveOutcome('members',snapshot)&&!conflicts.capture('members',snapshot)&&!membersDirty.value)adoptSnapshot('members',snapshot)
    } else if (currentTab==='connection') {
      const settings = await api('/api/websocket/status');
      if (!fresh()) return
      onebot.value=settings
      const snapshot={saved:settings,baseline:settings}
      if (!captureSaveOutcome('connection',snapshot)&&!conflicts.capture('connection',snapshot)&&(!connectionDirty.value||connectionNeedsReadback.value))adoptSnapshot('connection',snapshot)
    } else if (currentTab==='delivery') {
      const settings=await api('/api/cockpit/shadow');
      if(!fresh())return
      shadow.value=settings
    } else if (currentTab==='runtime') {
      const snapshot=await api('/api/settings/draft/runtime');
      if(!fresh())return
      if(!captureSaveOutcome('runtime',snapshot)&&!conflicts.capture('runtime',snapshot)&&!runtimeDirty.value)adoptSnapshot('runtime',snapshot)
      runtimeRestart.value=snapshot.requires_restart
      runtimeSavedBudgets.value=snapshot.saved
      runtimeEffectiveBudgets.value=snapshot.effective || {}
    } else {
      const result=await api('/api/auth/me');
      if(!fresh())return;
      me.value=result
    }
    if(fresh())readAt.value[currentTab]=Date.now()/1000
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
watch(tab,()=>{
  operationGuard();
  currentPage=pageGuard();
  busy.value='';
  message.value='';
  referenceError.value='';
  resetConfirm.value=false
  if(route.name==='settings')load()
},{immediate:true,flush:'sync'})
</script>
<template>
  <div class="page-stack settings-view">
    <PageHeader title="系统设置" description="各配置节分别保存到根参数文件，刷新保留未保存的草稿。">
      <v-btn variant="outlined" :loading="loading" :disabled="!!busy" @click="load">刷新当前设置</v-btn>
    </PageHeader>
    <v-alert v-if="error" type="error" variant="tonal">
      {{ error }}<span v-if="readAt[tab]"> · 上次读取 {{ fmtTime(readAt[tab]) }}</span>
    </v-alert>
    <v-alert v-if="message" type="success" variant="tonal" closable @click:close="message=''">
      {{ message }}
    </v-alert>
    <p class="muted">切换标签保留本页配置草稿，但不继续跟踪旧操作；服务器可能已保存，请回到原标签刷新核对后再提交。</p>
    <v-alert v-if="tab==='connection'&&connectionNeedsReadback" type="warning" variant="tonal">连接配置已写入，但保存值尚未读回；请刷新核对后再编辑，不要重复提交令牌。</v-alert>
    <v-alert v-if="currentSaveOutcome" type="warning" variant="tonal">
      <p>
        {{ currentSaveOutcome.confirmed?'已取得写入确认，但后续结果仍需核对。':'本次配置保存结果未知。' }}{{ currentSaveOutcome.message }}不要重复提交原草稿，尤其是令牌替换或新增授予。</p>
      <p v-if="currentSaveOutcome.snapshot">当前保存值读取于 {{ fmtTime(currentSaveOutcome.readAt) }}；这不是原操作回执。</p>
      <ResourceViewer
        v-if="currentSaveOutcome.snapshot"
        title="当前读取值（不是原草稿）"
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
    <p v-if="tab==='access'&&currentConflict" class="muted">白名单与授予列表按整组核对。明确保留后，仍使用当前授予 ID 和修订；已经删除的旧 ID 不会被改成新授予重新签发。</p>
    <v-tabs
      :model-value="tab"
      color="primary"
      show-arrows
      @update:model-value="value=>router.push({name:'settings',query:{tab:value}})"
    >
      <v-tab v-for="item in tabs" :key="item.value" :value="item.value">
        {{ item.title }}<span v-if="conflicts.entries[item.value]"> · 待处理冲突</span>
        <span v-if="saveOutcomes[item.value]"> · 保存待核对</span>
      </v-tab>
    </v-tabs>
    <v-progress-linear v-if="loading" indeterminate />
    <ConnectionSettings v-if="tab==='connection'&&onebot&&connection" :state="connectionTab" :page="shared" />
    <AccessSettings v-if="tab==='access'&&accessText!==null" :state="accessTab" :page="shared" />
    <QuotaSettings v-if="tab==='resources'&&quotaText!==null" :state="quotaTab" :page="shared" />
    <MemberSettings v-if="tab==='members'&&members!==null" :state="memberTab" :page="shared" />
    <DeliverySettings v-if="tab==='delivery'&&shadow" :state="deliveryTab" :page="shared" />
    <RuntimeSettings v-if="tab==='runtime'&&runtimeText!==null" :state="runtimeTab" :page="shared" />
    <AccountSettings v-if="tab==='account'&&me" :state="accountTab" :page="shared" />
    <ResetDataDialog :state="accountTab" :page="shared" />
  </div>
</template>
<style scoped>
.preset-field{margin-bottom:12px}
.meta{font-size:13px;color:#64748b}
.example-row{display:flex;justify-content:space-between;align-items:flex-start;gap:20px;padding:24px 0;border-bottom:1px solid #e2e8f0}
.example-row:last-child{border:0;padding-bottom:0}
.example-main{min-width:0;flex:1}
.example-row>.actions{max-width:220px;justify-content:flex-end}
.example-context{white-space:pre-wrap;line-height:1.65;color:#64748b;overflow-wrap:anywhere}
.example-body{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0;align-items:flex-start}
.example-body p{flex-basis:100%;white-space:pre-wrap;line-height:1.8;overflow-wrap:anywhere}
.example-body img{max-width:180px;max-height:180px;object-fit:contain}
.part-toolbar{display:flex;gap:12px;align-items:center;margin-bottom:16px;flex-wrap:wrap}
.part-toolbar>.v-input{flex:1;min-width:140px;max-width:180px}
.part-image{display:flex;gap:16px;align-items:center;flex-wrap:wrap}
.part-image img{max-width:100%;height:170px;object-fit:contain}
.media-filter{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;align-items:center}
.media-picker{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px}
.media-picker img{width:100%;height:150px;object-fit:contain;background:#f4f6f9}
.media-picker p{overflow-wrap:anywhere;min-height:3em}
.settings-view p{line-height:1.7}
@media(max-width:650px){
  .example-row{flex-direction:column}
  .example-row>.actions{max-width:none;justify-content:flex-start}
  .media-picker{grid-template-columns:repeat(2,minmax(0,1fr))}
  .part-toolbar>.actions{width:100%}
  .example-body img{max-width:140px;max-height:140px}
}
</style>
