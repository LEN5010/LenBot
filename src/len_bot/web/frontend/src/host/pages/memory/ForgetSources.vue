<script setup>
import { computed, ref, watch } from 'vue'
import { api, queryString } from '../../../api.js'
import { useResource } from '../../../composables/useResource.js'
import ErrorNote from '../../ui/ErrorNote.vue'
import LoadMore from '../../ui/LoadMore.vue'

const props = defineProps({ scene: { type: String, required: true } })
const emit = defineEmits(['selection'])
const text = ref(''), who = ref('')
const rows = ref([]), chosen = ref(new Map())
const limit = 500
let asked = {}
const page = useResource(async more => {
  const args = { scene: props.scene, offset: String(more === true ? page.data.value.next_offset : 0) }
  if (asked.text) args.query = asked.text
  if (asked.who) args.who = asked.who
  if (more === true) args.snapshot = String(page.data.value.snapshot)
  return { ...(await api('/api/host/memory/sources?' + queryString(args))), more: more === true }
}, { immediate: false })
watch(() => page.data.value, value => { if (value) rows.value = value.more ? [...rows.value, ...value.previews] : value.previews })
const list = computed(() => [...chosen.value.values()])

function search() {
  asked = { text: text.value.trim(), who: who.value.trim() }
  page.reload()
}
function toggle(item, on) {
  const next = new Map(chosen.value)
  if (on) next.set(item.record, item)
  else next.delete(item.record)
  chosen.value = next
  emit('selection', [...next.values()])
}
</script>

<template>
  <div class="sources">
    <h3>要跳过的聊天消息（可选）</h3>
    <form class="search" @submit.prevent="search">
      <v-text-field v-model="text" label="消息里的文字" />
      <v-text-field v-model="who" label="发言人账号" placeholder="onebot:QQ号" />
      <v-btn type="submit" variant="outlined" :loading="page.loading.value">查找</v-btn>
    </form>
    <ErrorNote v-if="page.error.value" title="查找消息失败" :error="page.error.value" />
    <p v-if="page.data.value && !rows.length" class="muted">没有找到消息</p>
    <ul class="plain-list rows">
      <li v-for="item in rows" :key="item.record">
        <v-checkbox :model-value="item.excluded || chosen.has(item.record)" :disabled="item.excluded || (!chosen.has(item.record) && chosen.size >= limit)"
          @update:model-value="value => toggle(item, value)">
          <template #label><span class="row-text"><span v-if="item.excluded" class="muted small">已经跳过</span>{{ item.text }}</span></template>
        </v-checkbox>
      </li>
    </ul>
    <LoadMore v-if="page.data.value?.next_offset != null" :loading="page.loading.value" @more="page.reload(true)" />
    <p v-if="list.length" class="muted small">已选 {{ list.length }} 条{{ list.length >= limit ? '，一次最多 500 条' : '' }}</p>
  </div>
</template>

<style scoped>
.sources{display:grid;gap:var(--sp-2);border-top:1px solid var(--line);padding-top:var(--sp-3)}
.sources p{margin:0}
.search{display:grid;grid-template-columns:1fr 160px auto;gap:var(--sp-2);align-items:center}
.rows{max-height:320px;overflow:auto}
.row-text{display:grid;gap:2px;white-space:pre-wrap;overflow-wrap:anywhere}
@media(max-width:600px){.search{grid-template-columns:1fr}}
</style>
