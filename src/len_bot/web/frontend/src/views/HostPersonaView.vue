<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import { api, sceneName } from '../api.js'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'

const route = useRoute(), router = useRouter()
const host = ref(null), scene = ref(''), snapshot = ref(null)
const stickerSnapshot = ref(null), stickerLoading = ref(false), stickerError = ref(''), stickerImageErrors = ref({})
const file = ref('persona.yaml'), draft = ref('')
const loading = ref(false), hostLoading = ref(false), saving = ref(false)
const readError = ref(''), saveError = ref(''), savedNotice = ref('')
const files = [
  { value: 'persona.yaml', title: '角色元数据 · persona.yaml', note: '身份、行为、自称、别名、风格、工具与技能许可的 YAML 原文。必须保留角色文件合同的结构。' },
  { value: 'voice.md', title: '表达风格 · voice.md', note: '表达器使用的完整原文；换行与空行按输入保存。' },
  { value: 'boundaries.md', title: '身份边界 · boundaries.md', note: '角色身份边界的完整原文。' },
  { value: 'examples.yaml', title: '人工样例 · examples.yaml', note: '人工样例 YAML 原文；样例不是聊天经历。' },
]
const options = computed(() => host.value?.scenes.map(item => ({
  title: `${sceneName(item.scene)} · ${item.persona.name}`, value: item.scene,
})) || [])
const selectedFile = computed(() => files.find(item => item.value === file.value))
const dirty = computed(() => snapshot.value !== null && draft.value !== snapshot.value.saved[file.value])
useUnsavedChanges(dirty)
onBeforeRouteUpdate(() => !dirty.value || window.confirm('有尚未保存的角色文件草稿。放弃并打开另一场景？'))
let selectionEpoch = 0
const selection = () => `${scene.value}\u0000${selectionEpoch}`
const beginRead = useRequestGuard(selection)
const beginHost = useRequestGuard()
const beginSave = useRequestGuard(selection)
const beginStickers = useRequestGuard(selection)

function stickerUrl(name) {
  return `/api/host/scenes/${encodeURIComponent(scene.value)}/persona-stickers/image?file=${encodeURIComponent(name)}`
}
async function readStickers() {
  if (!scene.value) return
  const target = scene.value, fresh = beginStickers()
  stickerLoading.value = true
  try {
    const value = await api(`/api/host/scenes/${encodeURIComponent(target)}/persona-stickers`)
    if (!fresh()) return
    stickerSnapshot.value = value
    stickerImageErrors.value = {}
    stickerError.value = ''
  } catch (error) {
    if (fresh()) stickerError.value = error.message
  } finally {
    if (fresh()) stickerLoading.value = false
  }
}

function selectFile(next) {
  if (next === file.value) return
  if (dirty.value && !window.confirm('放弃当前文件尚未保存的原文并切换文件？')) return
  file.value = next
  draft.value = snapshot.value?.saved[next] ?? ''
  saveError.value = ''
  savedNotice.value = ''
}
function selectScene(next, fromRoute = false) {
  if (!host.value?.scenes.some(item => item.scene === next) || next === scene.value) return
  if (!fromRoute && dirty.value && !window.confirm('放弃当前文件尚未保存的原文并切换场景？')) return
  scene.value = next
  ++selectionEpoch
  snapshot.value = null
  stickerSnapshot.value = null
  stickerImageErrors.value = {}
  stickerError.value = ''
  stickerLoading.value = false
  draft.value = ''
  readError.value = ''
  saveError.value = ''
  savedNotice.value = ''
  loading.value = false
  if (!fromRoute) router.replace({ name: 'host-persona', query: { scene: next } })
  readFiles(false)
  readStickers()
}
async function readFiles(confirmDiscard = true) {
  if (confirmDiscard && dirty.value && !window.confirm('放弃当前文件尚未保存的原文，重新读取角色包？')) return
  const target = scene.value
  const fresh = beginRead()
  loading.value = true
  try {
    const value = await api(`/api/host/scenes/${encodeURIComponent(target)}/persona-files`)
    if (!fresh()) return
    snapshot.value = value
    draft.value = value.saved[file.value]
    readError.value = ''
    saveError.value = ''
    savedNotice.value = ''
  } catch (error) {
    if (fresh()) readError.value = error.message
  } finally {
    if (fresh()) loading.value = false
  }
}
async function readHost() {
  const fresh = beginHost()
  hostLoading.value = true
  try {
    const value = await api('/api/host/state')
    if (!fresh()) return
    host.value = value
    const requested = route.query.scene
    scene.value = typeof requested === 'string' && value.scenes.some(item => item.scene === requested)
      ? requested : value.scenes[0].scene
    if (scene.value !== route.query.scene) router.replace({ name: 'host-persona', query: { scene: scene.value } })
    await readFiles(false)
    await readStickers()
  } catch (error) {
    if (fresh()) readError.value = error.message
  } finally {
    if (fresh()) hostLoading.value = false
  }
}
async function save() {
  if (!dirty.value || saving.value || loading.value) return
  const targetScene = scene.value, targetFile = file.value, content = draft.value
  const fresh = beginSave()
  saving.value = true
  saveError.value = ''
  savedNotice.value = ''
  try {
    const value = await api(`/api/host/scenes/${encodeURIComponent(targetScene)}/persona-files/${targetFile}`, {
      method: 'PUT', body: JSON.stringify({ content }),
    })
    if (!fresh() || file.value !== targetFile) return
    snapshot.value = value
    draft.value = value.saved[targetFile]
    savedNotice.value = value.restart_required
      ? '角色文件已保存；当前运行角色与人工样例不变，重启宿主后生效。'
      : '角色文件已保存；与当前运行角色一致。'
  } catch (error) {
    if (fresh() && file.value === targetFile) saveError.value = error.status >= 400 && error.status < 500
      ? `保存未被接受：${error.message}`
      : `保存结果未确认：${error.message} 草稿已保留；请重读角色包核对，不会自动重试。`
  } finally {
    if (saving.value) saving.value = false
  }
}
onMounted(readHost)
watch(() => route.query.scene, value => {
  if (typeof value === 'string' && host.value && value !== scene.value) selectScene(value, true)
})
</script>

