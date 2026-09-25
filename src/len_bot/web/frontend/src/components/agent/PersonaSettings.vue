<script setup>
import { fmtTime } from '../../api.js'
const props = defineProps({state: {type: Object, required: true}, page: {type: Object, required: true}})
const { route, busy, currentSaveOutcome, conflicts } = props.page
const { personaNeedsReadback, persona, exemplars, examplesLoading, examplesError,
  examplesReadAt, sourceExample, presetLoading, personaLabels, personaDirty, imageUrl,
  savePersona, editExample, changeExample, createFromSentMessage, previewPreset } = props.state
</script>
<template>
  <p class="muted">人物文字资料在本页保存；常服图片在<v-btn
      variant="text"
      :to="{name:'media',query:{scene:'global-safe',purpose:'character_reference',return_to:route.fullPath}}"
    >媒体与素材 → 人物与服装参考</v-btn>单独绑定。查看该入口不会修改人格字段。</p>
  <v-card v-if="persona" class="pa-5 form-card">
    <div class="section-header">
      <div><h2>人格与说话方式</h2><p class="muted mt-2">角色资料用于表达，不能作为群友事实或现实能力的依据。</p></div>
      <v-btn
        variant="tonal"
        :loading="presetLoading"
        :disabled="!!busy||personaNeedsReadback"
        @click="previewPreset"
      >查看嘉然模板</v-btn>
    </div>
    <v-form
      :disabled="!!currentSaveOutcome||!!busy||personaNeedsReadback"
      class="form-grid mt-5"
      @submit.prevent="savePersona"
    >
      <v-text-field v-model="persona.identity_name" label="机器人名字" />
      <v-text-field
        v-model="persona.addressNames"
        label="呼唤昵称"
        hint="用逗号或顿号分隔；呼唤提供观察机会，是否回应由模型决定。"
        persistent-hint
      />
      <v-textarea
        v-for="key in ['identity_persona','identity_core','character_context','conversation_style']"
        :key="key"
        v-model="persona[key]"
        :label="personaLabels[key]"
        :rows="key==='character_context'?6:4"
        auto-grow
        class="wide"
      />
      <v-btn
        type="submit"
        color="primary"
        :loading="busy==='persona'"
        :disabled="!!currentSaveOutcome||!!busy||personaNeedsReadback||!!conflicts.entries.persona||!personaDirty"
      >保存人格并立即生效</v-btn>
      <span v-if="personaDirty" class="muted">有未保存修改</span>
    </v-form>
  </v-card>
  <v-card class="pa-5">
    <v-alert v-if="examplesError" type="error" variant="tonal" class="mb-4">样例读取失败：{{ examplesError }}<span v-if="examplesReadAt">；保留 {{ fmtTime(examplesReadAt) }} 的列表</span>
    </v-alert>
    <v-progress-linear v-if="examplesLoading" indeterminate class="mb-4" />
    <p v-if="examplesReadAt" class="muted mb-3">样例读取于 {{ fmtTime(examplesReadAt) }}</p>
    <div class="section-header">
      <div><h2>表达样例</h2><p class="muted mt-2">按保存顺序提供，可使用文字、单图或混排。这些是人工表达示范。</p></div>
      <v-btn color="primary" variant="tonal" :disabled="!!busy" @click="editExample()">添加样例</v-btn>
    </div>
    <v-card variant="tonal" class="pa-4 mb-5">
      <h3>从真实送达消息创建</h3>
      <p class="muted my-2">只接受已确认真实发送的 Bot 消息；Shadow、草稿和 unknown 回执会被拒绝。</p>
      <div class="form-grid">
        <v-text-field
          :disabled="!!busy"
          v-model="sourceExample.scene_id"
          label="场景 ID"
          placeholder="group:123"
        />
        <v-text-field
          :disabled="!!busy"
          v-model="sourceExample.event_id"
          label="MESSAGE_SENT 事件 ID"
        />
        <v-text-field :disabled="!!busy" v-model="sourceExample.context" label="表达语境" />
        <v-text-field :disabled="!!busy" v-model="sourceExample.tag" label="标签" />
        <v-btn
          color="primary"
          variant="outlined"
          :loading="busy==='example-source'"
          :disabled="!!busy||!sourceExample.scene_id.trim()||!sourceExample.event_id.trim()"
          @click="createFromSentMessage"
        >创建并进入样例列表</v-btn>
      </div>
    </v-card>
    <p v-if="!examplesReadAt&&!examplesLoading&&!examplesError" class="muted py-6">尚未取得样例列表，请刷新当前设置后核对，不能据此判断没有样例。</p>
    <p
      v-if="examplesReadAt&&!examplesLoading&&!examplesError&&!exemplars.length"
      class="muted py-6"
    >尚无人工表达样例</p>
    <article v-for="(item,index) in exemplars" :key="item.id" class="example-row">
      <div class="example-main">
        <div class="meta mb-3">
          <v-chip size="small">第 {{ index+1 }} 条</v-chip>
          <v-chip size="small" :color="item.enabled?'success':'default'">
            {{ item.enabled?'已启用':'已停用' }}
          </v-chip>
          <span>{{ item.scene_id||'所有场景' }}</span>
          <span v-if="item.tag">{{ item.tag }}</span>
        </div>
        <p class="example-context clamp-2">{{ item.context||'通用表达' }}</p>
        <div class="example-body">
          <template v-for="(part,partIndex) in item.segments" :key="partIndex">
            <p v-if="part.type==='text'">{{ part.text }}</p>
            <img
              v-else
              :src="imageUrl(part.asset_id,item.scene_id)"
              alt="运营表达样例"
              loading="lazy"
            />
          </template>
        </div>
        <v-alert v-if="item.available===false" type="warning" variant="tonal" density="compact">
          {{ item.unavailable_reason }}
        </v-alert>
      </div>
      <div class="actions">
        <v-btn variant="outlined" :disabled="!!busy" @click="editExample(item)">编辑</v-btn>
        <v-btn variant="text" :disabled="!!busy" @click="changeExample(item)">
          {{ item.enabled?'停用':'启用' }}
        </v-btn>
        <v-btn
          color="error"
          variant="text"
          :disabled="!!busy"
          @click="changeExample(item,true)"
        >删除</v-btn>
      </div>
    </article>
  </v-card>
