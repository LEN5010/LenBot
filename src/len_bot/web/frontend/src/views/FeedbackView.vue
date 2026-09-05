<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api.js'
const scene = ref('group:126300994'), rows = ref([]), selected = ref(null), error = ref('')
const human = ref(''), kind = ref('naturalness'), acceptable = ref('unknown'), comment = ref('')
const labels = { naturalness: '自然度', followup: '追问', correction: '纠正', positive: '明确认可', negative: '明确不满', unrelated: '无关聊天', unknown: '未知' }
async function load() { try { rows.value = await api('/api/feedback?scene_id='+encodeURIComponent(scene.value)); error.value = '' } catch(e) { error.value=e.message } }
function select(row) { selected.value=row; human.value=''; kind.value='naturalness'; acceptable.value='unknown'; comment.value='' }
async function save() {
  try {
    await api('/api/feedback', { method:'POST', body:JSON.stringify({ scene_id:scene.value, sent_event_id:selected.value.sent_event_id,
      human_event_id:human.value || null, kind:kind.value, acceptable:acceptable.value==='unknown' ? null : acceptable.value==='yes', comment:comment.value }) })
    await load(); selected.value=rows.value.find(r=>r.sent_event_id===selected.value.sent_event_id)
  } catch(e) { error.value=e.message }
}
onMounted(load)
</script>
<template>
  <div>
    <div class="toolbar"><h1>回复效果</h1><input v-model="scene" aria-label="群聊范围" /><button @click="load">刷新</button></div>
    <p class="muted">真实送达后观察五分钟或十五条人类消息。引用关系自动记录；追问、纠正和态度由人工确认，无人回应保持未知。</p>
    <p v-if="error" class="tag bad" role="alert">{{ error }}</p>
    <div class="panel"><table><thead><tr><th>回复</th><th>发送 ID</th><th>观察</th><th></th></tr></thead><tbody>
      <tr v-for="row in rows" :key="row.sent_event_id"><td>{{ row.sent.content }}</td><td>{{ row.message_id }}</td><td>{{ row.human_count }} 条 · {{ row.window_closed ? '窗口已结束' : '观察中' }} · {{ row.response_status==='unknown' ? '回应未知' : '有引用或已标注反馈' }}</td><td><button @click="select(row)">评阅</button></td></tr>
      <tr v-if="!rows.length"><td colspan="4" class="muted">暂无真实发送记录。Shadow 候选在试运行页面评阅。</td></tr>
    </tbody></table></div>
    <div v-if="selected" class="panel detail">
      <h2>后续对话与人工评价</h2><p>{{ selected.sent.content }}</p>
      <p v-for="item in selected.followups" :key="item.event_id"><strong>{{ item.actor_id }}</strong> {{ item.quoted ? '引用此回复：' : '窗口内发言：' }}{{ item.payload.raw_text || item.payload.content }}</p>
      <p v-if="!selected.followups.length" class="muted">没有后续消息，不能据此判定满意。</p>
      <label>反馈原话<select v-model="human"><option value="">不关联原话（仅评自然度或未知）</option><option v-for="item in selected.followups" :key="item.event_id" :value="item.event_id">{{ item.actor_id }}：{{ item.payload.raw_text || item.payload.content }}</option></select></label>
      <div class="toolbar"><label>类别<select v-model="kind"><option v-for="(name,id) in labels" :key="id" :value="id">{{ name }}</option></select></label><label>自然度<select v-model="acceptable"><option value="unknown">未评</option><option value="yes">可接受</option><option value="no">不可接受</option></select></label></div>
      <label>评阅说明<textarea v-model="comment" rows="3" placeholder="是否接住本意、语气是否合适、有无重复或强行接梗、结束是否自然" /></label><button @click="save">保存人工评价</button>
      <p v-for="(item,i) in selected.labels" :key="i" class="muted">{{ item.reviewer }} · {{ labels[item.kind] }} · {{ item.acceptable===null ? '未评自然度' : item.acceptable ? '可接受' : '不可接受' }} · {{ item.comment }}</p>
      <p class="muted">这里的评价用于验收，不会自动修改人格、记忆或任务。</p>
    </div>
  </div>
</template>
<style scoped>.detail{margin-top:20px} label{display:block;margin:12px 0} select,textarea{display:block;width:100%;margin-top:5px} td:first-child{max-width:450px;overflow-wrap:anywhere}</style>
