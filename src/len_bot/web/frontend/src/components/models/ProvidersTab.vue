<script setup>
import ConfigConflictBanner from '../ConfigConflictBanner.vue'
import ResourceViewer from '../ResourceViewer.vue'
import StatusBadge from '../StatusBadge.vue'
const props = defineProps({state: {type: Object, required: true}, page: {type: Object, required: true}})
const { busy, loading, loaded, error, writeHeld, data, conflicts, refresh, resolveConflict } = props.page
const { catalogs, selectedModels, catalogInvalid, orphanCatalogs, inUse, catalogUnavailable,
  cancelCatalog, editProvider, deleteProvider, fetchModels, saveModels } = props.state
</script>
<template>
  <p class="muted">密钥只在后台保存，编辑时明确选择保留、替换或清除。获取接口目录会访问当前运行接口，但不会生成模型回答。</p>
  <v-card v-for="id in orphanCatalogs" :key="`missing:${id}`" class="pa-5 provider-card">
    <h2>{{ id }} · 目录草稿失效</h2>
    <p class="my-3">{{ catalogInvalid[id] }}</p>
    <ResourceViewer :content="selectedModels[id]" title="未保存的目录选择（仅供核对）" />
    <v-btn
      class="mt-3"
      variant="outlined"
      :disabled="!!busy||writeHeld"
      @click="cancelCatalog(id)"
    >放弃这份目录草稿</v-btn>
  </v-card>
  <v-card v-for="provider in data.providers" :key="provider.id" class="pa-5 provider-card">
    <div class="provider-heading">
      <div class="provider-name">
        <h2>{{ provider.id }}</h2>
        <p class="provider-url muted">{{ provider.base_url }}</p>
      </div>
      <StatusBadge domain="provider" :status="provider.enabled?'enabled':'disabled'" />
    </div>
    <div class="provider-meta">
      <span>密钥 {{ provider.api_key_masked || '未设置' }}</span>
      <span>超时 {{ provider.timeout_seconds }} 秒</span>
      <span v-if="inUse(provider.id)">已保存的职责或检索绑定引用</span>
    </div>
    <div class="actions">
      <v-btn
        variant="outlined"
        :disabled="!!busy||loading||writeHeld"
        @click="editProvider(provider)"
      >编辑接口</v-btn>
      <v-btn
        color="error"
        variant="text"
        :disabled="!!busy||loading||writeHeld||inUse(provider.id)||(!!conflicts.entries[`delete:${provider.id}`]&&!conflicts.entries[`delete:${provider.id}`].snapshot)"
        @click="deleteProvider(provider)"
      >删除</v-btn>
    </div>
    <v-divider class="my-4" />
    <h3 class="mb-3">常用模型目录</h3>
    <template v-if="catalogs[provider.id]">
      <v-alert v-if="catalogInvalid[provider.id]" type="warning" variant="tonal" class="mb-3">
        {{ catalogInvalid[provider.id] }}<p v-if="conflicts.entries[`models:${provider.id}`]">
          {{ conflicts.entries[`models:${provider.id}`].problem.message }}
        </p>
      </v-alert>
      <ConfigConflictBanner
        v-else
        :conflict="conflicts.entries[`models:${provider.id}`]?.problem"
        :current="conflicts.entries[`models:${provider.id}`]?.snapshot"
        :path-label="conflicts.entries[`models:${provider.id}`]?.problem.path.join(' → ')"
        :read-at="conflicts.entries[`models:${provider.id}`]?.readAt"
        :read-error="conflicts.entries[`models:${provider.id}`]?.readError"
        :busy="!!busy||loading||writeHeld"
        @keep="resolveConflict(`models:${provider.id}`,true)"
        @take="resolveConflict(`models:${provider.id}`,false)"
        @reload="refresh"
      >
        <template #current>
          <ResourceViewer
            :content="conflicts.entries[`models:${provider.id}`]?.snapshot?.provider?.models"
            title="本次保存的常用模型"
          />
        </template>
      </ConfigConflictBanner>
      <v-autocomplete
        :disabled="!!busy||loading||writeHeld||!!catalogInvalid[provider.id]"
        v-model="selectedModels[provider.id]"
        :items="[...new Set([...catalogs[provider.id],...provider.models,...selectedModels[provider.id]])]"
        label="搜索并选择常用模型"
        multiple
        chips
        closable-chips
        clearable
      />
      <div class="actions">
        <v-btn
          color="primary"
          :loading="busy===`models:${provider.id}`"
          :disabled="!!busy||loading||writeHeld||!!catalogInvalid[provider.id]||!!conflicts.entries[`models:${provider.id}`]"
          @click="saveModels(provider)"
        >保存常用模型</v-btn>
        <v-btn variant="text" :disabled="!!busy||writeHeld" @click="cancelCatalog(provider.id)">取消选择</v-btn>
      </div>
    </template>
    <template v-else>
      <div class="model-tags">
        <v-chip v-for="model in provider.models" :key="model" size="small">{{ model }}</v-chip>
        <span v-if="!provider.models.length" class="muted">尚未保存常用模型</span>
      </div>
      <p v-if="catalogUnavailable(provider)" class="muted mt-3">
        {{ catalogUnavailable(provider) }}
      </p>
      <v-btn
        class="mt-4"
        variant="tonal"
        :loading="busy===`catalog:${provider.id}`"
        :disabled="!!busy||loading||writeHeld||!!catalogUnavailable(provider)"
        @click="fetchModels(provider)"
      >获取接口模型目录</v-btn>
    </template>
  </v-card>
  <v-card v-if="loaded&&!error&&!data.providers.length" class="pa-8 text-center muted">还没有供应商，点击“添加供应商”开始配置。</v-card>
</template>
<style scoped>
.role-title,.provider-heading,.dialog-title{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
.role-title h2,.provider-heading h2{font-size:19px}
.provider-card{min-width:0}
.provider-name{min-width:0}
.provider-url{overflow-wrap:anywhere;margin-top:8px}
.provider-meta,.actions,.model-tags{display:flex;flex-wrap:wrap;gap:10px 16px}
.provider-meta{font-size:13px;color:#64748b;margin:16px 0}
.model-tags .v-chip{max-width:100%;height:auto;min-height:26px;white-space:normal;overflow-wrap:anywhere}
</style>