</template>
<style scoped>
.form-card{max-width:1000px;width:100%}
.section-header{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}
.section-header h2,.form-card>h2{font-size:20px}
.form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;align-items:start}
.wide{grid-column:1/-1}
.form-grid>.v-btn{justify-self:start}
.actions,.meta{display:flex;gap:8px 12px;flex-wrap:wrap;align-items:center}
.meta{font-size:13px;color:#64748b}
.example-row{display:flex;justify-content:space-between;align-items:flex-start;gap:20px;padding:24px 0;border-bottom:1px solid #e2e8f0}
.example-row:last-child{border:0;padding-bottom:0}
.example-main{min-width:0;flex:1}
.example-row>.actions{max-width:220px;justify-content:flex-end}
.example-context{white-space:pre-wrap;line-height:1.65;color:#64748b;overflow-wrap:anywhere}
.example-body{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0;align-items:flex-start}
.example-body p{flex-basis:100%;white-space:pre-wrap;line-height:1.8;overflow-wrap:anywhere}
.example-body img{max-width:180px;max-height:180px;object-fit:contain}
.settings-view p{line-height:1.7}
@media(max-width:650px){
  .form-grid{grid-template-columns:minmax(0,1fr)}
  .example-row{flex-direction:column}
  .example-row>.actions{max-width:none;justify-content:flex-start}
  .section-header{align-items:flex-start}
  .example-body img{max-width:140px;max-height:140px}
}
</style>
