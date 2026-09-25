<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, fmtTime } from '../api.js'
import PageHeader from '../components/PageHeader.vue'
import ScopeSelect from '../components/ScopeSelect.vue'
import EntityLink from '../components/EntityLink.vue'
import StatusBadge from '../components/StatusBadge.vue'
import ResourceViewer from '../components/ResourceViewer.vue'
import { useRequestGuard } from '../composables/useRequestGuard.js'
import { useGuardedRead } from '../composables/useGuardedRead.js'

const route = useRoute(), router = useRouter()
const scalar = value => typeof value === 'string' ? value : ''
const listScene = computed(() => scalar(route.query.list_scene ?? route.query.scene))
const detailKey = computed(() => JSON.stringify([route.query.id, route.query.scene, route.query.version]))
const rows = ref([]),
  total = ref(0),
  loaded = ref(false),
  loading = ref(false),
  error = ref(''),
  readAt = ref(null)
const scene = ref(route.query.scene || ''), query = ref(route.query.q || '')
const selected = ref(null),
  detailLoading = ref(false),
  detailError = ref(''),
  publishing = ref(false),
  publishTarget = ref(null)
const publishError = ref(''), publishReceipt = ref(null), readbackPending = ref(false)
const detailReadAt = ref(null), selectedKey = ref(null)
const tab = computed(() => route.query.tab === 'candidates' ? 'candidates' : 'saved')
const page = computed(() => Math.max(1, Number(route.query.page) || 1))
const listGuard = useRequestGuard(()=>JSON.stringify([route.name,listScene.value,tab.value,route.query.q,page.value]))
const detailSelection = ()=>JSON.stringify([route.name,detailKey.value])
const detailGuard = useRequestGuard(detailSelection),
  publishGuard = useRequestGuard(detailSelection)
const canPublish = computed(()=>!!selected.value && selected.value.scope!=='global-safe' && selected.value.version===selected.value.versions[0]?.version
  && !publishing.value && !detailLoading.value && !detailError.value && !publishReceipt.value)
const sameVersion = (skill, target)=>skill?.id===target.id && skill.scene_id===target.scene_id && skill.version===target.version
const sourceRanges = computed(() => Object.entries(selected.value?.source.work_observation_reads || {}).flatMap(([id, units]) =>
  Object.entries(units).flatMap(([unit, read]) => read.ranges.map(([start,end]) => ({ id, unit, start, end, total: read.total })))))
