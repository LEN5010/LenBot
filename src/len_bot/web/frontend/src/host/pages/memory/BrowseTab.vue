<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { mdiFileDocumentOutline, mdiFolderOutline } from '@mdi/js'
import { api, queryString } from '../../../api.js'
import { useResource } from '../../../composables/useResource.js'
import ErrorNote from '../../components/ErrorNote.vue'
import FilePanel from './FilePanel.vue'
import DirectorySummary from './DirectorySummary.vue'

const props = defineProps({ scene: { type: String, required: true }, state: { type: Object, required: true } })
const emit = defineEmits(['dirty'])
const route = useRoute()
const parentOf = path => path.split('/').slice(0, -1).join('/')
const startPath = typeof route.query.path === 'string' ? route.query.path : null
const scope = ref(route.query.scope === 'public' && props.state.public_readable ? 'public' : 'scene')
const directory = ref(startPath ? parentOf(startPath) : '')
const file = ref(startPath), creating = ref(false), fileDirty = ref(false)
const nodes = ref([]), hasMore = ref(false)
watch(fileDirty, value => emit('dirty', value), { immediate: true })

const can = action => props.state.actions.includes(action)
const writable = computed(() => can('write') && (scope.value === 'scene' || props.state.public_writable))
const listing = useResource(more => api('/api/host/memory/browse?' + queryString({ scene: props.scene, path: directory.value,
  scope: scope.value, offset: String(more === true ? nodes.value.length : 0), limit: '50' })).then(page => ({ page, more: more === true })),
{ immediate: can('browse') })
watch(() => listing.data.value, value => {
  if (!value) return
  nodes.value = value.more ? [...nodes.value, ...value.page.nodes] : value.page.nodes
  hasMore.value = value.page.has_more
})
const crumbs = computed(() => {
  const parts = directory.value ? directory.value.split('/') : []
  return [{ title: scope.value === 'public' ? '公共' : '本群', path: '' },
    ...parts.map((name, index) => ({ title: name, path: parts.slice(0, index + 1).join('/') }))]
})

const leave = () => !fileDirty.value || window.confirm('放弃没保存的修改？')
function openDirectory(path) {
  if (!leave()) return
  directory.value = path
  file.value = null
  creating.value = false
  fileDirty.value = false
  listing.reload()
}
function openFile(path) {
  if (path === file.value || !leave()) return
  file.value = path
  creating.value = false
}
function changeScope(value) {
  if (value === scope.value || !leave()) return
  scope.value = value
  directory.value = ''
  file.value = null
  creating.value = false
  fileDirty.value = false
  listing.reload()
}
function create() {
  if (!leave()) return
  file.value = null
  creating.value = true
}
function changed(path) {
  // A saved new file becomes the open file; a removed file closes the panel.
  file.value = path
  creating.value = false
  listing.reload()
}
</script>

<template>
  <div class="browse">
    <section class="surface tree">
      <div class="tree-head">
        <v-btn-toggle v-if="state.public_readable" :model-value="scope" mandatory density="compact" color="primary" @update:model-value="changeScope">
          <v-btn value="scene">本群</v-btn><v-btn value="public">公共</v-btn></v-btn-toggle>
        <v-btn v-if="writable" size="small" variant="tonal" color="primary" @click="create">新建</v-btn>
      </div>
      <v-breadcrumbs :items="crumbs" density="compact" class="crumbs">
        <template #item="{ item }"><a href="#" @click.prevent="openDirectory(item.path)">{{ item.title }}</a></template>
      </v-breadcrumbs>
      <ErrorNote v-if="listing.error.value" title="读取目录失败" :error="listing.error.value" />
      <p v-if="!can('browse')" class="muted">当前的记忆方式不支持按目录浏览，可以用搜索找到记忆。</p>
      <p v-else-if="!nodes.length && !listing.loading.value && !listing.error.value" class="muted">这里还是空的</p>
      <v-list density="compact" class="nodes" nav>
        <v-list-item v-for="node in nodes" :key="node.path" :active="node.path === file" :title="node.name"
          :prepend-icon="node.is_dir ? mdiFolderOutline : mdiFileDocumentOutline"
          @click="node.is_dir ? openDirectory(node.path) : openFile(node.path)" />
      </v-list>
      <v-btn v-if="hasMore" variant="text" size="small" :loading="listing.loading.value" @click="listing.reload(true)">显示更多</v-btn>
    </section>
    <div class="detail">
      <FilePanel v-if="file || creating" :key="`${scope}:${file}:${creating}`" :scene="scene" :scope="scope" :path="file"
        :directory="directory" :state="state" @dirty="value => fileDirty = value" @changed="changed" />
      <template v-else>
        <p class="surface muted">选择一个文件查看内容</p>
        <DirectorySummary :key="`${scope}:${directory}`" :scene="scene" :scope="scope" :path="directory" :state="state" />
      </template>
    </div>
  </div>
</template>

<style scoped>
.browse{display:grid;grid-template-columns:minmax(240px,320px) 1fr;gap:16px;align-items:start}
.tree{display:grid;gap:4px;padding:12px}
.tree-head{display:flex;justify-content:space-between;align-items:center;gap:8px}
.crumbs{padding:4px 0;flex-wrap:wrap}
.nodes{padding:0;background:transparent}
.detail{min-width:0;display:grid;gap:16px}
@media(max-width:800px){.browse{grid-template-columns:1fr}}
</style>
