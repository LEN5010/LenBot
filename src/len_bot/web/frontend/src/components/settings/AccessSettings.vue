<script setup>
const props = defineProps({state: {type: Object, required: true}, page: {type: Object, required: true}})
const { busy, currentSaveOutcome, conflicts } = props.page
const { accessText, grants, plugins, scopeOptions, policyOptions, referenceError,
  participantsFor, loadParticipants, capabilityItems, expiryPreview, accessProblems,
  grantCardProblems, saveAccess, addGrant, grantImpact } = props.state
</script>
<template>
  <v-card class="pa-5 form-card">
    <v-alert v-if="referenceError" type="error" variant="tonal" class="mb-4">群、成员、插件或额度策略参考读取失败：{{ referenceError }}；已有草稿保留，未自动选择替代项。</v-alert>
    <h2>QQ 回复白名单</h2>
    <p class="muted my-3">在已启用但关闭普通聊天的群中，白名单成员仍可正常提问和继续互动。白名单不会强制每条消息回复，也不授予管理员、跨群读取或 @全体权限；日程命令及引用评论仍保持安静。</p>
    <v-form :disabled="!!currentSaveOutcome||!!busy" @submit.prevent="saveAccess">
      <v-alert v-if="accessProblems.length" type="error" variant="tonal" class="mb-4">
        <p class="mb-2">请先修正以下内容；修正前不会提交保存。</p>
        <ul class="error-summary">
          <li v-for="(item,index) in accessProblems" :key="index+item.message">
            {{ item.message }}
          </li>
        </ul>
      </v-alert>
      <v-textarea
        v-model="accessText"
        data-field="whitelist"
        label="QQ 账号"
        rows="6"
        :error="accessProblems.some(item=>item.key==='whitelist')"
        :error-messages="accessProblems.filter(item=>item.key==='whitelist').map(item=>item.message)"
        hint="每行一个 QQ 账号，或用逗号分隔。这里填写 QQ 账号，不是 B 站 UID。空列表表示没有额外回复资格。"
        persistent-hint
      />
      <v-divider class="my-5" />
      <div class="section-header">
        <div>
          <h2>能力授予</h2>
          <p class="muted mt-2">只影响本计划新增的自主能力；普通聊天不需要这里的任何一条。未配置、已停用或已过期的授予一律不放行，撤销只阻止后续操作，已发出的字节无法撤回。</p>
        </div>
        <v-btn
          variant="tonal"
          color="primary"
          :disabled="!!currentSaveOutcome||!!busy"
          @click="addGrant"
        >添加授予</v-btn>
      </div>
      <p v-if="!grants.length" class="muted py-4">当前没有任何能力授予；新增自主能力保持关闭。</p>
      <section v-for="(grant,index) in grants" :key="index" class="grant-card">
        <h3>{{ index+1 }}. 谁 · 什么范围 · 允许什么</h3>
        <v-alert
          v-if="grantCardProblems(index).length"
          type="error"
          variant="tonal"
          density="compact"
          class="mb-3"
        >
          <ul class="error-summary">
            <li v-for="item in grantCardProblems(index)" :key="item.key+item.message">
              {{ item.message }}
            </li>
          </ul>
        </v-alert>
        <div class="form-grid">
          <v-select
            v-model="grant.principal_type"
            label="谁"
            :items="[{title:'一个群友（人类）',value:'human'},{title:'系统用途',value:'system'},{title:'一个插件',value:'plugin'}]"
            @update:model-value="value=>{grant.principal_type=value; if(value==='system') grant.scene_id=''; else grant.system_scope=''}"
          />
          <v-combobox
            v-if="grant.principal_type==='human'"
            v-model="grant.principal_id"
            :data-field="`principal_id:${index}`"
            :items="participantsFor(grant)"
            label="主体标识"
            :error="accessProblems.some(item=>item.key===`principal_id:${index}`)"
            :error-messages="accessProblems.filter(item=>item.key===`principal_id:${index}`).map(item=>item.message)"
            hint="从本群已记录成员中选择，或直接填 QQ 账号；保存的是 QQ 账号，不是 actor ID。"
            persistent-hint
            required
          />
          <v-select
            v-else-if="grant.principal_type==='plugin'"
            v-model="grant.principal_id"
            :data-field="`principal_id:${index}`"
            :items="plugins"
            label="哪个插件"
            :error="accessProblems.some(item=>item.key===`principal_id:${index}`)"
            :error-messages="accessProblems.filter(item=>item.key===`principal_id:${index}`).map(item=>item.message)"
            hint="从当前已声明插件中选择。"
            persistent-hint
            required
          />
          <v-text-field
            v-else
            v-model="grant.principal_id"
            :data-field="`principal_id:${index}`"
            label="主体标识"
            :error="accessProblems.some(item=>item.key===`principal_id:${index}`)"
            :error-messages="accessProblems.filter(item=>item.key===`principal_id:${index}`).map(item=>item.message)"
            hint="填明确的系统用途标识，例如 heartbeat。"
            persistent-hint
            required
          />
          <v-select
            v-if="grant.principal_type!=='system'"
            v-model="grant.scene_id"
            :data-field="`scene_id:${index}`"
            :items="scopeOptions"
            label="在哪个场景生效"
            :error="accessProblems.some(item=>item.key===`scene_id:${index}`)"
            :error-messages="accessProblems.filter(item=>item.key===`scene_id:${index}`).map(item=>item.message)"
            hint="从已保存的场景中选择；这里不新建群。"
            persistent-hint
            required
            @update:model-value="loadParticipants($event)"
          />
          <v-text-field
            v-else
            v-model="grant.system_scope"
            :data-field="`system_scope:${index}`"
            label="系统用途"
            :error="accessProblems.some(item=>item.key===`system_scope:${index}`)"
            :error-messages="accessProblems.filter(item=>item.key===`system_scope:${index}`).map(item=>item.message)"
            hint="明确的系统范围，例如 heartbeat；该词表不是登记表，需要人工填写。"
            persistent-hint
            required
          />
          <v-select
            v-model="grant.capabilities"
            :data-field="`capability:${index}`"
            multiple
            chips
            :items="capabilityItems"
            item-title="title"
            item-value="value"
            label="允许什么"
            class="wide"
            :error="accessProblems.some(item=>item.key===`capability:${index}`)"
            :error-messages="accessProblems.filter(item=>item.key===`capability:${index}`).map(item=>item.message)"
            required
          />
          <v-text-field
            v-model="grant.expiresInput"
            :data-field="`expires:${index}`"
            type="datetime-local"
            step="1"
            label="有效期（本机时区）"
            :error="accessProblems.some(item=>item.key===`expires:${index}`)"
            :error-messages="accessProblems.filter(item=>item.key===`expires:${index}`).map(item=>item.message)"
            :hint="`留空表示长期有效。这里按你这台机器的时区填写，保存时换算成绝对时间：${expiryPreview(index)}`"
            persistent-hint
          />
          <v-select
            v-model="grant.resource_policy"
            :items="policyOptions"
            label="使用哪项额度策略"
            clearable
            hint="从已保存的策略中选择；留空使用默认策略。没有可选策略时先去“额度策略”页保存。"
            persistent-hint
          />
          <v-text-field
            v-model.number="grant.concurrency"
            type="number"
            min="1"
            label="并发上限（可留空）"
          />
          <v-switch v-model="grant.enabled" label="启用这条授予" color="primary" />
        </div>
        <p class="muted mt-2">授予 ID 与版本由服务端负责：保存时按内容自动递增，签发者取当前登录账号。{{ grant.grant_id?`当前 ID ${grant.grant_id} · 第 ${grant.revision} 版；修改内容后版本自动加一。`:'新建的授予由服务端生成 ID。' }}
        </p>
        <v-btn
          variant="text"
          color="error"
          :disabled="!!currentSaveOutcome||!!busy"
          @click="grants.splice(index,1)"
        >删除这条授予</v-btn>
      </section>
      <div v-if="grantImpact.length" class="impact-summary">
        <p><strong>保存后的影响</strong>：这些主体在各自范围内将获准下列能力，下一次执行按新授予判断。</p>
        <p v-for="line in grantImpact" :key="line" class="muted">{{ line }}</p>
        <p class="muted">撤销或停用只阻止后续操作；已经发出的消息无法撤回，也不会改动其他未编辑的授予。</p>
      </div>
      <p class="muted mb-3">保存把白名单与能力授予一起写入根配置；它不发送消息、不调用模型，也不改动未编辑的授予。</p>
      <v-btn
        type="submit"
        color="primary"
        :loading="busy==='access'"
        :disabled="!!currentSaveOutcome||!!busy||!!conflicts.entries.access"
      >保存白名单与能力授予</v-btn>
    </v-form>
  </v-card>
</template>
<style scoped>
.form-card{max-width:1000px;width:100%}
.grant-card{border:1px solid #e2e8f0;border-radius:8px;padding:16px;margin-bottom:18px}
.grant-card h3{font-size:14px;font-weight:650;margin-bottom:12px}
.impact-summary{border-left:3px solid rgb(var(--v-theme-primary));padding:12px 14px;margin:16px 0;background:rgb(var(--v-theme-surface-variant));max-width:1000px}
.impact-summary p{margin:4px 0;font-size:13px;line-height:1.7}
.section-header{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}
.section-header h2,.form-card>h2{font-size:20px}
.form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;align-items:start}
.wide{grid-column:1/-1}
.form-grid>.v-btn{justify-self:start}
.error-summary{list-style:none;padding:0;margin:0;display:grid;gap:4px}
.settings-view p{line-height:1.7}
@media(max-width:650px){
  .form-grid{grid-template-columns:minmax(0,1fr)}
  .section-header{align-items:flex-start}
}
</style>
