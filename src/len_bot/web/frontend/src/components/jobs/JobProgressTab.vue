<script setup>
import { fmtTime } from '../../api.js'
import { rangeUnit, rangesLabel, spanLabel } from './jobLabels.js'
import EntityLink from '../EntityLink.vue'
import FileAssetsPanel from '../FileAssetsPanel.vue'
import ObservationDetails from '../ObservationDetails.vue'
import PluginWorkDetails from '../PluginWorkDetails.vue'
import ResourceViewer from '../ResourceViewer.vue'
import StatusBadge from '../StatusBadge.vue'
const props = defineProps({state: {type: Object, required: true}, page: {type: Object, required: true}})
const { job, route, scalar, openResource, clearFileFocus } = props.page
const { resource, resourceText, resourceLoading, resourceError, workspaceArtifacts,
  workspaceArtifactsLoading, workspaceArtifactsError, workspaceFile, workspaceFileLoading,
  workspaceFileError, workspaceFileTarget, artifactDownloadUrl, loadWorkspaceArtifacts,
  loadWorkspaceArtifact, loadResource } = props.state
const hasPresentedBody = observation => Object.values(observation.provided_ranges).some(read => read.ranges.some(([start,end]) => end > start))
</script>
<template>
  <v-card-text class="detail-body">
    <FileAssetsPanel
      :files="job.file_assets || []"
      :scene-id="job.scene_id"
      :job-id="job.id"
      :focus-id="scalar(route.query.file)"
      @clear-focus="clearFileFocus"
    />
    <PluginWorkDetails :job="job" />
    <template v-if="job.work_state">
      <div class="status-line">
        <h3>已保存进度 · 目标版本 {{ job.work_state.goal_revision }}</h3>
        <v-chip
          v-if="job.work_state.goal_revision !== job.revision"
          color="warning"
          variant="tonal"
        >旧目标进度，需重新核对</v-chip>
      </div>
      <h4>计划</h4>
      <ol><li v-for="(item, index) in job.work_state.plan" :key="index">{{ item }}</li></ol>
      <h4>已完成步骤与依据</h4>
      <ul>
        <li v-for="(step, index) in job.work_state.completed_steps" :key="index">
          {{ step.step }}<div class="action-row">
            <v-btn
              v-for="id in step.result_ids"
              :key="id"
              size="small"
              variant="text"
              @click="openResource(id)"
            >原始资料 · {{ id.slice(0, 10) }}
            </v-btn>
          </div>
          <div
            v-for="(span,spanIndex) in step.evidence_spans || []"
            :key="spanIndex"
            class="evidence-range"
          >
            <EntityLink
              type="result"
              :id="span.result_id"
              :scene-id="job.scene_id"
              :span="span"
              label="回读进度引用范围"
            />
            <span>{{ spanLabel(span) }}（起含止不含）</span>
          </div>
        </li>
      </ul>
      <h4>待解决</h4>
      <ul>
        <li v-for="(item, index) in job.work_state.unresolved" :key="index">{{ item }}</li>
      </ul>
      <p>下一步：{{ job.work_state.next_step || '尚未提供' }}</p>
      <template v-if="job.work_state.evidence_spans?.length">
        <h4>进度依据的具体范围</h4>
        <div
          v-for="(span,index) in job.work_state.evidence_spans"
          :key="index"
          class="evidence-range"
        >
          <EntityLink
            type="result"
            :id="span.result_id"
            :scene-id="job.scene_id"
            :span="span"
            label="回读进度引用范围"
          />
          <span>{{ spanLabel(span) }}</span>
        </div>
      </template>
    </template>
    <p v-else-if="!job.work_progress" class="muted-copy">尚无已保存的进度。</p>
    <h3>实际呈现范围</h3>
    <p class="muted-copy">这里只展示已写入工作的呈现记录。取得资料、引用资料或打开面板都不等于模型看过正文或图像。</p>
    <ResourceViewer title="正文范围与图像呈现事实" :content="job.observation_reads" />
    <h3>已取得的原始工具资料</h3>
    <div class="action-row">
      <v-btn
        v-for="id in job.result_ids"
        :key="id"
        variant="outlined"
        :aria-label="'读取原始资料 ' + id"
        @click="openResource(id)"
      >资料 · {{ id.slice(0, 10) }}
      </v-btn>
      <p v-if="!job.result_ids.length" class="muted-copy">尚未取得资料。</p>
    </div>
    <h4>此工作实际提供给模型的范围</h4>
    <p class="muted-copy">范围保留实际坐标单位，可以包含工作既有版本的阅读；正文保存本身不表示模型已读。</p>
    <article
      v-for="(units,id) in job.observation_reads || {}"
      :key="id"
      class="adopted-range"
    >
      <v-btn variant="text" size="small" @click="openResource(id)">资料 {{ id.slice(0,10) }}
      </v-btn>
      <div v-for="(read,unit) in units" :key="unit">
        <p>
          {{ rangesLabel(read.ranges) }} / {{ read.total }}
          {{ rangeUnit(unit) }}（起含止不含）</p>
        <div class="action-row">
          <EntityLink
            v-for="(range,index) in read.ranges"
            :key="index"
            type="result"
            :id="id"
            :scene-id="job.scene_id"
            :span="{start:range[0],end:range[1],coordinate_unit:unit}"
            :label="`回读 [${range[0]}, ${range[1]}) ${rangeUnit(unit)}`"
          />
        </div>
      </div>
    </article>
    <p v-if="!Object.keys(job.observation_reads || {}).length" class="muted-copy">尚未保存实际采用范围，不能从资料数量推定完整读取。</p>
    <ResourceViewer
      v-if="route.query.resource"
      title="原始工具资料"
      :content="resourceText"
      :loading="resourceLoading"
      :error="resourceError"
    />
    <div v-if="resource" class="resource-meta">
      <ObservationDetails :observation="resource" :scene-id="job.scene_id" />
      <v-btn
        v-if="resource.next_offset !== null && resource.next_offset !== undefined"
        :loading="resourceLoading"
        :disabled="resourceLoading"
        variant="outlined"
        class="mt-4"
        @click="loadResource(scalar(route.query.resource), resource.next_offset)"
      >继续读取已保存正文</v-btn>
    </div>
    <section v-if="job.platform_actions?.length" class="workspace-artifacts">
      <h3>平台动作回执</h3>
      <p class="muted-copy">账号写入与 QQ 消息交付分别记录。未知结果保留额度并阻止同资源再次写入；状态核对只证明当时状态，不证明未知请求的执行过程。</p>
      <div
        v-for="action in job.platform_actions"
        :key="action.action_id"
        class="workspace-artifact"
      >
        <div>
          <strong>
            {{ {bilibili_like:'点赞',bilibili_favorite:'收藏'}[action.action_type] || action.action_type }} · av{{ action.resource_id }} · 目标 {{ action.desired_state === true ? '设置' : action.desired_state === false ? '取消' : '未记录' }}
          </strong>
          <span>账号 {{ action.account_uid }} · {{ action.collection_id ? `收藏夹 ${action.collection_id} · ` : '' }}修订 {{ action.job_revision }}
          </span>
          <span>
            <StatusBadge domain="platform_action" :status="action.status" /> · {{ action.reason }}
          </span>
          <span>
            {{ action.attempted_at != null ? `尝试于 ${fmtTime(action.attempted_at)}` : '没有登记写入尝试' }} · {{ action.action_id }}
          </span>
          <span>此动作记录更新于 {{ fmtTime(action.updated_at) }}；不是当前平台状态实时查询。</span>
          <p v-if="action.receipt_note">{{ action.receipt_note }}</p>
        </div>
      </div>
    </section>
    <section
      v-if="workspaceArtifacts || workspaceArtifactsLoading || workspaceArtifactsError"
      class="workspace-artifacts"
    >
      <div class="action-row">
        <h3>Python 工作空间产物</h3>
        <v-btn
          variant="text"
          size="small"
          :loading="workspaceArtifactsLoading"
          @click="loadWorkspaceArtifacts"
        >刷新目录</v-btn>
      </div>
      <v-progress-linear v-if="workspaceArtifactsLoading" indeterminate />
      <v-alert v-if="workspaceArtifactsError" type="error" variant="tonal">
        {{ workspaceArtifactsError }}
      </v-alert>
      <template v-if="workspaceArtifacts">
        <p v-if="workspaceArtifacts.execution_id" class="muted-copy">这些文件属于最近一次 Python 执行的已确认快照（{{ workspaceArtifacts.execution_id }}，工作 v{{ workspaceArtifacts.job_revision ?? '未记录' }}），不是浏览器或媒体执行目录。历史读取继续绑定此执行身份；读取不会执行或发送文件。</p>
        <p v-else class="muted-copy">这是宿主 worker 当前目录，不是按执行保存的不可变快照。后续运行可能改变文件；文本逐页显示、不跨页拼接，不接受 Gateway 历史执行 ID。</p>
        <p v-if="workspaceArtifacts.sampled_at" class="muted-copy">目录读取于 {{ fmtTime(workspaceArtifacts.sampled_at) }}
        </p>
        <v-alert
          v-if="workspaceArtifacts.truncated"
          type="warning"
          variant="tonal"
          density="compact"
        >清单按产物数量上限标记为截断（{{ workspaceArtifacts.truncated_reason === 'artifact_cap' ? '达到产物上限' : '原因未标注' }}），不能据此确认完整目录。</v-alert>
        <div
          v-for="artifact in workspaceArtifacts.artifacts || []"
          :key="artifact.path"
          class="workspace-artifact"
        >
          <div>
            <strong>{{ artifact.path }}</strong>
            <span class="muted-copy">
              {{ artifact.size_bytes }} 字节 · {{ artifact.media_type }}
            </span>
            <v-alert
              v-if="artifact.over_limit"
              type="warning"
              variant="tonal"
              density="compact"
            >此文件超过当前产物上限，已保留记录但不能读取或登记媒体。</v-alert>
          </div>
          <div class="action-row">
            <v-btn
              size="small"
              variant="outlined"
              :disabled="artifact.over_limit"
              :loading="workspaceFileLoading && workspaceFileTarget?.path === artifact.path"
              @click="loadWorkspaceArtifact(artifact.path)"
            >读取文本</v-btn>
            <a
              v-if="!artifact.over_limit"
              class="v-btn v-btn--size-small v-btn--variant-text"
              :href="artifactDownloadUrl(artifact)"
              target="_blank"
              rel="noopener"
            >下载原文件</a>
          </div>
        </div>
        <p v-if="!workspaceArtifacts.artifacts?.length" class="muted-copy">此次已读取的目录没有列出普通文件产物。</p>
      </template>
      <p v-if="workspaceFile && !workspaceFileTarget?.executionId" class="muted-copy">当前页起始字符 {{ workspaceFile.offset }} · 本页 {{ workspaceFile.content.length }} 字符；未与前页合并，目录可能在两次读取间变化。</p>
      <ResourceViewer
        v-if="workspaceFileTarget"
        :title="'工作产物 · ' + workspaceFileTarget.path"
        :content="workspaceFile?.content"
        :loading="workspaceFileLoading"
        :error="workspaceFileError"
      />
      <v-btn
        v-if="workspaceFile?.next_offset !== null && workspaceFile?.next_offset !== undefined"
        variant="outlined"
        size="small"
        :loading="workspaceFileLoading"
        :disabled="workspaceFileLoading"
        @click="loadWorkspaceArtifact(workspaceFileTarget.path, workspaceFile.next_offset)"
      >
        {{ workspaceFileTarget.executionId ? '继续读取同一快照' : '读取当前文件下一页' }}
      </v-btn>
    </section>
    <h3>固定的方法版本与正文提供记录</h3>
    <p class="muted-copy">固定版本、取得正文、实际提供是三个不同事实；均不证明已正确使用或学会。范围可来自本工作既有目标版本。</p>
    <article
      v-for="method in job.method_reads || []"
      :key="method.skill_id"
      class="adopted-range"
    >
      <EntityLink
        type="skill"
        :id="method.skill_id"
        :scene-id="job.scene_id"
        :version="method.version"
        :label="method.skill_id + ' · v' + method.version"
      />
      <p v-if="!method.observations.length" class="muted-copy">版本已固定；本工作未关联到该版本的成功正文记录，不能推定已读。</p>
      <div v-for="observation in method.observations" :key="observation.result_id">
        <EntityLink
          type="result"
          :id="observation.result_id"
          :scene-id="job.scene_id"
          label="查看当时取得的方法正文"
        />
        <p v-if="!hasPresentedBody(observation)" class="muted-copy">正文已保存，尚无非空的实际提供范围。</p>
        <div v-for="(read,unit) in observation.provided_ranges" :key="unit">
          <p>已提供 {{ rangesLabel(read.ranges) }} / {{ read.total }}
            {{ rangeUnit(unit) }}（起含止不含）</p>
          <div class="action-row">
            <EntityLink
              v-for="(range,index) in read.ranges"
              :key="index"
              type="result"
              :id="observation.result_id"
              :scene-id="job.scene_id"
              :span="{ start:range[0], end:range[1], coordinate_unit:unit }"
              :label="`回读 [${range[0]}, ${range[1]}) ${rangeUnit(unit)}`"
            />
          </div>
        </div>
      </div>
    </article>
    <p v-if="!Object.keys(job.skill_versions).length" class="muted-copy">本工作没有固定的方法版本。</p>
  </v-card-text>