const unitName = value => ({ characters: '字符', records: '记录' }[value] || value)
const readList = useGuardedRead(listGuard, loading, error)
function load() {
  return readList(() => {
    const params = new URLSearchParams({ page: String(page.value), page_size: '30' })
    if (listScene.value) params.set('scene_id', listScene.value)
    if (tab.value === 'saved' && route.query.q) params.set('query', route.query.q)
    return api(`/api/cockpit/${tab.value === 'saved' ? 'skills' : 'skill-candidates'}?${params}`)
  }, result => {
    rows.value = result.items;
    total.value = result.total;
    loaded.value = true;
    readAt.value = Date.now()/1000
  })
}
async function loadDetail() {
  const fresh = detailGuard(),
    key = detailKey.value,
    id=scalar(route.query.id),
    scene=scalar(route.query.scene)
  detailError.value = '';
  publishTarget.value = null
  if (selectedKey.value !== key) {
    selected.value = null;
    detailReadAt.value = null;
    selectedKey.value = null
  }
  if (!id) {
    detailLoading.value = false;
    return
  }
  detailLoading.value = true
  const params = new URLSearchParams({ scene_id: scene })
  const version=publishReceipt.value?.version ?? scalar(route.query.version)
  if (version) params.set('version', String(version))
  try {
    const result = await api(`/api/cockpit/skills/${encodeURIComponent(id)}?${params}`)
    if (!fresh()) return
    if(!result || result.id!==id || version && result.version!==Number(version)
      || result.scene_id!==scene && result.scope!=='global-safe')throw new Error('读取结果不属于所选方法版本或范围，未采用。')
    if(publishReceipt.value && !sameVersion(result,publishReceipt.value))throw new Error('读回结果与原公开对象不符，未用其他版本代替。')
    if(readbackPending.value){
      if(result.scope!=='global-safe')throw new Error('本次读回未显示原版本已公开；原公开确认保留，暂不重复提交。')
      readbackPending.value=false
    }
    selected.value = result;
    selectedKey.value = key;
    detailReadAt.value = Date.now()/1000
  } catch (e) {
    if (fresh()) detailError.value = e.message
  }
  finally {
    if (fresh()) detailLoading.value = false
  }
}
function open(skill) {
  router.push({
    name: 'skills',
    query: {
      ...route.query,
      list_scene: listScene.value,
      id: skill.id,
      scene: skill.scene_id,
      version: String(skill.version)
    }
  })
}
function close() {
  const query = { ...route.query, scene: listScene.value || undefined }
  delete query.id;
  delete query.version;
  delete query.list_scene
  router.push({ name: 'skills', query })
}
function filter() {
  router.push({
    name: 'skills',
    query: {
      return_to: route.query.return_to,
      tab: tab.value,
      scene: scene.value || undefined,
      q: query.value || undefined,
      page: 1
    }
  })
}
function changeTab(value) {
  router.push({
    name: 'skills',
    query: {
      return_to: route.query.return_to,
      scene: listScene.value || undefined,
      tab: value,
      page: 1
    }
  })
}
function refresh() {
  load();
  if (route.query.id && !publishing.value) loadDetail()
}
function confirmPublish() {
  if(!canPublish.value)return
  const {id,name,scene_id,version}=selected.value
  publishTarget.value={id,name,scene_id,version};
  publishError.value=''
}
async function publish() {
  if(!publishTarget.value||publishing.value)return
  const target={...publishTarget.value}
  if(!canPublish.value || !sameVersion(selected.value,target)){
    publishError.value='当前方法或版本已经变化，请重新读取并确认原对象。';
    publishTarget.value=null;
    return
  }
  const fresh=publishGuard(),key=detailKey.value
  detailGuard();
  detailLoading.value=false
  publishing.value = true;
  publishError.value = ''
  const { id, scene_id, version } = target
  try {
    const result=await api(`/api/cockpit/skills/${encodeURIComponent(id)}/publish`, {
      method: 'POST',
      body: JSON.stringify({ scene_id, expected_version: version })
    })
    if (!fresh()) return
    if(result?.success!==true || !sameVersion(result.skill,target) || result.skill.scope!=='global-safe')throw new Error('接口没有返回同一版本的公开确认。')
    publishReceipt.value={...target,status:'confirmed'};
    publishTarget.value=null
    selected.value=result.skill;
    selectedKey.value=key;
    detailReadAt.value=Date.now()/1000
    await load()
  } catch (e) {
    if(!fresh())return
    publishTarget.value=null
    const detail=e.details,
      matched=detail?.skill_id===id&&detail.scene_id===scene_id&&detail.version===version
    if(e.status===409&&matched&&detail.skill_published===true&&detail.phase==='readback') {
      publishReceipt.value={...target,status:'confirmed'};
      readbackPending.value=true;
      publishError.value=e.message
    } else if(e.status===409&&matched&&detail.skill_published===false
      || e.details!=null&&detail?.skill_published!==true&&[400,401,403,404,422].includes(e.status))publishError.value=e.message
    else {
      publishReceipt.value={...target,status:'unknown'}
      publishError.value='未取得属于本次方法版本的有效公开确认，结果未知。接口信息：'+e.message
    }
  }
  finally {
    if (fresh()) publishing.value = false
  }
}
watch(() => [listScene.value, route.query.q, route.query.page, route.query.tab], () => {
  rows.value = [];
  total.value = 0;
  loaded.value = false;
  readAt.value = null
  scene.value = listScene.value;
  query.value = scalar(route.query.q);
  load()
}, { immediate: true, flush:'sync' })
watch(detailKey, () => {
  publishGuard();
  detailGuard();
  publishing.value=false;
  publishTarget.value=null;
  publishError.value='';
  publishReceipt.value=null;
  readbackPending.value=false;
  loadDetail()
}, { immediate: true, flush:'sync' })
</script>
<template>
  <div class="page-stack">
    <PageHeader title="方法技能" description="有来源、有版本的工作方法。读取页面不会生成或执行技能。">
      <v-btn variant="outlined" :loading="loading" @click="refresh">刷新</v-btn>
    </PageHeader>
    <v-alert v-if="error" type="error" variant="tonal">
      {{ error }}<span v-if="readAt"> · 上次读取 {{ fmtTime(readAt) }}</span>
    </v-alert>
    <v-tabs :model-value="tab" color="primary" @update:model-value="changeTab">
      <v-tab value="saved">已保存技能</v-tab>
      <v-tab value="candidates">经验候选</v-tab>
    </v-tabs>
    <v-card class="pa-4">
      <v-form class="filters" @submit.prevent="filter">
        <ScopeSelect v-model="scene" :include-global="true" clearable />
        <v-text-field v-if="tab==='saved'" v-model="query" label="名称或适用条件" hide-details clearable />
        <v-btn type="submit" color="primary">查询</v-btn>
      </v-form>
    </v-card>
    <p class="muted">
      {{ tab === 'saved' ? '自动技能限来源场景使用，只有运营明确公开的版本可跨场景读取。人工内容不会被自动整理覆盖；已保存不等于已在后续工作中正确采用。' : '候选待整理不表示已经学会。重复、没有新增方法价值或来源不足可正常跳过，并保留原因；失败、中断和过期分别记录。' }}
    </p>
    <v-progress-linear v-if="loading" indeterminate />
    <p v-if="loaded" class="muted">共 {{ total }}
      {{ tab === 'saved' ? '项技能' : '条候选' }} · 读取于 {{ fmtTime(readAt) }}
    </p>
    <div v-if="tab==='saved'" class="skill-list">
      <v-card v-for="skill in rows" :key="skill.id" class="pa-5 skill-row">
        <div class="skill-main">
          <h2>{{ skill.name }}</h2>
          <p class="clamp-2">{{ skill.applicability }}</p>
          <div class="meta">
            <span>{{ skill.author==='human' ? '人工维护' : '工作经验' }}</span>
            <span>{{ skill.scope==='global-safe' ? '此版本已公开' : '仅来源场景' }}</span>
            <EntityLink type="scene" :id="skill.scene_id" />
            <span>v{{ skill.version }}</span>
          </div>
        </div>
        <v-btn variant="tonal" color="primary" @click="open(skill)">查看正文</v-btn>
      </v-card>
    </div>
    <div v-else class="skill-list">
      <v-card v-for="candidate in rows" :key="candidate.id" class="pa-5">
        <div class="candidate-heading">
          <h2>{{ candidate.candidate.name }}</h2>
          <StatusBadge domain="skill_candidate" :status="candidate.status" />
        </div>
        <p class="clamp-2 my-3">{{ candidate.candidate.lesson }}</p>
        <div class="meta">
          <EntityLink type="job" :id="candidate.job_id" :scene-id="candidate.scene_id" />
          <span>来源工作版本 {{ candidate.job_revision }}</span>
          <EntityLink type="scene" :id="candidate.scene_id" />
        </div>
        <div class="source-row mt-3">
          <EntityLink
            v-for="id in candidate.candidate.correction_event_ids || []"
            :key="id"
            type="event"
            :id="id"
            :scene-id="candidate.scene_id"
            label="候选引用的纠正原话"
          />
          <EntityLink
            v-for="id in candidate.candidate.result_ids || []"
            :key="id"
            type="result"
            :id="id"
            :scene-id="candidate.scene_id"
          />
        </div>
        <div v-for="saved in candidate.saved_versions" :key="saved.version" class="source-row mt-3">
          <EntityLink
            type="skill"
            :id="candidate.skill_id"
            :scene-id="candidate.scene_id"
            :version="saved.version"
            :label="`读取此候选实际保存的 v${saved.version}`"
          />
          <span class="muted">
            {{ saved.scope==='global-safe' ? '此版本已公开' : '仅来源场景' }} · {{ fmtTime(saved.created_at) }}
          </span>
        </div>
        <p v-if="candidate.status==='saved' && !candidate.saved_versions.length" class="muted mt-3">候选状态为已保存，但未找到来源身份完全对应的版本；不以最新版本代替。</p>
        <p v-if="candidate.skip_reason" class="skip-reason">跳过原因：{{ candidate.skip_reason }}</p>
        <v-alert
          v-if="candidate.error"
          :type="candidate.status==='obsolete'||candidate.status==='interrupted'?'warning':'error'"
          variant="tonal"
          class="mt-3"
        >
          {{ candidate.error }}
        </v-alert>
        <details class="mt-3">
          <summary>候选全文与来源</summary>
          <ResourceViewer title="经验候选" :content="candidate.candidate" />
        </details>
      </v-card>
    </div>
    <v-card v-if="loaded && !loading && !error && !rows.length" class="pa-8 text-center muted">当前范围没有{{ tab==='saved' ? '已保存技能' : '经验候选' }}
    </v-card>
    <v-pagination
      v-if="total>30"
      :model-value="page"
      :length="Math.ceil(total/30)"
      :total-visible="5"
      @update:model-value="value=>router.push({name:'skills',query:{...route.query,page:value}})"
    />
    <v-dialog
      :model-value="!!route.query.id"
      max-width="900"
      scrollable
      :persistent="publishing"
      @update:model-value="value=>!value&&close()"
    >
      <v-card>
        <v-card-title class="dialog-title">技能详情<div>
            <v-btn
              variant="text"
              :disabled="publishing"
              :loading="detailLoading"
              @click="loadDetail"
            >刷新详情</v-btn>
            <v-btn variant="text" :disabled="publishing" @click="close">关闭</v-btn>
          </div>
        </v-card-title>
        <v-card-text>
          <v-progress-linear v-if="detailLoading" indeterminate />
          <v-alert v-if="detailError" type="error" variant="tonal">{{ detailError }}</v-alert>
          <v-alert v-if="publishError" type="error" variant="tonal" class="mb-3">
            {{ publishError }}
          </v-alert>
          <v-alert
            v-if="publishReceipt"
            :type="publishReceipt.status==='confirmed'?'info':'warning'"
            variant="tonal"
            class="mb-3"
          >
            <p>
              {{ publishReceipt.name }} · {{ publishReceipt.scene_id }} · {{ publishReceipt.id }} · v{{ publishReceipt.version }}
            </p>
            <p v-if="publishReceipt.status==='confirmed'" class="mt-2">此版本已经取得公开确认，其他版本范围不变；公开不表示已在后续工作中使用，也不会直接向群发送。</p>
            <p v-else class="mt-2">原公开请求结果未知，当前详情不再次提交。可读取原版本核对当前范围；读到已公开也不能追认为这次请求成功，读到未公开也不证明请求已停止。</p>
            <p v-if="readbackPending" class="mt-2">公开已经写入，但原版本保存值待读回。下方保留的是旧采样，不据此重复公开。</p>
            <v-btn
              class="mt-3"
              variant="outlined"
              :disabled="publishing"
              :loading="detailLoading"
              @click="loadDetail"
            >重读原版本</v-btn>
          </v-alert>
          <p v-if="detailReadAt" class="muted mb-3">以下为 {{ fmtTime(detailReadAt) }} 取得的详情；刷新失败保留原内容，不代表当前最新状态。</p>
          <template v-if="selected">
            <h2>{{ selected.name }}</h2>
            <p class="entity-id my-2">{{ selected.id }}</p>
            <div class="meta mb-5">
              <span>{{ selected.scene_id }}</span>
              <span>{{ selected.scope==='global-safe' ? '此版本已公开' : '仅来源场景' }}</span>
              <span>{{ selected.author==='human' ? '人工维护' : '工作经验' }}</span>
              <span>版本保存于 {{ fmtTime(selected.versions.find(item=>item.version===selected.version)?.created_at) }}
              </span>
            </div>
            <v-select
              :model-value="selected.version"
              :disabled="publishing || detailLoading"
              :items="selected.versions.map(item=>({title:`v${item.version} · ${item.scope==='global-safe'?'已公开':'来源场景'} · ${fmtTime(item.created_at)}`,value:item.version}))"
              label="读取指定版本"
              @update:model-value="value=>router.push({name:'skills',query:{...route.query,version:String(value)}})"
            />
            <section class="skill-body">
              <h3>适用条件</h3>
              <p>{{ selected.applicability }}</p>
              <h3>步骤</h3>
              <ol><li v-for="(step,i) in selected.steps" :key="i">{{ step }}</li></ol>
              <h3>验证要求</h3>
              <ul><li v-for="(item,i) in selected.verification" :key="i">{{ item }}</li></ul>
              <h3>不适用条件</h3>
              <ul><li v-for="(item,i) in selected.exclusions" :key="i">{{ item }}</li></ul>
            </section>
            <v-divider class="my-5" />
            <h3 class="mb-3">来源工作与纠正</h3>
            <div class="meta">
              <EntityLink
                v-if="selected.source.job_id"
                type="job"
                :id="selected.source.job_id"
                :scene-id="selected.scene_id"
              />
              <span v-if="selected.source.job_revision">来源目标版本 {{ selected.source.job_revision }}；当前工作可能已修订</span>
            </div>
            <div class="source-row my-3">
              <EntityLink
                v-for="id in selected.source.correction_event_ids || []"
                :key="id"
                type="event"
                :id="id"
                :scene-id="selected.scene_id"
                label="查看纠正原话"
              />
              <EntityLink
                v-for="id in selected.source.result_ids || []"
                :key="id"
                type="result"
                :id="id"
                :scene-id="selected.scene_id"
              />
            </div>
            <template v-if="selected.source.job_id">
              <h4 class="mt-4">保存方法时记录的工作阅读范围</h4>
              <p class="muted my-2">只表示来源工作实际提供过这些区间，不表示全部引用内容正确或后续已采用方法。</p>
              <p v-if="selected.source.work_observation_reads==null" class="muted">旧方法未保存该范围，不能从资料 ID 推定完整已读。</p>
              <p v-else-if="!sourceRanges.length" class="muted">没有工具资料范围；请核对上方纠正原话。</p>
              <div v-for="(span,index) in sourceRanges" :key="index" class="source-row my-2">
                <EntityLink
                  type="result"
                  :id="span.id"
                  :scene-id="selected.scene_id"
                  :span="{start:span.start,end:span.end,coordinate_unit:span.unit}"
                  :label="`回读 [${span.start}, ${span.end}) ${unitName(span.unit)}`"
                />
                <span class="muted">原资料共 {{ span.total }} {{ unitName(span.unit) }}</span>
              </div>
            </template>
            <details class="mt-4">
              <summary>完整来源字段</summary>
              <ResourceViewer title="完整来源" :content="selected.source" />
            </details>
            <v-btn
              v-if="selected.scope!=='global-safe'"
              class="mt-5"
              color="primary"
              variant="tonal"
              :disabled="!canPublish"
              @click="confirmPublish"
            >公开 v{{ selected.version }}
            </v-btn>
            <p
              v-if="selected.scope!=='global-safe' && selected.version !== selected.versions[0].version"
              class="muted mt-3"
            >当前为历史版本。公开接口只接受最新版本，选择最新版本后可提交。</p>
            <v-alert v-if="selected.scope==='global-safe'" class="mt-5" type="info" variant="tonal">v{{ selected.version }} 已明确公开。其他版本的可见范围分别记录。</v-alert>
          </template>
        </v-card-text>
      </v-card>
    </v-dialog>
    <v-dialog
      :model-value="!!publishTarget"
      max-width="560"
      :persistent="publishing"
      @update:model-value="value=>!value&&!publishing&&(publishTarget=null)"
    >
      <v-card v-if="publishTarget" title="公开指定技能版本">
        <v-card-text>
          <p>确认公开「{{ publishTarget.name }}」v{{ publishTarget.version }}？所有场景的工作都可以读取这一版本的正文与来源信息。</p>
          <p class="entity-id mt-3">{{ publishTarget.id }}</p>
          <p class="muted mt-3">来源范围：{{ publishTarget.scene_id }}。请核对完整正文和来源；此操作只公开这个固定版本，不跟随后来出现的新版本。离开页面不会撤销已经提交的请求。</p>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn :disabled="publishing" @click="publishTarget=null">取消</v-btn>
          <v-btn color="primary" :loading="publishing" :disabled="!canPublish" @click="publish">确认公开 v{{ publishTarget.version }}
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>
<style scoped>
.source-row{display:flex;align-items:center;flex-wrap:wrap;gap:8px 16px;min-width:0}
.source-row>span{overflow-wrap:anywhere}
summary{cursor:pointer}
.skip-reason{margin:14px 0 0;padding:12px;background:rgb(var(--v-theme-surface-variant));border-radius:8px;font-size:13px;line-height:1.7;white-space:pre-wrap;overflow-wrap:anywhere}
.filters{display:grid;grid-template-columns:minmax(180px,1fr) minmax(220px,1.5fr) auto;align-items:center;gap:12px}
.skill-list{display:grid;gap:12px}
.skill-row,.candidate-heading,.dialog-title{display:flex;align-items:flex-start;justify-content:space-between;gap:16px}
.skill-main{min-width:0;flex:1}
.skill-main h2,.candidate-heading h2{font-size:17px;line-height:1.5;overflow-wrap:anywhere}
.skill-main>p{margin:10px 0;line-height:1.65}
.meta{display:flex;flex-wrap:wrap;gap:8px 16px;color:#64748b;font-size:13px;align-items:center}
.skill-body{line-height:1.8;overflow-wrap:anywhere}
.skill-body h3{margin:22px 0 8px}
.skill-body p{white-space:pre-wrap}
.skill-body ol,.skill-body ul{padding-left:24px}
.skill-body li{margin:6px 0;white-space:pre-wrap}
.dialog-title{align-items:center}
@media(max-width:650px){
  .filters{grid-template-columns:minmax(0,1fr)}
  .skill-row,.candidate-heading{flex-direction:column}
  .skill-row>.v-btn{align-self:flex-start}
}
</style>
