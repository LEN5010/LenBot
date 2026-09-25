<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api, fmtTime } from '../api.js'
import EntityLink from './EntityLink.vue'
import StatusBadge from './StatusBadge.vue'

const props = defineProps({
  run: { type: Object, required: true },
  jobId: { type: String, required: true },
  sceneId: { type: String, required: true }
})
const expanded = ref(false), data = ref(null), loading = ref(false), error = ref('')
const key = computed(() => JSON.stringify([props.sceneId, props.jobId, props.run.execution_id]))
const stopLabels = {
  confirmed_absent: '已确认容器不存在',
  confirmed_stopped: '已确认停止',
  unconfirmed: '停止尚未确认'
}
const sourceLabels = {
  anonymous_public: '匿名公开来源',
  account: '账号态来源',
  scene: '场景来源',
  derived: '派生资料',
  unknown: '来源属性未确认'
}
let generation = 0
async function load() {
  const request = ++generation, target = key.value
  loading.value = true;
  error.value = ''
  try {
    const result = await api(`/api/cockpit/jobs/${encodeURIComponent(props.jobId)}/executions/${encodeURIComponent(props.run.execution_id)}?` + new URLSearchParams({ scene_id: props.sceneId }))
    if (request !== generation || target !== key.value) return
    data.value = result
  } catch (failure) {
    if (request === generation && target === key.value) error.value = failure.message
  }
  finally {
    if (request === generation) loading.value = false
  }
}
function toggle(event) {
  expanded.value = event.target.open
  if (expanded.value && (!data.value || error.value || data.value.last_sequence !== props.run.last_sequence)) load()
}
watch(key, () => {
  ++generation;
  expanded.value = false;
  data.value = null;
  error.value = '';
  loading.value = false
})
watch(() => props.run.last_sequence, () => {
  if (expanded.value) load()
})
onBeforeUnmount(() => {
  ++generation
})
</script>

