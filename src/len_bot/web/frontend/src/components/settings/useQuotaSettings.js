import { computed, ref } from 'vue'

// Named resource policies edited as rows. The page owns guards and the
// shared read flow.
export function useQuotaSettings({
  busy, message, readAt, saveOutcomes, conflicts, beginOperation, saveDraft, saveError,
  adoptSnapshot
}) {
  const quotaText = ref(null)
  const quotaOriginal = ref('')
  const quotaDirty = computed(()=>quotaText.value!==null&&JSON.stringify(quotaRows.value)!==quotaOriginal.value)
  const policyRows = policies => Object.entries(policies || {}).map(([name,item])=>({name,
    work:item.work_token_limit ?? null, user:item.daily_user_token_limit ?? null, scene:item.daily_scene_token_limit ?? null}))
  const quotaRows = ref([])
  const quotaProblems = ref([])
  const quotaRaw = ref(false)
  const policyEditable = item => item && ['work_token_limit','daily_user_token_limit','daily_scene_token_limit']
    .every(key=>item[key]===undefined || item[key]===null || Number.isInteger(item[key]))
  const rawPolicies = computed(()=>Object.fromEntries(Object.entries(quotaRecord.value).filter(([,item])=>!policyEditable(item))))
  const quotaRecord = ref({})
  const quotaProblemsFor = index => quotaProblems.value.filter(item=>item.key===`policy:${index}`)
  const focusPolicy = key => document.querySelector(`[data-policy="${key}"]`)?.scrollIntoView({block:'center',behavior:'smooth'})
  const toggleQuotaRaw = () => {
    quotaRaw.value=!quotaRaw.value
  }
  const quotaNumber = (value,label,key,{minimum=0}={}) => {
    if (value===null || value===undefined || value==='') return null
    const text = String(value).trim()
    if (!/^-?\d+$/.test(text)) {
      quotaProblems.value=[...quotaProblems.value,{key, message:`${label}：请填写整数，不能截断小数或改写非法值`}]
      return undefined
    }
    const number = Number(text)
    if (!Number.isSafeInteger(number) || number<minimum) {
      quotaProblems.value=[...quotaProblems.value,{key,
        message:minimum>0?`${label}：请填 ${minimum} 或更大的整数；不设该维度上限请留空`
          :`${label}：请填 0 或更大的整数；留空表示不设该维度上限`}]
      return undefined
    }
    return number
  }
  function quotaValues() {
    quotaProblems.value=[]
    const policies=new Map(Object.entries(rawPolicies.value))
    for(const [index,row] of quotaRows.value.entries()) {
      const name=row.name.trim()
      if(!name){
        quotaProblems.value=[{key:`policy:${index}`,message:`第 ${index+1} 项：策略名不能为空`}];
        return null
      }
      if(policies.has(name)){
        quotaProblems.value=[{key:`policy:${index}`,message:`第 ${index+1} 项：策略名“${name}”已有同名策略`}];
        return null
      }
      const work=quotaNumber(row.work,'单工作累计 token',`policy:${index}`,{minimum:1})
      const user=quotaNumber(row.user,'主体日额度',`policy:${index}`),
        scene=quotaNumber(row.scene,'群日额度',`policy:${index}`)
      if(quotaProblems.value.length||work===undefined||user===undefined||scene===undefined)return null
      policies.set(name,{
        work_token_limit:work,
        daily_user_token_limit:user,
        daily_scene_token_limit:scene
      })
    }
    return {policies:Object.fromEntries(policies)}
  }
  async function saveQuota() {
    if (busy.value||saveOutcomes.value.resources||conflicts.entries.resources) return
    const fresh = beginOperation('resources');
    quotaProblems.value=[]
    const progress={submitted:false,confirmed:false}
    try {
      const values=quotaValues()
      if(values===null)return
      const result = await saveDraft('resources','/api/settings/resources',values,'PUT',progress)
      if (!fresh()) return
      adoptSnapshot('resources',{saved:result.settings,baseline:result.settings});
      readAt.value.resources=Date.now()/1000;
      message.value=result.message
    } catch(e) {
      if (!fresh()) return
      // A server rejection names the policy it belongs to; the same sentence is
      // shown beside that policy with a position the operator can act on.
      quotaProblems.value=(Array.isArray(e.details)?e.details:[]).map(item=>{
        const name=(item.loc||[]).filter(part=>typeof part==='string'&&part!=='body'&&part!=='policies')[0]
        const index=quotaRows.value.findIndex(row=>row.name.trim()===name)
        return {
          key:index>=0?`policy:${index}`:'',
          message:`${name?`策略“${name}” · `:''}${item.msg}`
        }
      })
      await saveError(e,'resources',fresh,progress)
    } finally {
      if(fresh())busy.value=''
    }
  }
  return {
    quotaText, quotaOriginal, quotaDirty, policyRows, quotaRows, quotaProblems, quotaRaw,
    rawPolicies, quotaRecord, quotaProblemsFor, focusPolicy, toggleQuotaRaw, quotaValues, saveQuota
  }
}
