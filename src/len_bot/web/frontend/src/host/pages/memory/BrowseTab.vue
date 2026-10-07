<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { mdiFileDocumentOutline, mdiFolderOutline } from '@mdi/js'
import { api, queryString } from '../../../api.js'
import { useResource } from '../../../composables/useResource.js'
import { confirm } from '../../../composables/useConfirm.js'
import MasterDetail from '../../ui/MasterDetail.vue'
import Panel from '../../ui/Panel.vue'
import ErrorNote from '../../ui/ErrorNote.vue'
import ObjectList from '../../ui/ObjectList.vue'
import ObjectRow from '../../ui/ObjectRow.vue'
import LoadMore from '../../ui/LoadMore.vue'
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

const leave = async () => !fileDirty.value || confirm({ title: '放弃没保存的修改？', confirmLabel: '放弃', danger: true })
async function openDirectory(path) {
  if (!await leave()) return
  directory.value = path
  file.value = null
  creating.value = false
  fileDirty.value = false
  listing.reload()
}
async function openFile(path) {
  if (path === file.value || !await leave()) return
  file.value = path
  creating.value = false
}
async function changeScope(value) {
  if (value === scope.value || !await leave()) return
  scope.value = value
  directory.value = ''
  file.value = null
  creating.value = false
  fileDirty.value = false
  listing.reload()
}
async function create() {
  if (!await leave()) return
  file.value = null
  creating.value = true
}
function changed(path) {
  file.value = path
  creating.value = false
  listing.reload()
}
async function closeFile() {
  if (!await leave()) return
  file.value = null
  creating.value = false
  fileDirty.value = false
}
</script>

<template>
  <MasterDetail :selected="Boolean(file || creating)" default-detail @back="closeFile">
    <template #list>
      <Panel flush>
        <template #title>
          <v-btn-toggle v-if="state.public_readable" :model-value="scope" mandatory @update:model-value="changeScope">
            <v-btn value="scene">本群</v-btn><v-btn value="public">公共</v-btn></v-btn-toggle>
          <h2 v-else>本群记忆</h2>
        </template>
        <template v-if="writable" #actions><v-btn size="small" variant="outlined" @click="create">新建</v-btn></template>
        <div class="tree">
          <v-breadcrumbs :items="crumbs" density="compact" class="crumbs">
            <template #item="{ item }"><a href="#" @click.prevent="openDirectory(item.path)">{{ item.title }}</a></template>
          </v-breadcrumbs>
          <ErrorNote v-if="listing.error.value" title="读取目录失败" :error="listing.error.value" @retry="listing.reload()" />
          <p v-if="!can('browse')" class="muted small">当前的记忆方式不支持按目录浏览，可以用搜索找到记忆。</p>
          <p v-else-if="!nodes.length && !listing.loading.value && !listing.error.value" class="muted small">这里还是空的</p>
          <ObjectList>
            <ObjectRow v-for="node in nodes" :key="node.path" :title="node.name" clickable :active="node.path === file"
              @click="node.is_dir ? openDirectory(node.path) : openFile(node.path)">
              <template #prepend><v-icon :icon="node.is_dir ? mdiFolderOutline : mdiFileDocumentOutline" size="18" color="secondary" /></template>
            </ObjectRow>
          </ObjectList>
          <LoadMore v-if="hasMore" :loading="listing.loading.value" @more="listing.reload(true)" />
        </div>
      </Panel>
    </template>
    <FilePanel v-if="file || creating" :key="`${scope}:${file}:${creating}`" :scene="scene" :scope="scope" :path="file"
      :directory="directory" :state="state" @dirty="value => fileDirty = value" @changed="changed" />
    <DirectorySummary v-else :key="`${scope}:${directory}`" :scene="scene" :scope="scope" :path="directory" :state="state" />
  </MasterDetail>
</template>

<style scoped>
.tree{display:grid;gap:var(--sp-1);padding:0 var(--sp-2) var(--sp-2)}
.tree p{margin:0;padding:0 var(--sp-2)}
.crumbs{padding:0 var(--sp-2);flex-wrap:wrap}
</style>
