<script setup>
import { ref } from 'vue'
import { api } from '../../../api.js'
import { useResource } from '../../../composables/useResource.js'
import Panel from '../../ui/Panel.vue'
import ResourceState from '../../ui/ResourceState.vue'
import ObjectList from '../../ui/ObjectList.vue'
import ObjectRow from '../../ui/ObjectRow.vue'
import DevOnly from '../../ui/DevOnly.vue'

const props = defineProps({ scene: { type: String, required: true } })
defineEmits(['open'])
const text = ref('')
const result = useResource(query => api('/api/host/memory/search', { method: 'POST',
  body: JSON.stringify({ scene: props.scene, query, limit: 10 }) }), { immediate: false })
</script>

<template>
  <Panel title="搜索记忆">
    <form class="search-row" @submit.prevent="result.reload(text.trim())">
      <v-text-field v-model="text" label="想找什么" />
      <v-btn type="submit" color="primary" :loading="result.loading.value" :disabled="!text.trim()">搜索</v-btn>
    </form>
    <ResourceState :resource="result" error-title="搜索失败" :empty="!result.data.value?.hits.length" empty-text="没有找到相关的记忆" compact v-slot="{ data }">
      <ObjectList divided>
        <ObjectRow v-for="hit in data.hits" :key="`${hit.scope}:${hit.path}`" :title="`${hit.scope === 'public' ? '公共 · ' : ''}${hit.path}`" clickable @click="$emit('open', hit)">
          <p class="preview">{{ hit.preview }}</p>
          <DevOnly><span class="muted small">分数 {{ hit.score ?? '—' }} · {{ hit.total_chars ?? '—' }} 字</span></DevOnly>
        </ObjectRow>
      </ObjectList>
    </ResourceState>
  </Panel>
</template>

<style scoped>
.search-row{display:flex;gap:var(--sp-2);align-items:center}
.search-row > :first-child{flex:1}
.preview{margin:var(--sp-1) 0 0;white-space:pre-wrap;overflow-wrap:anywhere;font-weight:400}
</style>
