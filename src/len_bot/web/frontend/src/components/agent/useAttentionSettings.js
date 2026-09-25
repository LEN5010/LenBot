import { computed, ref } from 'vue'

// Attention tab draft and save. The page owns guards and the shared read flow.
export function useAttentionSettings({
  busy, message, readAt, saveOutcomes, conflicts, beginOperation, saveDraft, saveError,
  adoptSnapshot, formValues
}) {
  const attention = ref(null)
  const attentionOriginal = ref('')
  const attentionDirty = computed(()=>!!attention.value&&JSON.stringify(attention.value)!==attentionOriginal.value)
  async function saveAttention() {
    if(busy.value||saveOutcomes.value.attention||conflicts.entries.attention)return
    const fresh = beginOperation('attention')
    const progress={submitted:false,confirmed:false}
    try {
      const result=await saveDraft('attention','/api/settings/attention',formValues('attention'),'PATCH',progress)
      if(!fresh())return
      adoptSnapshot('attention',{saved:result.settings,baseline:result.settings});
      readAt.value.attention=Date.now()/1000;
      message.value=result.message
    }catch(e){
      await saveError(e,'attention',fresh,progress)
    }finally{
      if(fresh())busy.value=''
    }
  }
  return {
    attention, attentionOriginal, attentionDirty, saveAttention
  }
}
