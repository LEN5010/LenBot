<script setup>
import EntityLink from '../EntityLink.vue'
import ScopeSelect from '../ScopeSelect.vue'
const props = defineProps({state: {type: Object, required: true}, page: {type: Object, required: true}})
const { busy, error } = props.page
const { exampleOpen, editingExample, example, exampleDirty, imageUrl, closeExample, changePart,
  addPart, movePart, saveExample, openMedia } = props.state
</script>
<template>
  <v-dialog
    :model-value="exampleOpen"
    max-width="880"
    scrollable
    :persistent="!!busy"
    @update:model-value="value=>!value&&closeExample()"
  >
    <v-card>
      <v-card-title class="section-header">
        {{ editingExample?'编辑表达样例':'添加表达样例' }}<v-btn variant="text" :disabled="!!busy" @click="closeExample">关闭</v-btn>
      </v-card-title>
      <v-card-text>
        <v-alert v-if="error" type="error" variant="tonal" class="mb-4">{{ error }}</v-alert>
        <v-form v-if="example" :disabled="!!busy" @submit.prevent="saveExample">
          <div class="form-grid">
            <v-textarea
              v-model="example.context"
              label="前文与语境"
              maxlength="8000"
              rows="3"
              class="wide"
            />
            <ScopeSelect v-model="example.scene_id" clearable />
            <v-text-field v-model="example.tag" label="样例标签" maxlength="200" />
          </div>
          <p class="muted mb-4">范围留空适用于所有场景，只能使用公共运营素材。本群样例也可使用本群运营素材。</p>
          <div class="section-header">
            <h3>表达片段</h3>
            <div class="actions">
              <v-btn
                size="small"
                variant="tonal"
                :disabled="!!busy||example.segments.length>=20"
                @click="addPart('text')"
              >添加文字</v-btn>
              <v-btn
                size="small"
                variant="tonal"
                :disabled="!!busy||example.segments.length>=20"
                @click="addPart('image')"
              >添加图片</v-btn>
            </div>
          </div>
          <v-card
            v-for="(part,index) in example.segments"
            :key="index"
            variant="outlined"
            class="pa-4 my-3"
          >
            <div class="part-toolbar">
              <span class="muted">第 {{ index+1 }} 段</span>
              <v-select
                :model-value="part.type"
                :items="[{title:'文字',value:'text'},{title:'图片',value:'image'}]"
                label="片段类型"
                hide-details
                @update:model-value="value=>changePart(index,value)"
              />
              <div class="actions">
                <v-btn
                  size="small"
                  variant="text"
                  :disabled="!!busy||index===0"
                  @click="movePart(index,-1)"
                >上移</v-btn>
                <v-btn
                  size="small"
                  variant="text"
                  :disabled="!!busy||index===example.segments.length-1"
                  @click="movePart(index,1)"
                >下移</v-btn>
                <v-btn
                  size="small"
                  color="error"
                  variant="text"
                  :disabled="!!busy"
                  @click="example.segments.splice(index,1)"
                >移除</v-btn>
              </div>
            </div>
            <v-textarea
              v-if="part.type==='text'"
              v-model="part.text"
              label="要说的话"
              rows="3"
              auto-grow
              required
            />
            <template v-else>
              <v-btn variant="outlined" class="my-3" :disabled="!!busy" @click="openMedia(index)">
                {{ part.asset_id?'重新选择运营素材':'选择运营素材' }}
              </v-btn>
              <div v-if="part.asset_id" class="part-image">
                <img :src="imageUrl(part.asset_id,example.scene_id)" alt="当前样例图片" />
                <EntityLink
                  type="media"
                  :id="part.asset_id"
                  :scene-id="example.scene_id||'global-safe'"
                  label="查看素材来源"
                />
              </div>
            </template>
          </v-card>
          <v-btn
            type="submit"
            color="primary"
            :loading="busy==='example'"
            :disabled="!!busy||!example.segments.length||!exampleDirty"
          >
            {{ editingExample?'保存样例修改':'添加样例' }}
          </v-btn>
        </v-form>
      </v-card-text>
    </v-card>
  </v-dialog>
</template>
<style scoped>
.section-header{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}
.form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;align-items:start}
.wide{grid-column:1/-1}
.form-grid>.v-btn{justify-self:start}
.actions,.meta{display:flex;gap:8px 12px;flex-wrap:wrap;align-items:center}
.part-toolbar{display:flex;gap:12px;align-items:center;margin-bottom:16px;flex-wrap:wrap}
.part-toolbar>.v-input{flex:1;min-width:140px;max-width:180px}
.part-image{display:flex;gap:16px;align-items:center;flex-wrap:wrap}
.part-image img{max-width:100%;height:170px;object-fit:contain}
@media(max-width:650px){
  .form-grid{grid-template-columns:minmax(0,1fr)}
  .section-header{align-items:flex-start}
  .part-toolbar>.actions{width:100%}
}
</style>
