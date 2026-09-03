<script setup>
import { ref, onMounted } from 'vue'
import { api, fmtAgo } from '../api.js'

const plugins = ref([])
const error = ref('')
const message = ref('')

onMounted(load)
async function load() {
  try { plugins.value = await api('/api/plugins/list') } catch (e) { error.value = e.message }
}

async function toggle(p) {
  error.value = ''; message.value = ''
  try {
    await api('/api/plugins/toggle', { method: 'POST', body: JSON.stringify({ plugin_id: p.id, enabled: !p.enabled }) })
    await load()
  } catch (e) { error.value = e.message }
}

async function saveConfig(p) {
  error.value = ''; message.value = ''
  try {
    const res = await api('/api/plugins/config', { method: 'POST', body: JSON.stringify({ plugin_id: p.id, config: p.config }) })
    message.value = `${p.id} 配置已保存`
    await load()
  } catch (e) { error.value = e.message }
}

// Render a primitive value for schema-driven fields
function schemaFields(p) {
  const props = p.config_schema?.properties || {}
  return Object.entries(props).map(([key, schema]) => ({ key, schema }))
}
function fieldValue(p, key) {
  if (p.config && key in p.config) return p.config[key]
  return p.default_config?.[key]
}
function setFieldValue(p, key, value) {
  if (!p.config) p.config = {}
  p.config[key] = value
}
function parseList(raw) {
  return raw.split(/[,\s]+/).filter(Boolean)
}
</script>

<template>
  <div>
    <h1>Plugins</h1>
    <p class="muted">真实 PluginHost 注册表（无 mock）。Sensor 只发事件；Tool 供 cognition 主动调用。</p>
    <p v-if="message" class="tag ok">{{ message }}</p>
    <p v-if="error" class="tag bad">{{ error }}</p>

    <div v-for="p in plugins" :key="p.id" class="panel">
      <div class="toolbar">
        <h3 style="margin:0; flex:1">{{ p.name }} <code>{{ p.id }}</code> v{{ p.version }}</h3>
        <span class="tag" :class="p.state === 'enabled' ? 'ok' : (p.state === 'error' ? 'bad' : '')">{{ p.state }}</span>
        <button @click="toggle(p)">{{ p.enabled ? '禁用' : '启用' }}</button>
      </div>
      <p class="muted" style="margin-top:0">{{ p.description }}</p>
      <div class="kv"><span class="k">类型 / 权限</span><span>{{ p.plugin_type }} · {{ p.permissions.join(', ') }}</span></div>
      <div class="kv" v-if="p.emitted_events?.length"><span class="k">发出事件</span><span>{{ p.emitted_events.join(', ') }}</span></div>
      <div class="kv" v-if="p.registered_tools?.length"><span class="k">注册工具</span><span>{{ p.registered_tools.join(', ') }}</span></div>
      <div class="kv"><span class="k">健康</span>
        <span>错误 {{ p.error_count }} · 最近事件 {{ p.last_event_at ? fmtAgo(p.last_event_at) : '—' }} · 最近执行 {{ p.last_run_at ? fmtAgo(p.last_run_at) : '—' }}</span></div>
      <p v-if="p.last_error" class="tag bad">{{ p.last_error }}</p>

      <div v-if="schemaFields(p).length" style="margin-top:10px">
        <h4 class="muted" style="margin:0 0 6px">配置 (config_schema 驱动)</h4>
        <div class="toolbar" v-for="f in schemaFields(p)" :key="f.key">
          <span class="k" style="min-width:160px">{{ f.schema.title || f.key }}</span>
          <template v-if="f.schema.type === 'array'">
            <input :value="(fieldValue(p, f.key) || []).join(', ')"
                   :placeholder="f.schema.items?.type === 'integer' ? '如 12345, 67890' : '逗号分隔'"
                   @input="setFieldValue(p, f.key, parseList($event.target.value).map(v => f.schema.items?.type === 'integer' ? Number(v) : v))" />
          </template>
          <template v-else-if="f.schema.type === 'number'">
            <input type="number" :value="fieldValue(p, f.key)" @input="setFieldValue(p, f.key, Number($event.target.value))" />
          </template>
          <template v-else>
            <input :value="fieldValue(p, f.key)" @input="setFieldValue(p, f.key, $event.target.value)" />
          </template>
        </div>
        <button class="primary" @click="saveConfig(p)">保存配置</button>
      </div>
    </div>

    <p v-if="!plugins.length" class="muted">无已装载插件</p>
  </div>
</template>
