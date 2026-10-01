<script setup>
import { ref } from 'vue'
import { api } from '../../../api.js'
import { useResource } from '../../../composables/useResource.js'
import ErrorNote from '../../components/ErrorNote.vue'
import DevOnly from '../../components/DevOnly.vue'

const props = defineProps({ scene: { type: String, required: true } })
defineEmits(['open'])
const text = ref('')
const result = useResource(query => api('/api/host/memory/search', { method: 'POST',
  body: JSON.stringify({ scene: props.scene, query, limit: 10 }) }), { immediate: false })
</script>

<template>
  <section class="surface search">
    <form class="search-row" @submit.prevent="result.reload(text.trim())">
      <v-text-field v-model="text" label="搜索记忆" hide-details density="comfortable" />
      <v-btn type="submit" color="primary" :loading="result.loading.value" :disabled="!text.trim()">搜索</v-btn>
    </form>
    <ErrorNote v-if="result.error.value" title="搜索失败" :error="result.error.value" />
    <p v-if="result.data.value && !result.data.value.hits.length" class="muted">没有找到相关的记忆</p>
    <ul class="hits">
      <li v-for="hit in result.data.value?.hits || []" :key="`${hit.scope}:${hit.path}`">
        <a href="#" @click.prevent="$emit('open', hit)">{{ hit.scope === 'public' ? '公共 · ' : '' }}{{ hit.path }}</a>
        <p>{{ hit.preview }}</p>
        <DevOnly><span class="muted">分数 {{ hit.score ?? '—' }} · {{ hit.total_chars ?? '—' }} 字</span></DevOnly>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.search{display:grid;gap:12px}
.search-row{display:flex;gap:8px;align-items:center}
.search-row>:first-child{flex:1}
.hits{list-style:none;margin:0;padding:0;display:grid}
.hits li{padding:10px 0;border-bottom:1px solid var(--line);overflow-wrap:anywhere}
.hits li:last-child{border-bottom:0}
.hits p{margin:4px 0 0;white-space:pre-wrap}
</style>