</template>
<style scoped>
.workspace-artifacts{display:grid;gap:12px;margin-top:28px}
.workspace-artifact{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:12px 0;border-bottom:1px solid var(--line)}
.workspace-artifact>div{display:grid;gap:4px;min-width:0}
.workspace-artifact strong{overflow-wrap:anywhere}
.delivery-record,.adopted-range{display:grid;gap:10px;min-width:0;padding:14px 0;border-bottom:1px solid var(--line)}
.evidence-range{display:flex;align-items:center;flex-wrap:wrap;gap:8px;font-size:13px}
.adopted-range .v-btn{justify-self:start}
.adopted-range p{margin:0;font-size:13px;overflow-wrap:anywhere}
.field-label,.work-usage,.muted-copy,.read-time{font-size:13px;color:rgb(var(--v-theme-on-surface-variant))}
.identity-line,.detail-status,.action-row,.status-line{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.action-row{margin-top:16px}
.detail-body{min-width:0;line-height:1.65}
.detail-body h3{font-size:17px;margin:24px 0 12px}
.detail-body h3:first-child{margin-top:0}
.detail-body h4{font-size:15px;margin:18px 0 8px}
.detail-body ul,.detail-body ol{padding-left:24px;margin:8px 0}
.detail-body li{margin:8px 0;overflow-wrap:anywhere}
.detail-body p{margin:10px 0}
.resource-meta{font-size:13px}
@media(max-width:650px){
  .action-row>.v-btn{flex-grow:1}
}
</style>
