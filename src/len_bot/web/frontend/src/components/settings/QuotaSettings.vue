<script setup>
import ResourceViewer from '../ResourceViewer.vue'
const props = defineProps({state: {type: Object, required: true}, page: {type: Object, required: true}})
const { busy, currentSaveOutcome, conflicts } = props.page
const { quotaText, quotaDirty, quotaRows, quotaProblems, quotaRaw, rawPolicies,
  quotaProblemsFor, focusPolicy, toggleQuotaRaw, saveQuota } = props.state
</script>
<template>
  <v-card class="pa-5 form-card">
    <h2>额度策略</h2>
    <p class="muted my-3">这里定义命名的额度策略；能力授予的“使用哪项额度策略”填写这里的名称，不在授予里复制额度数值。<strong>token 不是货币</strong>：上限按 token 计，费用另看调用账。未配置策略时各维度不设 token 上限，由期限和消息上限结束；留空表示该维度不设上限，写出的数字才是限制。引用已失效的策略名称会拒绝，不会改用默认值。并发上限在创建准入时生效。修改默认策略不会改动已在执行的工作，它们仍按创建时的快照。</p>
    <v-form
      :disabled="!!currentSaveOutcome||!!busy"
      class="form-grid"
      @submit.prevent="saveQuota"
    >
      <template v-if="!quotaRaw">
        <div
          v-for="(row,index) in quotaRows"
          :key="index"
          class="wide policy-row"
          :data-policy="`policy:${index}`"
        >
          <div class="policy-heading">
            <h3>策略 {{ index+1 }}</h3>
            <v-btn
              variant="text"
              color="error"
              size="small"
              :disabled="!!currentSaveOutcome||!!busy"
              @click="quotaRows.splice(index,1)"
            >删除这项策略</v-btn>
          </div>
          <div class="policy-fields">
            <v-text-field
              v-model="row.name"
              label="策略名称"
              hint="能力授予按这个名字引用；改名等于新建一项策略"
              persistent-hint
              required
            />
            <v-text-field
              v-model.number="row.work"
              label="单工作累计 token"
              type="number"
              min="1"
              hint="单个工作累计模型 token 上限，至少为 1；留空表示不设该维度"
              persistent-hint
            />
            <v-text-field
              v-model.number="row.user"
              label="主体日额度（token）"
              type="number"
              min="0"
              hint="同一账号在账务日内的上限，可填 0；留空表示不设该维度"
              persistent-hint
            />
            <v-text-field
              v-model.number="row.scene"
              label="群日额度（token，可选）"
              type="number"
              min="0"
              hint="同一场景在账务日内的上限，可填 0；留空表示本场景未配置该维度"
              persistent-hint
            />
          </div>
          <p
            v-for="problem in quotaProblemsFor(index)"
            :key="problem.message"
            class="policy-error"
          >
            {{ problem.message }}
          </p>
        </div>
        <div v-if="!quotaRows.length" class="wide muted">当前没有具名策略；不配置时各维度不设 token 上限，由期限和消息上限结束。</div>
        <div class="wide actions">
          <v-btn
            variant="tonal"
            :disabled="!!currentSaveOutcome||!!busy"
            @click="quotaRows.push({name:'',work:null,user:null,scene:null})"
          >添加一项策略</v-btn>
        </div>
        <p class="wide muted">这里改的是往后新建工作的上限；已在执行的工作保留创建时的快照。已保存的精确取值在下方 JSON 里逐字对照。</p>
        <ul v-if="quotaProblems.length" class="wide error-summary">
          <li v-for="problem in quotaProblems" :key="problem.message">
            <button class="error-link" type="button" @click="focusPolicy(problem.key)">
              {{ problem.message }}
            </button>
          </li>
        </ul>
      </template>
      <template v-if="quotaRaw">
        <v-textarea
          :model-value="quotaText"
          readonly
          label="策略（JSON）"
          rows="10"
          class="wide runtime-json"
          hint="已保存取值的只读对照，没有保存入口；改数值请返回表单。切换视图不会丢掉未保存的表单草稿。"
          persistent-hint
        />
        <p class="wide muted">这是已保存取值的只读视图，没有保存按钮；改数值请返回表单编辑。</p>
      </template>
      <p v-if="Object.keys(rawPolicies).length" class="wide muted">有 {{ Object.keys(rawPolicies).length }} 项策略的形状不是这三个字段（{{ Object.keys(rawPolicies).join('、') }}），表单原样保留它们，只在保存时一起写回。</p>
      <ResourceViewer v-if="!quotaRaw" class="wide" title="已保存的精确取值（只读对照）" :content="quotaText" />
      <v-btn
        v-if="!Object.keys(rawPolicies).length"
        class="wide"
        variant="text"
        :disabled="!!currentSaveOutcome||!!busy"
        @click="toggleQuotaRaw"
      >
        {{ quotaRaw?'返回表单编辑':'查看已保存 JSON' }}
      </v-btn>
      <v-btn
        v-if="!quotaRaw"
        type="submit"
        color="primary"
        :loading="busy==='resources'"
        :disabled="!!currentSaveOutcome||!!busy||!!conflicts.entries.resources||!quotaDirty"
      >保存额度策略</v-btn>
      <span v-if="quotaDirty" class="muted">有未保存修改</span>
    </v-form>
  </v-card>
</template>
<style scoped>
.runtime-json :deep(textarea){font-family:monospace;font-size:13px;line-height:1.6}
.form-card{max-width:1000px;width:100%}
.section-header h2,.form-card>h2{font-size:20px}
.form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;align-items:start}
.wide{grid-column:1/-1}
.form-grid>.v-btn{justify-self:start}
.actions,.meta,.delivery-state,.saved-scenes{display:flex;gap:8px 12px;flex-wrap:wrap;align-items:center}
.error-summary{list-style:none;padding:0;margin:0;display:grid;gap:4px}
.policy-row{border:1px solid var(--line);border-radius:8px;padding:16px}
.policy-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
.policy-heading h3{font-size:14px;font-weight:650}
.policy-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
.policy-error{color:var(--error-text);font-size:13px;margin:8px 0 0}
.settings-view p{line-height:1.7}
@media(max-width:650px){
  .form-grid{grid-template-columns:minmax(0,1fr)}
  .policy-fields{grid-template-columns:minmax(0,1fr)}
}
</style>
