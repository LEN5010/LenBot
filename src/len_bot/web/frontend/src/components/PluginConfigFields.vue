<script setup>
import {computed,nextTick,ref} from 'vue'
import {blankConfigDraft, configFields, enumLabels, exclusiveGroups, groupFor, listChoices, selectedBranch, chooseBranch} from '../lib/pluginConfig.js'

const props=defineProps({modelValue:{type:Object,required:true},schema:{type:Object,required:true},
  secrets:{type:Array,default:()=>[]},configSet:{type:Object,default:()=>({})},
  problems:{type:Array,default:()=>[]},prefix:{type:String,default:''},
  definitions:{type:Object,default:null},disabled:Boolean})
const emit=defineEmits(['update:modelValue'])
const path=key=>props.prefix?`${props.prefix}.${key}`:key
const fields=computed(()=>configFields(props.schema, props.definitions || props.schema?.$defs))
const groups=computed(()=>exclusiveGroups(props.schema))
// A nested object with declared properties is edited as its own subfields —
// the workspace worker/gateway backends are the case that exists today.
// Anything compound that the schema does not describe property-by-property
// stays an explicit JSON value instead of being guessed at.
const nestedSecrets=field=>props.secrets.filter(item=>item===path(field.key)||item.startsWith(path(field.key)+'.'))
const nestedConfigSet=field=>Object.fromEntries(Object.entries(props.configSet)
  .filter(([item])=>item===path(field.key)||item.startsWith(path(field.key)+'.')))
const childValue=(field,value)=>{
  if(props.disabled)return
  const branch=typeof value==='object'&&value!==null&&!Array.isArray(value)?value:{}
  emit('update:modelValue',{...props.modelValue,[field.key]:branch})
}
const anchors={}
const nestedRefs={}
const setAnchor=key=>element=>{ if (element) anchors[key]=element }
const setNestedRef=key=>element=>{ if (element) nestedRefs[key]=element }
// The same sentence the summary shows, repeated beside the field it names.
const messages=key=>props.problems.filter(item=>item.key===path(key)).map(item=>item.message)
const hasProblem=key=>messages(key).length>0
async function focus(target) {
  const name=String(target||'')
  const [head,...rest]=name.split('.')
  const nested=nestedRefs[head]
  // Nested sections have no top-level input anchor; go through the child
  // renderer that actually owns the field before requiring a local element.
  if (rest.length&&nested) { await nested.focus(rest.join('.')); return }
  const element=anchors[head]
  if (!element) return
  if (typeof element.focus==='function') element.focus()
  else element.$el?.querySelector?.('input,textarea,select,button')?.focus?.()
  await nextTick()
}
defineExpose({focus})
const visible=key=>{
  // A field inside an exclusive group is one branch of a choice: it is only
  // edited while that branch is the chosen one, and the file above the toggle
  // states which shapes exist.
  const group=groupFor(props.schema,key)
  return !group || selectedBranch(props.modelValue,group)===key
}
const branch=group=>selectedBranch(props.modelValue,group)
const update=(key,value)=>{if(!props.disabled)emit('update:modelValue',{...props.modelValue,[key]:value})}
const pick=(group,key)=>{
  if(props.disabled)return
  // Switching branches clears the branch that is not chosen instead of
  // submitting both: the backend accepts exactly one of them.  The chosen
  // branch starts from the schema's own defaults, so choosing the Gateway is
  // not the same click as writing an empty object over it.
  const field=fields.value.find(item=>item.key===key)
  const seed=field?.nested?blankConfigDraft(field.schema):'{}'
  emit('update:modelValue',chooseBranch(props.modelValue,group,key,seed))
}
const rows=(key,value)=>Array.isArray(value)?value:[]
const setRow=(key,value,index,row)=>{const next=[...rows(key,value)];next[index]=row;update(key,next)}
const emptyItem=declaration=>{
  const items=declaration.schema.items||{}
  if (items.enum?.length) return items.enum[0]
  if (items.type==='string') return ''
  if (items.type==='boolean') return false
  if (items.type==='integer'||items.type==='number') return 0
  return ''
}
const addRow=(key,value,declaration)=>update(key,[...rows(key,value),emptyItem(declaration)])
const removeRow=(key,value,index)=>update(key,rows(key,value).filter((_,position)=>position!==index))
const moveRow=(key,value,index,delta)=>{
  const next=[...rows(key,value)],target=index+delta
  if (target<0||target>=next.length) return
  ;[next[index],next[target]]=[next[target],next[index]]
  update(key,next)
}
const label=field=>`${field.schema.title||field.key}${field.required?' *':''}`
// Enum values stay the schema's own strings; the display name comes from the
// model that declares them.  An unlabelled value falls back to itself, so a
// new member is readable rather than missing from the list.
const enumItems=(field,{nullable=false}={})=>[...field.schema.enum.map(value=>({
  title:(enumLabels(props.schema,field.key)||{})[value]||value, value})),
  ...(nullable?[{title:'未设置',value:null}]:[])]
