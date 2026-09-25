import { computed, ref } from 'vue'

// Streamers and subscription targets. The page owns guards and the shared
// read flow.
export function useMemberSettings({
  busy, message, readAt, saveOutcomes, conflicts, beginOperation, saveDraft, saveError,
  adoptSnapshot, formValues
}) {
  const members = ref(null)
  const membersOriginal = ref('')
  const membersRestart = ref(false)
  const membersDirty = computed(()=>members.value!==null&&JSON.stringify(members.value)!==membersOriginal.value)
  function addMember() {
    members.value.push({name:'',aliases:[],aliasText:'',bilibili_uid:null,room_id:null})
  }
  async function saveMembers() {
    if (busy.value || saveOutcomes.value.members||conflicts.entries.members || !members.value) return
    const fresh = beginOperation('members')
    const progress={submitted:false,confirmed:false}
    try {
      const values=formValues('members')
      const result=await saveDraft('members','/api/settings/members',values,'PUT',progress)
      if (!fresh()) return
      adoptSnapshot('members',{saved:result.settings,baseline:result.settings});
      readAt.value.members=Date.now()/1000;
      membersRestart.value=result.requires_restart;
      message.value=result.message
    } catch(e) {
      await saveError(e,'members',fresh,progress)
    } finally {
      if(fresh())busy.value=''
    }
  }
  return {
    members, membersOriginal, membersRestart, membersDirty, addMember, saveMembers
  }
}
