<script setup>
import {computed} from 'vue'
import {configFields} from '../lib/pluginConfig.js'

const props=defineProps({modelValue:{type:Object,required:true},schema:{type:Object,required:true},
  secrets:{type:Array,default:()=>[]},configSet:{type:Object,default:()=>({})}})
const emit=defineEmits(['update:modelValue'])
const fields=computed(()=>configFields(props.schema))
const update=(key,value)=>emit('update:modelValue',{...props.modelValue,[key]:value})
const label=field=>`${field.schema.title||field.key}${field.required?' *':''}`
</script>

<template>
  <div class="config-fields">
    <template v-for="field in fields" :key="field.key">
      <v-text-field v-if="secrets.includes(field.key)" :model-value="modelValue[field.key]" @update:model-value="value=>update(field.key,value)"
        :label="label(field)" type="password" autocomplete="new-password" :placeholder="configSet[field.key]?'已保存，留空保留':'尚未配置'" :hint="field.schema.description" />
      <v-text-field v-else-if="field.schema.const!==undefined" :model-value="field.schema.const" :label="label(field)" readonly />
      <v-select v-else-if="field.schema.enum" :model-value="modelValue[field.key]" @update:model-value="value=>update(field.key,value)"
        :items="field.nullable?[...field.schema.enum,null]:field.schema.enum" :label="label(field)" :hint="field.schema.description" persistent-hint />
      <v-select v-else-if="field.schema.type==='boolean'" :model-value="modelValue[field.key]" @update:model-value="value=>update(field.key,value)"
        :items="[{title:'是',value:true},{title:'否',value:false},...(field.nullable?[{title:'未指定',value:null}]:[])]" :label="label(field)" :hint="field.schema.description" persistent-hint />
      <v-text-field v-else-if="['number','integer'].includes(field.schema.type)" :model-value="modelValue[field.key]" @update:model-value="value=>update(field.key,value===''?null:Number(value))"
        :label="label(field)" type="number" :min="field.schema.minimum" :max="field.schema.maximum" :step="field.schema.type==='integer'?1:'any'" :hint="field.schema.description" persistent-hint />
      <v-textarea v-else-if="field.json" class="compound-field" :model-value="modelValue[field.key]" @update:model-value="value=>update(field.key,value)"
        :label="label(field)+' · JSON'" rows="4" auto-grow :hint="field.schema.description||'按下方插件 Schema 填写对象或列表；保存时校验结构。'" persistent-hint />
      <v-textarea v-else :model-value="modelValue[field.key]" @update:model-value="value=>update(field.key,value)"
        :label="label(field)" rows="2" auto-grow :hint="field.schema.description" persistent-hint />
    </template>
  </div>
</template>

<style scoped>
.config-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.compound-field{grid-column:1/-1}@media(max-width:650px){.config-fields{grid-template-columns:minmax(0,1fr)}}
</style>
