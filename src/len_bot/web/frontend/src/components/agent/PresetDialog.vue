<script setup>
import ResourceViewer from '../ResourceViewer.vue'
const props = defineProps({state: {type: Object, required: true}, page: {type: Object, required: true}})
const { busy, saveOutcomes } = props.page
const { personaNeedsReadback, persona, preset, selectedPresetFields, selectedPresetExamples,
  presetExampleResults, presetMessage, personaLabels, imageUrl, fillPresetFields,
  savePresetExamples } = props.state
</script>
<template>
  <v-dialog
    :model-value="!!preset"
    max-width="920"
    scrollable
    :persistent="!!busy"
    @update:model-value="value=>!value&&(preset=null)"
  >
    <v-card v-if="preset">
      <v-card-title class="section-header">嘉然模板<v-btn variant="text" :disabled="!!busy" @click="preset=null">关闭</v-btn>
      </v-card-title>
      <v-card-text>
        <p class="mb-4">选择要填入人格草稿的字段，再使用“保存人格”。表达样例按选择逐条新增，各自显示保存结果。</p>
        <v-alert v-if="presetMessage" type="info" variant="tonal" class="mb-4">
          {{ presetMessage }}
        </v-alert>
        <h3 class="mb-3">人格字段</h3>
        <div v-for="(value,key) in preset.fields" :key="key" class="preset-field">
          <v-checkbox
            v-model="selectedPresetFields"
            :value="key"
            :label="personaLabels[key]"
            :disabled="!!busy"
            hide-details
          />
          <v-expansion-panels>
            <v-expansion-panel title="查看模板与当前草稿">
              <v-expansion-panel-text>
                <ResourceViewer title="模板内容" :content="value" />
                <ResourceViewer title="当前草稿" :content="persona[key]" />
              </v-expansion-panel-text>
            </v-expansion-panel>
          </v-expansion-panels>
        </div>
        <v-btn
          color="primary"
          variant="tonal"
          class="mt-4"
          :disabled="!!busy||!!saveOutcomes.persona||personaNeedsReadback||!selectedPresetFields.length"
          @click="fillPresetFields"
        >将所选字段填入草稿</v-btn>
        <v-divider class="my-6" />
        <h3>表达样例</h3>
        <v-alert v-if="preset.missing_media.length" type="warning" variant="tonal" class="mt-4">固定目录缺少素材：{{ preset.missing_media.join('、') }}。补充素材后重新查看模板，可选用对应图文样例。</v-alert>
        <article v-for="(item,index) in preset.examples" :key="item.id" class="example-row">
          <div class="example-main">
            <v-checkbox
              v-model="selectedPresetExamples"
              :value="item.id"
              :label="`新增第 ${index+1} 组样例`"
              :disabled="!!busy||item.missing_media_refs.length>0||presetExampleResults[item.id]?.status==='saved'"
              hide-details
            />
            <p class="example-context mt-3">{{ item.context }}</p>
            <div class="example-body">
              <template v-for="(part,partIndex) in item.segments" :key="partIndex">
                <p v-if="part.type==='text'">{{ part.text }}</p>
                <img v-else :src="imageUrl(part.asset_id)" alt="模板样例图片" />
              </template>
            </div>
            <p v-if="item.missing_media_refs.length" class="muted">{{ item.content }}</p>
            <v-alert
              v-if="presetExampleResults[item.id]"
              :type="presetExampleResults[item.id].status==='saved'?'success':presetExampleResults[item.id].status==='error'?'error':'info'"
              variant="tonal"
              density="compact"
            >
              {{ presetExampleResults[item.id].message }}
            </v-alert>
          </div>
        </article>
        <v-btn
          color="primary"
          variant="tonal"
          class="mt-4"
          :loading="busy==='preset-examples'"
          :disabled="!!busy||!selectedPresetExamples.length"
          @click="savePresetExamples"
        >逐条添加所选样例</v-btn>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn :disabled="!!busy" @click="preset=null">关闭预览</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
<style scoped>
.preset-field{margin-bottom:12px}
.section-header{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}
.example-row{display:flex;justify-content:space-between;align-items:flex-start;gap:20px;padding:24px 0;border-bottom:1px solid var(--line)}
.example-row:last-child{border:0;padding-bottom:0}
.example-main{min-width:0;flex:1}
.example-context{white-space:pre-wrap;line-height:1.65;color:var(--text-secondary);overflow-wrap:anywhere}
.example-body{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0;align-items:flex-start}
.example-body p{flex-basis:100%;white-space:pre-wrap;line-height:1.8;overflow-wrap:anywhere}
.example-body img{max-width:180px;max-height:180px;object-fit:contain}
@media(max-width:650px){
  .example-row{flex-direction:column}
  .section-header{align-items:flex-start}
  .example-body img{max-width:140px;max-height:140px}
}
</style>
