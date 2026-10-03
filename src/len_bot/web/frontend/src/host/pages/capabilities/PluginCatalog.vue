<script setup>
import { computed, ref, watch } from 'vue'
import { pluginsApi } from '../../api/plugins.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { formatTime } from '../../time.js'
import ErrorNote from '../../components/ErrorNote.vue'

const props = defineProps({ snapshot: Object, busy: Boolean })
const emit = defineEmits(['dirty', 'install', 'configure', 'update'])
const catalog = useResource(() => pluginsApi.catalog()), source = ref(''), search = ref(''), category = ref(''), kind = ref('')
const selected = ref(null), sourceAction = useAction(), refresh = useAction()
watch(() => catalog.data.value?.url, value => { source.value = value || '' })
const dirty = computed(() => catalog.data.value !== null && source.value.trim() !== (catalog.data.value.url || ''))
watch(dirty, value => emit('dirty', value), { immediate: true })
const categories = computed(() => [{ title: '全部分类', value: '' }, ...[...new Set(catalog.data.value?.entries.map(entry => entry.category) || [])]
  .map(value => ({ title: value, value }))])
const entries = computed(() => (catalog.data.value?.entries || []).filter(entry =>
  (!category.value || entry.category === category.value) && (!kind.value || entry.install === kind.value)
  && [entry.name, entry.title, entry.description, ...entry.authors, ...entry.capabilities].join(' ').toLowerCase().includes(search.value.trim().toLowerCase())))
const installed = name => Boolean(props.snapshot.available[name]?.length)
const manifest = name => props.snapshot.available[name]?.length === 1 ? props.snapshot.available[name][0] : null
const running = name => props.snapshot.running.plugins.find(item => item.name === name)
const configured = name => name in props.snapshot.saved.plugins
const state = name => props.snapshot.saved.disabled.includes(name) ? '已停用'
  : running(name)?.status === 'failed' ? '加载失败' : running(name)?.status === 'running' ? '运行中'
    : configured(name) ? '已配置' : installed(name) ? '源码已可用' : '未安装'
async function reload() {
  const result = await refresh.run(() => pluginsApi.refreshCatalog())
  if (result) catalog.data.value = result
}
async function saveSource() {
  const result = await sourceAction.run(() => pluginsApi.catalogSource(source.value.trim() || null))
  if (!result) return
  catalog.data.value = result
  await reload()
}
function configure(name) { selected.value = null; emit('configure', name) }
function install(entry) { selected.value = null; emit('install', entry) }
function update(entry) { selected.value = null; emit('update', entry) }
</script>

