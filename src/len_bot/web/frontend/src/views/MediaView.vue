<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api.js'
const scope = ref('global-safe'), query = ref(''), assets = ref([]), file = ref(null)
const description = ref(''), tags = ref(''), error = ref(''), busy = ref(false)
const selected = ref(null), question = ref('描述图片中可确认的信息'), analysis = ref(null)
async function load() {
  try { assets.value = await api(`/api/media?scene_id=${encodeURIComponent(scope.value)}&query=${encodeURIComponent(query.value)}`); error.value = '' }
  catch (e) { error.value = e.message }
}
async function upload() {
  busy.value = true; error.value = ''
  try {
    const form = new FormData(); form.append('file', file.value); form.append('scope', scope.value)
    form.append('description', description.value); form.append('tags', tags.value)
    await api('/api/media', { method: 'POST', body: form }); await load()
  } catch (e) { error.value = e.message } finally { busy.value = false }
}
function select(asset) { selected.value = { ...asset, tagsText: asset.tags.join(' ') }; analysis.value = null }
async function save() {
  try { const asset = selected.value; await api(`/api/media/${asset.id}`, { method: 'POST', body: JSON.stringify({ scope: asset.scope, description: asset.description, tags: asset.tagsText.split(/\s+/).filter(Boolean), enabled: asset.enabled }) }); await load() }
  catch (e) { error.value = e.message }
}
async function inspect() {
  busy.value = true; error.value = ''
  try { analysis.value = await api(`/api/media/${selected.value.id}/inspect`, { method: 'POST', body: JSON.stringify({ scene_id: scope.value, question: question.value }) }) }
  catch (e) { error.value = e.message } finally { busy.value = false }
}
function preview(asset) { return `/api/media/${asset.id}/file?scene_id=${encodeURIComponent(asset.scope)}` }
onMounted(load)
</script>
<template>
  <div>
    <div class="toolbar"><h1>图片与表情</h1><input v-model="scope" placeholder="素材范围，例如 group:123" /><input v-model="query" placeholder="描述或标签" /><button @click="load">查询</button></div>
    <p class="muted">global-safe 素材可在所有场景使用；群内图片保持原场景范围。支持 PNG、JPEG、WEBP、GIF，最大10MB。</p>
    <p v-if="error" class="tag bad" role="alert">{{ error }}</p>
    <form class="panel" @submit.prevent="upload"><h2>添加运营素材</h2><div class="toolbar"><input type="file" accept="image/png,image/jpeg,image/webp,image/gif" @change="file = $event.target.files[0]" required /><input v-model="description" placeholder="描述用途或情绪" /><input v-model="tags" placeholder="标签，空格分隔" /><button class="primary" :disabled="busy || !file">上传到当前范围</button></div></form>
    <div class="media-grid"><button class="panel media-card" v-for="asset in assets" :key="asset.id" @click="select(asset)"><img :src="preview(asset)" :alt="asset.description || '图片预览'" loading="lazy" /><strong>{{ asset.description || '群内图片' }}</strong><span class="muted">{{ asset.scope }} · {{ asset.enabled ? '已启用' : '已停用' }}</span><span>{{ asset.tags.join(' · ') }}</span></button></div>
    <p v-if="!assets.length" class="muted">当前范围没有匹配素材</p>
    <section v-if="selected" class="panel"><h2>素材详情</h2><p class="muted">{{ selected.id }}</p><img class="detail-image" :src="preview(selected)" :alt="selected.description" />
      <div v-if="selected.curated"><label>描述<input v-model="selected.description" /></label><label>标签<input v-model="selected.tagsText" /></label><label><input type="checkbox" v-model="selected.enabled" /> 启用素材</label><button @click="save">保存</button></div>
      <div class="toolbar"><input v-model="question" placeholder="想从图中了解什么" /><button :disabled="busy" @click="inspect">{{ busy ? '正在查看…' : '测试图片理解' }}</button></div>
      <div v-if="analysis"><span class="tag" :class="analysis.status === 'ok' ? 'ok' : 'bad'">{{ analysis.status }}</span><p class="analysis">{{ analysis.content }}</p><p v-if="analysis.status === 'ok'" class="muted">这是视觉模型的解释，需要结合原图核对。</p></div>
    </section>
  </div>
</template>
<style scoped>.media-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(180px,1fr)); gap:16px; margin:20px 0 }.media-card { display:flex; flex-direction:column; gap:8px; text-align:left; min-width:0; white-space:normal; color:var(--text) }.media-card img { width:100%; height:150px; object-fit:contain; background:var(--bg) }.detail-image { max-width:100%; max-height:300px; object-fit:contain } label { display:block; margin:10px 0 } .analysis { white-space:pre-wrap }</style>