const choicesFor=field=>listChoices(props.schema,field.key)
const choiceTitle=(field,choice)=>(enumLabels(props.schema,field.key)||{})[choice]||choice
const toggleChoice=(field,value,choice)=>{
  const current=rows(field.key,value)
  update(field.key,current.includes(choice)?current.filter(item=>item!==choice):[...current,choice])
}
</script>

<template>
  <div class="config-fields">
    <template v-for="group in groups" :key="'group:'+group.title">
      <div class="exclusive-group">
        <p class="exclusive-title">{{ group.title }}</p>
        <p class="exclusive-hint muted">{{ group.hint }}</p>
        <v-btn-toggle :disabled="disabled" :model-value="branch(group)" mandatory divided color="primary" variant="outlined"
          @update:model-value="value=>value&&pick(group,value)">
          <v-btn v-for="key in group.fields" :key="key" :value="key" :disabled="disabled">{{ (fields.find(item=>item.key===key)?.schema.title) || key }}</v-btn>
        </v-btn-toggle>
        <p v-if="!branch(group)" class="muted mt-2">尚未选择；未选中的分支不会写入配置。</p>
      </div>
    </template>
    <template v-for="field in fields" :key="field.key">
      <template v-if="visible(field.key)">
      <v-text-field :disabled="disabled" v-if="secrets.includes(path(field.key))" :ref="setAnchor(field.key)" :model-value="modelValue[field.key]" @update:model-value="value=>update(field.key,value)"
        :label="label(field)" type="password" autocomplete="new-password" :placeholder="configSet[path(field.key)]?'已保存，留空保留':'尚未配置'"
        :hint="field.schema.description" :error="hasProblem(field.key)" :error-messages="messages(field.key)" persistent-hint />
      <v-text-field :disabled="disabled" v-else-if="field.schema.const!==undefined" :model-value="field.schema.const" :label="label(field)" readonly />
      <v-select :disabled="disabled" v-else-if="field.schema.enum" :ref="setAnchor(field.key)" :model-value="modelValue[field.key]" @update:model-value="value=>update(field.key,value)"
        :items="enumItems(field,{nullable:field.nullable})" :label="label(field)" :hint="field.schema.description"
        :error="hasProblem(field.key)" :error-messages="messages(field.key)" persistent-hint />
      <v-select :disabled="disabled" v-else-if="field.schema.type==='boolean'" :ref="setAnchor(field.key)" :model-value="modelValue[field.key]" @update:model-value="value=>update(field.key,value)"
        :items="[{title:'是',value:true},{title:'否',value:false},...(field.nullable?[{title:'未指定',value:null}]:[])]" :label="label(field)" :hint="field.schema.description"
        :error="hasProblem(field.key)" :error-messages="messages(field.key)" persistent-hint />
      <v-text-field :disabled="disabled" v-else-if="['number','integer'].includes(field.schema.type)" :ref="setAnchor(field.key)" :model-value="modelValue[field.key]" @update:model-value="value=>update(field.key,value===''?null:Number(value))"
        :label="label(field)" type="number" :min="field.schema.minimum" :max="field.schema.maximum" :step="field.schema.type==='integer'?1:'any'" :hint="field.schema.description"
        :error="hasProblem(field.key)" :error-messages="messages(field.key)" persistent-hint />
      <section v-else-if="field.nested" class="nested-field">
        <p class="nested-title">{{ label(field) }}</p>
        <p v-if="field.schema.description" class="muted mb-3">{{ field.schema.description }}</p>
        <PluginConfigFields :disabled="disabled" v-if="modelValue[field.key]" :ref="setNestedRef(field.key)" :model-value="modelValue[field.key]" :schema="field.schema"
          :secrets="nestedSecrets(field)" :config-set="nestedConfigSet(field)" :problems="problems" :prefix="path(field.key)"
          :definitions="field.definitions || definitions || schema.$defs"
          @update:model-value="value=>update(field.key,value)" />
        <div v-else class="actions"><v-btn size="small" variant="tonal" @click="childValue(field,modelValue[field.key])" :disabled="disabled">填写此分支</v-btn><span class="muted">尚未选择；不填写就不会写入配置。</span></div>
      </section>
      <section v-else-if="field.list" class="list-field">
        <p class="nested-title">{{ label(field) }}</p>
        <p v-if="field.schema.description" class="muted mb-2">{{ field.schema.description }}</p>
        <div v-if="choicesFor(field)" class="list-choices">
          <v-checkbox :disabled="disabled" v-for="choice in choicesFor(field)" :key="choice" :model-value="rows(field.key,modelValue[field.key]).includes(choice)"
            :label="choiceTitle(field,choice)" hide-details density="compact" @update:model-value="()=>toggleChoice(field,modelValue[field.key],choice)" />
          <p class="muted">当前可选项来自插件声明；取消全部勾选即提交空列表。</p>
        </div>
        <template v-else>
        <div v-for="(row,index) in rows(field.key,modelValue[field.key])" :key="index" class="list-row">
          <v-select :disabled="disabled" v-if="field.schema.items?.enum" :model-value="row" :items="field.schema.items.enum" :label="`第 ${index+1} 项`" hide-details @update:model-value="value=>setRow(field.key,modelValue[field.key],index,value)" />
          <v-text-field :disabled="disabled" v-else-if="['number','integer'].includes(field.schema.items?.type)" :model-value="row" :label="`第 ${index+1} 项`" type="number" hide-details @update:model-value="value=>setRow(field.key,modelValue[field.key],index,value===''?null:Number(value))" />
          <v-text-field :disabled="disabled" v-else :model-value="row" :label="`第 ${index+1} 项`" hide-details @update:model-value="value=>setRow(field.key,modelValue[field.key],index,value)" />
          <div class="list-actions"><v-btn size="small" variant="text" :disabled="disabled||index===0" @click="moveRow(field.key,modelValue[field.key],index,-1)">上移</v-btn><v-btn size="small" variant="text" :disabled="disabled||index===rows(field.key,modelValue[field.key]).length-1" @click="moveRow(field.key,modelValue[field.key],index,1)">下移</v-btn><v-btn size="small" variant="text" color="error" @click="removeRow(field.key,modelValue[field.key],index)" :disabled="disabled">移除</v-btn></div>
        </div>
        <p v-if="!rows(field.key,modelValue[field.key]).length" class="muted mb-2">当前为空列表。</p>
        <v-btn size="small" variant="tonal" @click="addRow(field.key,modelValue[field.key],field)" :disabled="disabled">添加一项</v-btn>
        </template>
        <p class="muted mt-2">保存的是这里的实际行数；删掉全部行即提交空列表，与“未填写”不是同一件事。</p>
      </section>
      <v-textarea :disabled="disabled" v-else-if="field.json" :ref="setAnchor(field.key)" class="compound-field" :model-value="modelValue[field.key]" @update:model-value="value=>update(field.key,value)"
        :label="label(field)+' · JSON'" rows="4" auto-grow :hint="field.schema.description||'按下方插件 Schema 填写对象或列表；保存时校验结构。'"
        :error="hasProblem(field.key)" :error-messages="messages(field.key)" persistent-hint />
      <v-textarea :disabled="disabled" v-else :ref="setAnchor(field.key)" :model-value="modelValue[field.key]" @update:model-value="value=>update(field.key,value)"
        :label="label(field)" rows="2" auto-grow :hint="field.schema.description"
        :error="hasProblem(field.key)" :error-messages="messages(field.key)" persistent-hint />
      </template>
    </template>
  </div>
</template>

<style scoped>
.config-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.compound-field{grid-column:1/-1}
.exclusive-group{grid-column:1/-1;border:1px solid #e2e8f0;border-radius:8px;padding:14px}
.exclusive-title{margin:0 0 4px;font-weight:600}.exclusive-hint{margin:0 0 10px;font-size:13px;line-height:1.6}
.nested-field,.list-field{grid-column:1/-1;border:1px solid #e2e8f0;border-radius:8px;padding:14px}
.nested-title{margin:0 0 6px;font-weight:600}
.list-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;align-items:center;margin-bottom:10px}
.list-choices{display:flex;flex-wrap:wrap;gap:4px 18px;margin-bottom:6px}
.list-choices .muted{flex-basis:100%;margin:0 0 4px;font-size:13px}
.list-actions{display:flex;gap:4px;flex-wrap:wrap}
.actions{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
@media(max-width:650px){.config-fields{grid-template-columns:minmax(0,1fr)}.list-row{grid-template-columns:minmax(0,1fr)}}
</style>
