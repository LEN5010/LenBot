<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api.js'

const scope = ref('global-safe'), query = ref(''), assets = ref([]), file = ref(null)
const description = ref(''), tags = ref(''), error = ref(''), message = ref(''), busy = ref(false)
const selected = ref(null)
async function load() {
  error.value = ''
  try { assets.value = await api(`/api/media?scene_id=${encodeURIComponent(scope.value)}&query=${encodeURIComponent(query.value)}`) }
  catch (e) { error.value = e.message }
}
async function upload() {
  if (!file.value) return
  busy.value = true; error.value = ''; message.value = ''
  try {
    const form = new FormData(); form.append('file', file.value); form.append('scope', scope.value)
    form.append('description', description.value); form.append('tags', tags.value)
    const asset = await api('/api/media', { method: 'POST', body: form })
    select(asset); message.value = '素材已上传，可以在详情中选入固定目录'; await load()
  } catch (e) { error.value = e.message } finally { busy.value = false }
}
function select(asset) { selected.value = { ...asset, tagsText: (asset.tags || []).join(' '), palette_order: asset.palette_order ?? null } }
async function save() {
  busy.value = true; error.value = ''; message.value = ''
  try {
    const asset = selected.value
    const updated = await api(`/api/media/${encodeURIComponent(asset.id)}`, { method: 'POST', body: JSON.stringify({ scope: asset.scope, description: asset.description, tags: asset.tagsText.split(/\s+/).filter(Boolean), enabled: asset.enabled, palette_order: asset.palette_order }) })
    select(updated); message.value = '素材与固定目录设置已保存'; await load()
  } catch (e) { error.value = e.message } finally { busy.value = false }
}
function preview(asset) { return `/api/media/${encodeURIComponent(asset.id)}/file?scene_id=${encodeURIComponent(asset.scope)}` }
onMounted(load)
</script>

<template>
  <div class="media-view">
    <div class="toolbar"><div class="page-title"><h1>图片与表情</h1><p class="muted">管理可用素材和对话模型直接看到的固定表情目录。</p></div><button @click="load">刷新</button></div>
    <form class="toolbar" @submit.prevent="load"><input v-model="scope" placeholder="素材范围，例如 group:123" /><input v-model="query" placeholder="描述或标签" /><button>查询</button></form>
    <p class="muted">global-safe 素材可在所有场景使用，群内图片保持原场景范围。支持 PNG、JPEG、WEBP、GIF，最大 10MB。</p>
    <p v-if="error" class="tag bad" role="alert">{{ error }}</p><p v-if="message" class="tag ok" role="status">{{ message }}</p>
    <form class="panel" @submit.prevent="upload"><h2>添加运营素材</h2><div class="toolbar"><input type="file" accept="image/png,image/jpeg,image/webp,image/gif" @change="file = $event.target.files[0]" required /><input v-model="description" placeholder="描述用途或情绪" /><input v-model="tags" placeholder="标签，空格分隔" /><button class="primary" :disabled="busy || !file">上传到当前范围</button></div></form>
    <p class="muted">固定目录按运营顺序最多展示 20 项，模型可直接选择原图发送；未选入目录的素材仍可检索。停用素材会退出当前目录。</p>
    <div class="media-grid"><button v-for="asset in assets" :key="asset.id" class="panel media-card" :class="{ selected: selected?.id === asset.id }" @click="select(asset)"><img :src="preview(asset)" :alt="asset.description || '图片预览'" loading="lazy" /><strong>{{ asset.description || '群内图片' }}</strong><span class="muted">{{ asset.scope }} · {{ asset.enabled ? '已启用' : '已停用' }}</span><span v-if="asset.palette_order != null" class="tag" :class="asset.enabled ? 'ok' : 'warn'">目录顺序 {{ asset.palette_order }}</span><span class="tags-text">{{ asset.tags?.join(' · ') }}</span></button></div>
    <p v-if="!assets.length" class="muted">当前范围没有匹配素材</p>
    <section v-if="selected" class="panel"><div class="panel-header"><h2>素材详情</h2><button @click="selected = null">关闭</button></div><p class="muted asset-id">{{ selected.id }}</p><img class="detail-image" :src="preview(selected)" :alt="selected.description || '素材预览'" />
      <form v-if="selected.curated" @submit.prevent="save"><label>描述<input v-model="selected.description" maxlength="2000" /></label><label>标签<input v-model="selected.tagsText" /></label><label>固定目录顺序<select v-model="selected.palette_order"><option :value="null">不在固定目录中展示</option><option v-for="order in 20" :key="order" :value="order">{{ order }}</option></select></label><label class="checkbox-row"><input v-model="selected.enabled" type="checkbox" />启用素材</label><div class="toolbar"><button class="primary" :disabled="busy">{{ busy ? '正在保存…' : '保存素材与目录' }}</button><button v-if="selected.palette_order != null" type="button" @click="selected.palette_order = null">从目录移出</button></div></form>
      <p v-else class="muted">这张图片来自聊天事件，作为原始资料保留。</p>
      <details><summary>来源记录</summary><p>范围：{{ selected.scope }}</p><p class="asset-id">来源事件：{{ selected.source_event_id }}</p></details>
    </section>
  </div>
</template>

<style scoped>
.media-grid { display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:16px;margin:20px 0 }.media-card { display:flex;flex-direction:column;gap:8px;text-align:left;min-width:0;white-space:normal;color:var(--text) }.media-card.selected { border-color:var(--border-accent) }.media-card img { width:100%;height:150px;object-fit:contain;background:var(--bg) }.media-card strong,.tags-text,.asset-id { overflow-wrap:anywhere }.media-card .tag { align-self:flex-start }.tags-text { color:var(--muted);font-size:.8rem }.detail-image { max-width:100%;max-height:300px;object-fit:contain }label { display:flex;flex-direction:column;gap:7px;margin:14px 0 }label input,label select { width:100% }.checkbox-row { flex-direction:row;align-items:center }.checkbox-row input { width:auto }details { color:var(--muted);font-size:.8rem;margin-top:16px }summary { cursor:pointer }
</style>
