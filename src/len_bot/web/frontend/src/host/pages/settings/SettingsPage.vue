<script setup>
import { computed, reactive } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../../../api.js'
import { useResource } from '../../../composables/useResource.js'
import { useUnsavedChanges } from '../../../composables/useUnsavedChanges.js'
import { developerMode } from '../../../composables/useDeveloperMode.js'
import { notify, readPendingRestart } from '../../store.js'
import HostPage from '../../components/HostPage.vue'
import ErrorNote from '../../components/ErrorNote.vue'
import ConnectionSection from './ConnectionSection.vue'
import AccountSection from './AccountSection.vue'
import PermissionsSection from './PermissionsSection.vue'
import LimitsSection from './LimitsSection.vue'
import RetentionSection from './RetentionSection.vue'
import ProcessingSection from './ProcessingSection.vue'

const route = useRoute(), router = useRouter()
const tabs = [['connection', '连接'], ['account', '面板账号'], ['permissions', '权限'], ['limits', '花费上限'], ['retention', '数据保留'], ['advanced', '高级']]
const tab = computed({
  get: () => tabs.some(([key]) => key === route.query.tab) ? route.query.tab : 'connection',
  set: value => router.replace({ query: { ...route.query, tab: value } }),
})
const scene = computed(() => typeof route.query.scene === 'string' ? route.query.scene : '')
const settings = useResource(() => api('/api/host/settings'))
const dirty = reactive({})
useUnsavedChanges(computed(() => Object.values(dirty).some(Boolean)))

function saved(value) {
  if (value) settings.data.value = value
  readPendingRestart()
  notify('已保存')
}
</script>

<template>
  <HostPage title="设置">
    <ErrorNote v-if="settings.error.value" title="读取设置失败" :error="settings.error.value" />
    <v-tabs v-model="tab" color="primary" show-arrows>
      <v-tab v-for="[key, label] in tabs" :key="key" :value="key">{{ label }}</v-tab>
    </v-tabs>
    <v-window v-if="settings.data.value" v-model="tab">
      <v-window-item value="connection"><ConnectionSection :snapshot="settings.data.value" @saved="saved" @dirty="value => dirty.connection = value" /></v-window-item>
      <v-window-item value="account"><AccountSection :snapshot="settings.data.value" @saved="saved" @dirty="value => dirty.account = value" /></v-window-item>
      <v-window-item value="permissions">
        <PermissionsSection :scene="scene" @saved="saved" @dirty="value => dirty.permissions = value"
          @scene="value => router.replace({ query: { ...route.query, scene: value } })" />
      </v-window-item>
      <v-window-item value="limits"><LimitsSection :snapshot="settings.data.value" @saved="saved" @dirty="value => dirty.limits = value" /></v-window-item>
      <v-window-item value="retention"><RetentionSection :snapshot="settings.data.value" @saved="saved" @dirty="value => dirty.retention = value" /></v-window-item>
      <v-window-item value="advanced">
        <div class="page-stack">
          <section class="surface">
            <h2>开发者模式</h2>
            <v-switch v-model="developerMode" label="显示内部编号、原始请求与响应、完整数据"
              hint="只对当前浏览器页签有效，刷新页面后关闭" persistent-hint />
          </section>
          <ProcessingSection :snapshot="settings.data.value" @saved="saved" @dirty="value => dirty.processing = value" />
        </div>
      </v-window-item>
    </v-window>
    <div v-else-if="settings.loading.value" class="surface empty-state">读取中…</div>
  </HostPage>
</template>