<template>
  <div class="catalog">
    <section class="surface catalog-source">
      <div class="row"><h2>发现插件</h2><v-btn size="small" variant="text" :loading="refresh.busy.value" :disabled="dirty || sourceAction.busy.value" @click="reload">刷新目录</v-btn></div>
      <p v-if="catalog.data.value" class="muted">{{ catalog.data.value.source === 'builtin' ? '本版本内置目录' : catalog.data.value.url }} · {{ catalog.data.value.loaded_at ? `读取于 ${formatTime(catalog.data.value.loaded_at)}` : '点击刷新读取所选目录' }}</p>
      <details><summary>目录来源</summary>
        <v-text-field v-model="source" label="远程 JSON 目录地址" hint="留空使用本版本内置目录；远程内容仅在明确刷新时读取。" persistent-hint :disabled="sourceAction.busy.value || refresh.busy.value" />
        <v-btn size="small" variant="tonal" :disabled="!dirty || refresh.busy.value" :loading="sourceAction.busy.value" @click="saveSource">保存并读取</v-btn>
      </details>
      <ErrorNote v-if="catalog.error.value || sourceAction.error.value || refresh.error.value" title="目录读取或保存失败" :error="catalog.error.value || sourceAction.error.value || refresh.error.value" />
    </section>
    <div class="filters">
      <v-text-field v-model="search" label="搜索名称、用途、作者或能力" hide-details />
      <v-select v-model="category" :items="categories" label="分类" hide-details />
      <v-select v-model="kind" :items="[{title:'全部来源',value:''},{title:'内置',value:'builtin'},{title:'外部 Git',value:'git'}]" label="来源" hide-details />
    </div>
    <p v-if="catalog.data.value?.loaded_at && !entries.length" class="muted">没有匹配的插件。</p>
    <div class="cards">
      <article v-for="entry in entries" :key="entry.name" class="surface plugin-card">
        <div class="row"><h3>{{ entry.title }}</h3><v-chip size="small" variant="tonal">{{ state(entry.name) }}</v-chip></div>
        <p>{{ entry.description }}</p>
        <p class="muted">{{ entry.authors.join('、') }} · {{ entry.license }} · 目录 v{{ entry.version }} · 接口 {{ entry.interface }}</p>
        <p v-if="installed(entry.name)" class="muted">源码版本 {{ manifest(entry.name)?.version || '读取失败' }} · 运行版本 {{ running(entry.name)?.version || '未加载' }}</p>
        <div class="tags"><v-chip v-for="capability in entry.capabilities" :key="capability" size="small" variant="outlined">{{ capability }}</v-chip></div>
        <div class="row actions">
          <v-btn size="small" variant="text" @click="selected = entry">详情与用法</v-btn>
          <v-btn v-if="installed(entry.name)" size="small" variant="tonal" @click="configure(entry.name)">配置与选群</v-btn>
          <v-btn v-else-if="entry.install === 'git'" size="small" color="primary" :disabled="busy" @click="install(entry)">安装</v-btn>
          <v-btn v-if="entry.install === 'git' && manifest(entry.name)?.managed" size="small" variant="text" :disabled="busy" @click="update(entry)">更新至目录版本</v-btn>
        </div>
      </article>
    </div>
    <v-dialog :model-value="selected !== null" max-width="680" @update:model-value="value => { if (!value) selected = null }">
      <v-card v-if="selected" :title="selected.title">
        <v-card-text class="details">
          <p>{{ selected.description }}</p>
          <ol><li v-for="item in selected.usage" :key="item">{{ item }}</li></ol>
          <p>{{ selected.capabilities.join(' · ') }}</p>
          <p class="muted">{{ selected.name }} · v{{ selected.version }} · 接口 {{ selected.interface }} · {{ selected.license }}</p>
          <p v-if="selected.ref">安装定位：<code>{{ selected.ref }}</code></p>
          <p v-if="selected.install === 'builtin'">已随宿主提供，配置后选择启用场景。</p>
          <p v-else>安装后填写实际清单的配置表单，再选择启用场景。安装本身不向所有群启用。</p>
          <div class="row"><a v-if="selected.repository" :href="selected.repository" target="_blank" rel="noopener noreferrer">源码仓库</a><a v-if="selected.homepage" :href="selected.homepage" target="_blank" rel="noopener noreferrer">项目说明</a></div>
        </v-card-text>
        <v-card-actions><v-btn @click="selected = null">关闭</v-btn><v-spacer />
          <v-btn v-if="installed(selected.name)" color="primary" @click="configure(selected.name)">配置与选群</v-btn>
          <v-btn v-else-if="selected.install === 'git'" color="primary" :disabled="busy" @click="install(selected)">安装</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped>
.catalog,.catalog-source,.plugin-card,.details{display:grid;gap:12px}.catalog p,.catalog h2,.catalog h3{margin:0}.row,.tags{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.filters{display:grid;grid-template-columns:2fr 1fr 1fr;gap:12px}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,340px),1fr));gap:14px}.plugin-card{align-content:start}.plugin-card p,.details p{overflow-wrap:anywhere}.actions{margin-top:auto}.details ol{padding-left:22px;display:grid;gap:8px}.catalog-source summary{cursor:pointer;color:var(--muted)}@media(max-width:700px){.filters{grid-template-columns:1fr}}
</style>
