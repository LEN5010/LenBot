<script setup>
import { ref, onMounted } from 'vue'
import { api, fmtAgo } from '../api.js'

const plugins = ref([])
const error = ref('')
const message = ref('')

onMounted(load)
async function load() {
  try {
    plugins.value = await api('/api/plugins/list')
  } catch (e) {
    error.value = e.message
  }
}

async function toggle(p) {
  error.value = ''
  message.value = ''
  try {
    await api('/api/plugins/toggle', {
      method: 'POST',
      body: JSON.stringify({ plugin_id: p.id, enabled: !p.enabled })
    })
    await load()
  } catch (e) {
    error.value = e.message
  }
}

async function saveConfig(p) {
  error.value = ''
  message.value = ''
  try {
    const res = await api('/api/plugins/config', {
      method: 'POST',
      body: JSON.stringify({ plugin_id: p.id, config: p.config })
    })
    message.value = `“${p.name}”的配置已保存`
    await load()
  } catch (e) {
    error.value = e.message
  }
}

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
  <div class="plugins-view">
    <div class="toolbar">
      <div class="page-title">
        <h1>扩展能力</h1>
        <p class="muted">管理直播监测、网页查询等机器人能力。</p>
      </div>
      <button class="primary" @click="load">
        <span>刷新</span>
      </button>
    </div>

    <p v-if="message" class="tag ok" style="margin-bottom: 16px;">✓ {{ message }}</p>
    <p v-if="error" class="tag bad" style="margin-bottom: 16px;">✕ {{ error }}</p>

    <!-- Plugin Bento Cards List -->
    <div class="plugin-list">
      <div v-for="p in plugins" :key="p.id" class="panel plugin-card">
        <div class="plugin-header">
          <div class="plugin-title-group">
            <span class="plugin-type-badge">
              {{ p.plugin_type === 'sensor' ? '信息监测' : '查询工具' }}
            </span>
            <h3>{{ p.name }} <code>{{ p.id }}</code> <span class="tag">v{{ p.version }}</span></h3>
            <p class="muted plugin-desc">{{ p.description }}</p>
          </div>
          <div class="plugin-action-group">
            <span class="tag" :class="p.state === 'enabled' ? 'ok' : (p.state === 'error' ? 'bad' : '')">
              {{ p.state === 'enabled' ? '运行中' : p.state === 'disabled' ? '已禁用' : p.state }}
            </span>
            <button :class="p.enabled ? 'danger' : 'primary'" @click="toggle(p)">
              {{ p.enabled ? '停用插件' : '启用插件' }}
            </button>
          </div>
        </div>

        <div class="bento-grid" style="margin: 16px 0;">
          <div class="bento-card bento-col-4">
            <div class="bento-badge">它能做什么</div>
            <div class="kv">
              <span class="k">已获权限</span>
              <span class="v">{{ p.permissions.join(', ') || '无特殊权限' }}</span>
            </div>
            <div class="kv" v-if="p.emitted_events?.length">
              <span class="k">能够发现</span>
              <span class="v code-text">{{ p.emitted_events.join(', ') }}</span>
            </div>
            <div class="kv" v-if="p.registered_tools?.length">
              <span class="k">提供工具</span>
              <span class="v code-text">{{ p.registered_tools.join(', ') }}</span>
            </div>
          </div>

          <div class="bento-card bento-col-8">
            <div class="bento-badge">运行情况</div>
            <div class="kv">
              <span class="k">发生错误</span>
              <span class="v" :class="p.error_count > 0 ? 'bad-text' : 'ok-text'">{{ p.error_count }} 次</span>
            </div>
            <div class="kv">
              <span class="k">最近发现信息</span>
              <span class="v">{{ p.last_event_at ? fmtAgo(p.last_event_at) : '—' }}</span>
            </div>
            <div class="kv">
              <span class="k">最近使用</span>
              <span class="v">{{ p.last_run_at ? fmtAgo(p.last_run_at) : '—' }}</span>
            </div>
            <p v-if="p.last_error" class="tag bad" style="margin-top: 8px;">最近错误：{{ p.last_error }}</p>
          </div>
        </div>

        <!-- Dynamic Configuration Form (schema-driven) -->
        <div v-if="schemaFields(p).length" class="config-box">
          <h4 style="margin: 0 0 12px; color: var(--text-soft);">参数设置</h4>
          <div class="config-fields-grid">
            <div class="field-item" v-for="f in schemaFields(p)" :key="f.key">
              <label>{{ f.schema.title || f.key }}</label>
              <template v-if="f.schema.type === 'array'">
                <input
                  :value="(fieldValue(p, f.key) || []).join(', ')"
                  :placeholder="f.schema.items?.type === 'integer' ? '例如 12345, 67890' : '使用逗号分隔多个值'"
                  @input="setFieldValue(p, f.key, parseList($event.target.value).map(v => f.schema.items?.type === 'integer' ? Number(v) : v))"
                />
              </template>
              <template v-else-if="f.schema.type === 'number'">
                <input type="number" :value="fieldValue(p, f.key)" @input="setFieldValue(p, f.key, Number($event.target.value))" />
              </template>
              <template v-else>
                <input :value="fieldValue(p, f.key)" @input="setFieldValue(p, f.key, $event.target.value)" />
              </template>
            </div>
          </div>
          <button class="primary" style="margin-top: 14px;" @click="saveConfig(p)">保存插件配置</button>
        </div>
      </div>
    </div>

    <p v-if="!plugins.length" class="muted" style="text-align: center; padding: 36px;">当前系统暂无已装载插件</p>
  </div>
</template>

<style scoped>
.page-title h1 {
  margin: 0;
  font-size: 1.4rem;
}
.page-title p {
  margin: 4px 0 0;
  font-size: 0.85rem;
}

.plugin-card {
  margin-bottom: 24px;
}

.plugin-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  border-bottom: 1px solid var(--border);
  padding-bottom: 14px;
}

.plugin-type-badge {
  font-size: 0.74rem;
  font-weight: 600;
  color: var(--accent-strong);
  margin-bottom: 4px;
  display: inline-block;
}

.plugin-title-group h3 {
  margin: 2px 0 6px;
  font-size: 1.1rem;
}

.plugin-desc {
  margin: 0;
  font-size: 0.88rem;
}

.plugin-action-group {
  display: flex;
  align-items: center;
  gap: 10px;
}

.bento-col-4 {
  grid-column: span 4;
}
.bento-col-8 {
  grid-column: span 8;
}

@media (max-width: 960px) {
  .bento-col-4, .bento-col-8 {
    grid-column: span 12;
  }
}

.code-text {
  font-family: monospace;
  font-size: 0.84rem;
}

.ok-text { color: var(--ok); font-weight: 600; }
.bad-text { color: var(--bad); font-weight: 600; }

.config-box {
  background: rgba(239, 246, 255, 0.66);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 16px 18px;
  margin-top: 14px;
}

.config-fields-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 14px;
}

.field-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.field-item label {
  font-size: 0.82rem;
  color: var(--muted);
  font-weight: 500;
}
</style>