<template>
  <details class="execution-details" :open="expanded" @toggle="toggle">
    <summary>
      <strong>{{ run.worker_type }} · 工作 v{{ run.job_revision }}</strong>
      <StatusBadge domain="external_execution" :status="run.state" />
      <span>{{ run.occupies_capacity ? '仍占用资源' : '不占用资源' }}</span>
      <code>{{ run.execution_id }}</code>
    </summary>
    <div class="execution-body">
      <div class="execution-toolbar">
        <p class="muted-copy">只读宿主已保存的执行请求和事件，不查询网关、不启动或重放执行。</p>
        <v-btn variant="text" size="small" :loading="loading" @click="load">刷新本地记录</v-btn>
      </div>
      <v-progress-linear v-if="loading" indeterminate aria-label="正在读取原执行记录" />
      <v-alert v-if="error" type="error" variant="tonal">
        {{ error }}<span v-if="data"> · 下方保留上次读取内容</span>
      </v-alert>
      <template v-if="data">
        <p class="muted-copy">读取于 {{ fmtTime(data.sampled_at) }} · 本地事件截至序号 {{ data.last_sequence }}，不是网关实时探测。</p>
        <div class="execution-facts">
          <StatusBadge domain="external_execution" :status="data.state" />
          <span>退出码 {{ data.returncode ?? '未记录' }}</span>
          <span>{{ data.occupies_capacity ? '仍占用资源' : '不占用资源' }}</span>
        </div>
        <p>接受 {{ fmtTime(data.accepted_at) }} · 开始 {{ fmtTime(data.started_at) }} · 结束 {{ fmtTime(data.ended_at) }} · 期限 {{ fmtTime(data.deadline_at) }}
        </p>
        <p>镜像配置引用 {{ data.image_ref }} · 出网策略 {{ data.network_policy }}</p>
        <v-alert v-if="data.error" type="error" variant="tonal" class="error-text">
          {{ data.error }}
        </v-alert>
        <section v-if="data.termination">
          <h4>{{ stopLabels[data.termination.status] || data.termination.status }}</h4>
          <p class="error-text">{{ data.termination.detail || '未附加停止说明' }}</p>
          <p v-if="data.termination.container_name" class="muted-copy">原容器 {{ data.termination.container_name }}
          </p>
        </section>
        <p v-else class="muted-copy">未记录停止回执；正常进程退出与发送“停止”请求是不同事实，均不证明业务结果正确或群已收到。</p>
        <section class="input-section">
          <h4>宿主提交的输入清单</h4>
          <p class="muted-copy">清单只说明宿主导出了哪些资料，不证明容器已收到或程序实际读过；不返回脚本、输入正文或媒体字节。</p>
          <v-alert v-if="data.input_manifest_error" type="warning" variant="tonal">
            {{ data.input_manifest_error }}
          </v-alert>
          <p v-else-if="data.input_manifest_state==='not_applicable'" class="muted-copy">
            {{ data.worker_type }} 执行不使用 Python 工作区输入清单；覆盖沿其原工具资料核对。</p>
          <p v-else-if="!data.input_manifest" class="muted-copy">此执行未保存可投影的输入清单，不能据此称没有输入。</p>
          <template v-if="data.input_manifest">
            <p>清单工作版本 {{ data.input_manifest.job_revision == null ? '旧记录未保存' : `v${data.input_manifest.job_revision}` }} · {{ data.input_manifest.inputs.length }} 项 · {{ data.input_manifest.input_directory }}
            </p>
            <p v-if="!data.input_manifest.inputs.length" class="muted-copy">该次清单明确没有导入文本或媒体文件。</p>
            <article
              v-for="item in data.input_manifest.inputs"
              :key="item.name"
              class="input-entry"
            >
              <strong>
                {{ item.name }} · {{ item.kind==='text' ? '保存的工具正文' : '媒体文件' }} · {{ item.bytes.toLocaleString() }} 字节</strong>
              <code>{{ item.container_path }}</code>
              <p>覆盖说明：{{ item.coverage }}</p>
              <template v-if="item.kind==='text'">
                <StatusBadge domain="observation" :status="item.status" />
                <EntityLink
                  type="result"
                  :id="item.result_id"
                  :scene-id="sceneId"
                  label="回读原工具资料及来源"
                />
                <p>
                  {{ item.source_truncated == null ? '旧清单未记录源端截断' : item.source_truncated ? '源端有未取得内容，导出的正文不是源全文' : '该次清单未标记源端截断，不等于程序已读或资料必然正确' }}
                </p>
                <p class="muted-copy">
                  {{ item.provenance ? (sourceLabels[item.provenance.access] || item.provenance.access) : '旧清单未保存来源属性' }}
                </p>
              </template>
              <template v-else>
                <p>{{ item.scope }} · {{ item.mime_type || '类型未记录' }}</p>
                <EntityLink type="media" :id="item.asset_id" :scene-id="sceneId" label="查看原媒体登记" />
                <EntityLink
                  v-if="item.source_event_id"
                  type="event"
                  :id="item.source_event_id"
                  :scene-id="sceneId"
                  label="原媒体来源事件"
                />
              </template>
            </article>
          </template>
        </section>
        <section>
          <h4>原执行事件</h4>
          <ol v-if="data.events.length" class="execution-events">
            <li v-for="event in data.events" :key="event.sequence">
              <strong>#{{ event.sequence }} · {{ event.kind }}</strong>
              <time>{{ fmtTime(event.at) }}</time>
              <p class="error-text">{{ event.detail || '未附加说明' }}</p>
            </li>
          </ol>
          <p v-else class="muted-copy">该读取截点没有已保存的执行事件。</p>
        </section>
      </template>
    </div>
  </details>
</template>

<style scoped>
.execution-details{border:1px solid var(--line);border-radius:8px;min-width:0;margin:8px 0}
.execution-details summary{display:flex;align-items:center;flex-wrap:wrap;gap:8px 12px;padding:14px;cursor:pointer;font-size:13px}
.execution-details summary::before{content:'▸';color:var(--muted)}
.execution-details[open] summary::before{content:'▾'}
.execution-details summary:focus-visible{outline:3px solid #2563eb;outline-offset:-3px}
.execution-details code{overflow-wrap:anywhere;min-width:0}
.execution-details summary code{flex-basis:100%;font-size:12px;color:var(--muted)}
.execution-body,.input-section{display:grid;gap:12px;min-width:0}
.execution-body{padding:0 16px 16px;font-size:13px;line-height:1.7}
.execution-body p{margin:0;overflow-wrap:anywhere}
.execution-toolbar,.execution-facts{display:flex;gap:8px 14px;align-items:center;flex-wrap:wrap}
.execution-toolbar p{flex:1;min-width:180px}
.muted-copy{color:var(--muted)}
.error-text{white-space:pre-wrap;overflow-wrap:anywhere}
.input-entry{display:grid;gap:6px;padding:12px;border:1px solid var(--line);border-radius:6px;min-width:0}
.input-entry strong{overflow-wrap:anywhere}
.input-entry>.v-chip{justify-self:start}
.execution-events{padding-left:24px}
.execution-events li{margin:12px 0}
.execution-events time{margin-left:12px;color:var(--muted)}
@media(max-width:550px){
  .execution-body{padding-inline:12px}
  .execution-events time{display:block;margin-left:0}
}
</style>