<template>
  <div class="page-stack host-persona">
    <header class="page-intro"><div><p class="eyebrow">独立多场景宿主</p><h1>角色文件</h1>
      <p class="muted">只编辑当前配置角色包的四个真实文件。单次仅保存选中的原文；保存前完整校验角色和所有共用场景的工具依赖，不热加载运行中的角色。</p></div>
      <v-btn variant="outlined" :loading="loading || hostLoading" :disabled="saving || !scene" @click="readFiles()">重读角色包</v-btn></header>
    <v-alert v-if="readError" type="error" variant="tonal" role="alert" :title="snapshot?'读取失败 · 保留上次草稿':'读取角色文件失败'">{{ readError }}</v-alert>
    <v-alert v-if="saveError" type="error" variant="tonal" role="alert">{{ saveError }}</v-alert>
    <v-alert v-if="savedNotice" type="success" variant="tonal" role="status">{{ savedNotice }}</v-alert>
    <section class="surface"><h2>配置场景与共享角色</h2>
      <v-select :model-value="scene" :items="options" label="选择场景" hide-details="auto" :disabled="!host || saving" @update:model-value="selectScene" />
      <p v-if="(loading || hostLoading) && !snapshot" class="muted" role="status">正在读取角色包原文和运行角色…</p>
      <template v-if="snapshot"><p>当前运行角色：<strong>{{ snapshot.running.name }}</strong> <span class="muted">{{ snapshot.running.id }}</span></p>
        <p class="muted">同一角色包用于：{{ snapshot.affected_scenes.map(sceneName).join('、') }}。本页保存会影响这些场景的下次启动，不会修改当前会话。</p>
        <v-chip variant="tonal" :color="snapshot.restart_required?'warning':'info'">{{ snapshot.restart_required?'角色包保存值待重启':'角色包与运行值一致' }}</v-chip>
      </template>
    </section>
    <section v-if="scene" class="surface" aria-labelledby="stickers-title">
      <div class="section-heading"><h2 id="stickers-title">当前运行角色的表情目录</h2>
        <v-btn variant="outlined" :loading="stickerLoading" :disabled="stickerLoading" @click="readStickers">重读目录</v-btn></div>
      <p class="muted">只展示本次启动已加载的角色原件，不从磁盘热加载。平台确认次数只计该场景已保存为 sent 的图片发送，不代表 QQ 客户端已收到。</p>
      <v-chip v-if="snapshot" variant="tonal" :color="snapshot.stickers_restart_required?'warning':'info'">{{ snapshot.stickers_restart_required?'保存目录与运行表情不同 · 待重启':'保存目录与运行表情一致' }}</v-chip>
      <v-alert v-if="stickerError" type="error" variant="tonal" role="alert" :title="stickerSnapshot?'目录读取失败 · 保留上次快照':'目录读取失败'">{{ stickerError }}</v-alert>
      <p v-if="stickerLoading && !stickerSnapshot" role="status">正在读取当前角色表情目录…</p>
      <p v-if="stickerSnapshot && !stickerSnapshot.stickers.length" class="muted">当前运行角色没有已加载的表情原件。</p>
      <ul v-if="stickerSnapshot?.stickers.length" class="sticker-grid">
        <li v-for="item in stickerSnapshot.stickers" :key="item.file" class="sticker-card">
          <a :href="stickerUrl(item.file)" target="_blank" rel="noopener" :aria-label="`打开表情原件：${item.description}`">
            <span v-if="stickerImageErrors[item.file]" class="image-error" role="status">原件当前不可读取；可打开链接查看接口错误。</span>
            <img v-else :src="stickerUrl(item.file)" :alt="item.description" loading="lazy" :width="item.width" :height="item.height" @error="stickerImageErrors[item.file]=true" />
          </a>
          <div><strong>{{ item.description }}</strong><p class="muted">{{ item.file }} · {{ item.width }}×{{ item.height }} · {{ item.mime_type }}<span v-if="item.animated"> · 动图</span></p>
            <p class="muted">情绪：{{ item.emotions.join('、') || '未标注' }}；标签：{{ item.tags.join('、') || '未标注' }}</p>
            <p class="muted">本场景平台确认发送 {{ item.confirmed_uses }} 次 · {{ item.bytes }} 字节</p></div>
        </li>
      </ul>
    </section>
    <template v-if="snapshot">
      <form class="surface editor" @submit.prevent="save"><div class="section-heading"><h2>编辑角色文件原文</h2><span class="muted">只保存所选文件</span></div>
        <v-select :model-value="file" :items="files" item-title="title" item-value="value" label="选择文件" hide-details="auto" :disabled="saving || loading" @update:model-value="selectFile" />
        <p class="muted">{{ selectedFile.note }}</p>
        <v-textarea v-model="draft" :label="`${file} 原文`" rows="18" auto-grow spellcheck="false" :disabled="saving || loading" hide-details="auto" class="source-editor" />
        <p v-if="dirty" class="dirty-note" role="status">此文件原文有未保存修改。</p>
        <div class="form-actions"><v-btn type="submit" color="primary" :loading="saving" :disabled="!dirty || loading">保存所选文件</v-btn>
          <span class="muted">失败时保留原文草稿与接口错误，不自动重试；根配置的角色路径不在此修改。</span></div>
      </form>
      <section class="surface"><h2>当前运行角色摘要 · 只读</h2>
        <dl class="role-facts"><div><dt>身份简述</dt><dd>{{ snapshot.running.brief }}</dd></div>
          <div><dt>行为风格</dt><dd>{{ snapshot.running.behavior }}</dd></div>
          <div><dt>说话风格</dt><dd>{{ snapshot.running.voice }}</dd></div>
          <div><dt>身份边界</dt><dd>{{ snapshot.running.boundaries }}</dd></div>
          <div><dt>工具许可</dt><dd>{{ snapshot.running.tools==='all'?'不逐项限制':snapshot.running.tools.join('、')||'无' }}</dd></div>
          <div><dt>人工样例</dt><dd>{{ snapshot.running.examples.length }} 条；角色文件保存后这里仍为当前运行值。</dd></div></dl>
        <p class="muted">只调整许可也可到 <RouterLink :to="{name:'host-capabilities',query:{scene}}">工具能力</RouterLink>；场景补充在 <RouterLink :to="{name:'host-settings',query:{scene}}">群聊设置与角色</RouterLink>。</p>
      </section>
    </template>
  </div>
