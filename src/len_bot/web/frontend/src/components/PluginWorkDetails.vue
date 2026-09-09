<script setup>
import {fmtTime} from '../api.js'
import ResourceViewer from './ResourceViewer.vue'
import PluginOrigin from './PluginOrigin.vue'
defineProps({job:{type:Object,required:true}})
const label=(schema,key)=>schema?.properties?.[key]?.title || key
const description=(schema,key)=>schema?.properties?.[key]?.description
const display=(value,schema,key)=>schema?.properties?.[key]?.format==='unix-time' ? fmtTime(value)
  : typeof value==='boolean' ? (value?'是':'否') : value===null ? '未记录'
  : typeof value==='object' ? JSON.stringify(value,null,2) : String(value)
</script>

<template>
  <section v-if="job.plugin_origin || job.legacy_payload">
    <h3>插件业务</h3>
    <PluginOrigin :origin="job.plugin_origin" :name="job.plugin_name" :scene-id="job.scene_id" />
    <v-alert v-if="job.plugin_issue" type="warning" variant="tonal" class="my-4">{{ job.plugin_issue }}</v-alert>
    <template v-for="(schema,field) in {work_parameters:job.work_parameters_schema,work_progress:job.work_progress_schema}" :key="field">
      <h4 v-if="job[field]">{{ field==='work_parameters'?'固定业务参数':'实际业务进度' }}</h4>
      <dl v-if="job[field]" class="business-fields">
        <template v-for="(value,key) in job[field]" :key="key">
          <dt>{{ label(schema,key) }}</dt><dd>{{ display(value,schema,key) }}<small v-if="description(schema,key)">{{ description(schema,key) }}</small></dd>
        </template>
      </dl>
    </template>
    <ResourceViewer v-if="job.legacy_payload" title="升级前的业务记录（只读）" :content="JSON.stringify(job.legacy_payload,null,2)" />
  </section>
</template>

<style scoped>
.business-fields{display:grid;grid-template-columns:160px minmax(0,1fr);gap:10px 18px;margin:14px 0 22px}.business-fields dt{color:rgb(var(--v-theme-on-surface-variant))}.business-fields dd{margin:0;white-space:pre-wrap;overflow-wrap:anywhere}.business-fields small{display:block;margin-top:4px;color:rgb(var(--v-theme-on-surface-variant))}@media(max-width:650px){.business-fields{grid-template-columns:1fr;gap:4px}.business-fields dd{margin-bottom:10px}}
</style>
