import { computed, ref } from 'vue'

// Business time tab draft and save. The page owns guards and the shared read
// flow.
export function useTimeSettings({
  busy, message, readAt, saveOutcomes, conflicts, beginOperation, saveDraft, saveError,
  adoptSnapshot, formValues
}) {
  const timeDraft = ref(null)
  const timeOriginal = ref('')
  const timeConfigured = ref(false)
  const timeLoaded = ref(false)
  const timeRestart = ref(false)
  const weekdays = [
    {title:'周一',value:0},
    {title:'周二',value:1},
    {title:'周三',value:2},
    {title:'周四',value:3},
    {title:'周五',value:4},
    {title:'周六',value:5},
    {title:'周日',value:6}
  ]
  const timeDirty = computed(()=>timeDraft.value!==null&&JSON.stringify(timeDraft.value)!==timeOriginal.value)
  function beginTimeConfiguration() {
    if(busy.value||saveOutcomes.value.time)return
    timeDraft.value = {
      timezone:'',
      week_start:null,
      afternoon_start:'',
      afternoon_end:'',
      sleep_start:null,
      sleep_end:null
    }
  }
  async function saveTime() {
    if (busy.value || saveOutcomes.value.time||conflicts.entries.time || !timeDraft.value) return
    const fresh = beginOperation('time')
    const progress={submitted:false,confirmed:false}
    try {
      const payload = formValues('time')
      const result = await saveDraft('time','/api/settings/time',payload,'PUT',progress)
      if(!fresh())return
      adoptSnapshot('time',{saved:result.settings,baseline:result.settings});
      readAt.value.time=Date.now()/1000;
      timeConfigured.value=result.settings!==null;
      timeRestart.value=result.requires_restart;
      message.value=result.message
    } catch(e) {
      await saveError(e,'time',fresh,progress)
    } finally {
      if(fresh())busy.value=''
    }
  }
  return {
    timeDraft, timeOriginal, timeConfigured, timeLoaded, timeRestart, weekdays, timeDirty,
    beginTimeConfiguration, saveTime
  }
}