</template>

<style scoped>
.host-persona{max-width:1200px;margin-inline:auto}
.page-intro,.section-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:14px;flex-wrap:wrap}
.page-intro>div{min-width:0;flex:1 1 480px}.page-intro h1{margin:0 0 10px}
.eyebrow{font-size:12px;letter-spacing:.08em;color:var(--primary);font-weight:700;margin:0 0 5px}
.surface{min-width:0;overflow-wrap:anywhere}.surface h2{font-size:18px;margin:0 0 14px}
.editor>p{margin:12px 0}.source-editor :deep(textarea){font-family:ui-monospace,SFMono-Regular,Consolas,monospace;line-height:1.55;white-space:pre-wrap;overflow-wrap:anywhere}
.role-facts{display:grid;gap:12px}.role-facts>div{border-top:1px solid var(--line);padding-top:10px}.role-facts dt{font-weight:700}.role-facts dd{margin:6px 0 0;white-space:pre-wrap;overflow-wrap:anywhere}
.dirty-note{border-left:3px solid var(--primary);background:var(--selected-bg);padding:8px 12px}
.host-persona :deep(.v-btn){min-height:44px}.host-persona :deep(.v-alert),.host-persona .muted{overflow-wrap:anywhere}
.sticker-grid{list-style:none;margin:16px 0 0;padding:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr));gap:14px}
.sticker-card{min-width:0;border:1px solid var(--line);border-radius:10px;padding:12px;overflow-wrap:anywhere}
.sticker-card>a{display:grid;place-items:center;width:100%;height:180px;border-radius:8px;background:var(--list-heading-bg);overflow:hidden}
.sticker-card>a:focus-visible{outline:3px solid var(--primary);outline-offset:2px}
.sticker-card img{display:block;max-width:100%;max-height:100%;width:auto;height:auto;object-fit:contain}
.sticker-card strong{display:block;margin-top:10px}.sticker-card p{font-size:13px;margin:5px 0;white-space:pre-wrap;overflow-wrap:anywhere}
.image-error{text-align:center;padding:12px;color:var(--error-text);font-size:13px}
@media(max-width:600px){.page-intro{display:grid}.page-intro>.v-btn{width:100%}.surface{padding:16px}}
</style>
