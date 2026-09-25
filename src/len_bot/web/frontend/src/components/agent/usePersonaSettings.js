import { computed, ref, watch } from 'vue'
import { api } from '../../api.js'
import { useRequestGuard } from '../../composables/useRequestGuard.js'

// Persona tab: the persona draft, the Diana template preview, expression
// examples and the media picker used while editing one. Called from the page
// setup, so its media guard and watchers share the page's lifetime.
export function usePersonaSettings({
  busy, error, message, saveOutcomes, conflicts, clone, beginOperation, saveDraft, saveError,
  formValues, load, selection, exampleListGuard, presetGuard
}) {
  const personaNeedsReadback = ref(false)
  const persona = ref(null)
  const personaOriginal = ref('')
  const exemplars = ref([])
  const examplesLoading = ref(false), examplesError = ref(''), examplesReadAt = ref(null)
  const exampleOpen = ref(false)
  const editingExample = ref('')
  const example = ref(null)
  const exampleOriginal = ref('')
  const sourceExample = ref({scene_id:'', event_id:'', context:'', tag:''})
  const preset = ref(null)
  const presetLoading = ref(false)
  const selectedPresetFields = ref([])
  const selectedPresetExamples = ref([])
  const presetExampleResults = ref({})
  const presetMessage = ref('')
  const mediaOpen = ref(false)
  const mediaRows = ref([])
  const mediaTotal = ref(0)
  const mediaPage = ref(1)
  const mediaQuery = ref('')
  const mediaSearch = ref('')
  const mediaLoading = ref(false)
  const mediaError = ref('')
  const mediaScope = ref('global-safe')
  const mediaReadAt = ref(null)
  const mediaGuard = useRequestGuard(() => JSON.stringify([
    selection(),
    exampleOpen.value,
    mediaOpen.value,
    editingExample.value,
    example.value?.scene_id,
    mediaScope.value,
    mediaSearch.value,
    mediaPage.value
  ]))
  let mediaTarget = null
  const personaLabels = {
    identity_name:'机器人名字',
    identity_persona:'身份背景',
    identity_core:'性格与相处方式',
    character_context:'角色资料与梗',
    conversation_style:'说话方式'
  }
  const personaDirty = computed(()=>!!persona.value&&JSON.stringify(persona.value)!==personaOriginal.value)
  const exampleDirty = computed(()=>exampleOpen.value&&JSON.stringify(example.value)!==exampleOriginal.value)
  const imageUrl = (assetId,scene='') => `/api/media/${encodeURIComponent(assetId)}/file?scene_id=${encodeURIComponent(scene||'global-safe')}`
  async function loadExamples() {
    const fresh = exampleListGuard()
    examplesLoading.value = true;
    examplesError.value = ''
    try {
      const result = await api('/api/voice/exemplars')
      if(fresh()){
        exemplars.value=result.exemplars;
        examplesReadAt.value=Date.now()/1000
      }
    } catch(e){
      if(fresh())examplesError.value=e.message
    }
    finally{
      if(fresh())examplesLoading.value=false
    }
  }
  async function savePersona() {
    if(busy.value||personaNeedsReadback.value||saveOutcomes.value.persona||conflicts.entries.persona)return
    const fresh = beginOperation('persona')
    const progress={submitted:false,confirmed:false}
    try {
      const result=await saveDraft('persona','/api/settings/persona',formValues('persona'),'POST',progress)
      if(!fresh())return
      personaNeedsReadback.value=true;
      message.value=result.message;
      await load({accept:fresh})
    }catch(e){
      await saveError(e,'persona',fresh,progress)
    }finally{
      if(fresh())busy.value=''
    }
  }
  function editExample(item=null) {
    if(busy.value)return
    editingExample.value=item?.id||''
    example.value=item?{
      context:item.context,
      scene_id:item.scene_id,
      tag:item.tag,
      segments:clone(item.segments)
    }:{context:'',scene_id:'',tag:'',segments:[{type:'text',text:''}]}
    exampleOriginal.value=JSON.stringify(example.value);
    exampleOpen.value=true
  }
  function closeExample() {
    if(busy.value)return
    if(exampleDirty.value&&!window.confirm('放弃尚未保存的表达样例？'))return
    exampleOpen.value=false;
    example.value=null
    mediaOpen.value=false
  }
  function changePart(index,type){
    example.value.segments[index]=type==='text'?{type,text:''}:{type,asset_id:''}
  }
  function addPart(type){
    if(example.value.segments.length<20)example.value.segments.push(type==='text'?{type,text:''}:{type,asset_id:''})
  }
  function movePart(index,direction){
    const parts=example.value.segments,
      target=index+direction;
    if(target>=0&&target<parts.length)[parts[index],parts[target]]=[parts[target],parts[index]]
  }
  async function saveExample(){
    if(busy.value)return
    const fresh = beginOperation('example')
    try{
      await api('/api/voice/exemplars'+(editingExample.value?'/'+encodeURIComponent(editingExample.value):''),{method:editingExample.value?'PUT':'POST',body:JSON.stringify(example.value)});
      if(!fresh())return;
      exampleOpen.value=false;
      example.value=null;
      message.value='表达样例已保存';
      await loadExamples()
    }
    catch(e){
      if(fresh())error.value=e.message
    }finally{
      if(fresh())busy.value=''
    }
  }
  async function changeExample(item,remove=false){
    if(busy.value)return
    if(!window.confirm(remove?'删除这条表达样例？':`${item.enabled?'停用':'启用'}这条表达样例？后续对话将按新的状态携带样例。`))return
    const fresh = beginOperation(`example:${item.id}`)
    try{
      await api(remove?'/api/voice/exemplars/'+encodeURIComponent(item.id):'/api/voice/exemplars/toggle',{
        method:remove?'DELETE':'POST',
        body:remove?undefined:JSON.stringify({example_id:item.id,enabled:!item.enabled})
      });
      if(!fresh())return;
      message.value=remove?'表达样例已删除':'表达样例状态已保存';
      await loadExamples()
    }
    catch(e){
      if(fresh())error.value=e.message
    }finally{
      if(fresh())busy.value=''
    }
  }
  async function createFromSentMessage(){
    if(busy.value || !sourceExample.value.scene_id.trim() || !sourceExample.value.event_id.trim()) return
    const fresh = beginOperation('example-source')
    try {
      await api('/api/voice/exemplars/from-message',{
        method:'POST',
        body:JSON.stringify({
          ...sourceExample.value,
          scene_id:sourceExample.value.scene_id.trim(),
          event_id:sourceExample.value.event_id.trim(),
          context:sourceExample.value.context.trim(),
          tag:sourceExample.value.tag.trim()
        })
      })
      if(!fresh())return
      sourceExample.value={scene_id:'',event_id:'',context:'',tag:''};
      message.value='已从真实送达消息创建表达样例，请继续编辑或停用';
      await loadExamples()
    } catch(e){
      if(fresh())error.value=e.message
    } finally {
      if(fresh())busy.value=''
    }
  }
  function openMedia(index){
    if(busy.value)return;
    mediaTarget=example.value.segments[index];
    mediaScope.value=example.value.scene_id||'global-safe';
    mediaQuery.value='';
    mediaSearch.value='';
    mediaPage.value=1;
    mediaOpen.value=true;
    loadMedia()
  }
  async function loadMedia(){
    const fresh=mediaGuard();
    mediaRows.value=[];
    mediaTotal.value=0;
    mediaReadAt.value=null
    if(!mediaOpen.value||!exampleOpen.value)return
    mediaLoading.value=true;
    mediaError.value=''
    try{
      const result=await api('/api/media?'+new URLSearchParams({
        scene_id:mediaScope.value,
        query:mediaSearch.value,
        curated:'true',
        enabled:'true',
        page:String(mediaPage.value),
        page_size:'48'
      }));
      if(!fresh())return;
      mediaRows.value=result.items;
      mediaTotal.value=result.total;
      mediaReadAt.value=Date.now()/1000
    }
    catch(e){
      if(fresh())mediaError.value=e.message
    }finally{
      if(fresh())mediaLoading.value=false
    }
  }
  function chooseMedia(asset){
    if(busy.value||mediaLoading.value||!mediaReadAt.value||mediaError.value||!mediaOpen.value||!exampleOpen.value)return
    if(mediaScope.value!==(example.value.scene_id||'global-safe')||!example.value.segments.includes(mediaTarget)||mediaTarget.type!=='image')return
    mediaTarget.asset_id=asset.id;
    mediaOpen.value=false
  }
  async function previewPreset(){
    if(busy.value)return
    const fresh = presetGuard()
    presetLoading.value = true
    error.value = ''
    try {
      const result = await api('/api/settings/persona/diana')
      if (!fresh()) return
      preset.value = result
      selectedPresetFields.value = []
      selectedPresetExamples.value = []
      presetExampleResults.value = {}
      presetMessage.value = ''
    } catch (e) {
      if (fresh()) error.value = e.message
    } finally {
      if (fresh()) presetLoading.value = false
    }
  }
  function fillPresetFields(){
    if (busy.value || saveOutcomes.value.persona || personaNeedsReadback.value || !preset.value || !persona.value) return
    for (const key of selectedPresetFields.value) {
      persona.value[key] = preset.value.fields[key]
    }
    presetMessage.value = `已将 ${selectedPresetFields.value.length} 个字段填入人格草稿，请关闭预览后保存人格。`
    selectedPresetFields.value = []
  }
  async function savePresetExamples(){
    if (busy.value || !preset.value) return
    const selected = clone(preset.value.examples.filter(item=>selectedPresetExamples.value.includes(item.id)))
    const fresh = beginOperation('preset-examples')
    try {
      for (const item of selected) {
        if(!fresh())return
        const body = {
          scene_id:item.scene_id,
          context:item.context,
          tag:item.tag,
          segments:item.segments
        }
        presetExampleResults.value[item.id] = {status:'saving',message:'正在保存'}
        try {
          const result = await api('/api/voice/exemplars', {method:'POST',body:JSON.stringify(body)})
          if(!fresh())return
          presetExampleResults.value[item.id] = {status:'saved',message:`已添加样例 ${result.exemplar.id}`}
          selectedPresetExamples.value = selectedPresetExamples.value.filter(id=>id!==item.id)
        } catch (e) {
          if(!fresh())return
          presetExampleResults.value[item.id] = {status:'error',message:e.message}
          presetMessage.value='本条保存未确认，本批不再提交后续样例。请先核对已保存列表，不要直接重复添加。'
          break
        }
      }
      if(fresh())await loadExamples()
    } finally {
      if(fresh())busy.value = ''
    }
  }
  watch(()=>example.value?.scene_id,()=>{
    mediaOpen.value=false
  })
  watch(mediaOpen,open=>{
    if(!open){
      mediaGuard();
      mediaLoading.value=false;
      mediaRows.value=[];
      mediaTotal.value=0;
      mediaReadAt.value=null;
      mediaTarget=null
    }
  },{flush:'sync'})
  return {
    personaNeedsReadback, persona, personaOriginal, exemplars, examplesLoading, examplesError,
    examplesReadAt, exampleOpen, editingExample, example, exampleOriginal, sourceExample, preset,
    presetLoading, selectedPresetFields, selectedPresetExamples, presetExampleResults,
    presetMessage, mediaOpen, mediaRows, mediaTotal, mediaPage, mediaQuery, mediaSearch,
    mediaLoading, mediaError, mediaScope, mediaReadAt, mediaGuard, personaLabels, personaDirty,
    exampleDirty, imageUrl, loadExamples, savePersona, editExample, closeExample, changePart,
    addPart, movePart, saveExample, changeExample, createFromSentMessage, openMedia, loadMedia,
    chooseMedia, previewPreset, fillPresetFields, savePresetExamples
  }
}
