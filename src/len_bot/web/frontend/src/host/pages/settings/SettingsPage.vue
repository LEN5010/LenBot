<script setup>
import { computed, reactive } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../../../api.js'
import { useResource } from '../../../composables/useResource.js'
import { useUnsavedChanges } from '../../../composables/useUnsavedChanges.js'
import { useCurrentScene } from '../../../composables/useCurrentScene.js'
import { developerMode } from '../../../composables/useDeveloperMode.js'
import { notify, readPendingRestart } from '../../store.js'
import HostPage from '../../ui/HostPage.vue'
import PageTabs from '../../ui/PageTabs.vue'
import Panel from '../../ui/Panel.vue'
import ResourceState from '../../ui/ResourceState.vue'
import ConnectionSection from './ConnectionSection.vue'
import AccountSection from './AccountSection.vue'
import PermissionsSection from './PermissionsSection.vue'
import RetentionSection from './RetentionSection.vue'
import ProcessingSection from './ProcessingSection.vue'

const route = useRoute()
const tabs = [['connection', '连接'], ['account', '面板账号'], ['permissions', '权限'], ['retention', '数据保留'], ['advanced', '高级']]
const tab = computed(() => tabs.some(([key]) => key === route.query.tab) ? route.query.tab : 'connection')
const { scene } = useCurrentScene()
const settings = useResource(() => api('/api/host/settings'))
const dirty = reactive({})
useUnsavedChanges(computed(() => Object.values(dirty).some(Boolean)), { onDiscard: () => { for (const key of Object.keys(dirty)) dirty[key] = false } })

function saved(value) {
  if (value) settings.data.value = value
  readPendingRestart()
  notify('已保存')
}
</script>

<template>
  <HostPage title="设置">
    <PageTabs :tabs="tabs" :model-value="tab" label="设置分类" />
    <ResourceState :resource="settings" error-title="读取设置失败" v-slot="{ data }">
      <ConnectionSection v-if="tab === 'connection'" :snapshot="data" @saved="saved" @dirty="value => dirty.connection = value" />
      <AccountSection v-else-if="tab === 'account'" :snapshot="data" @saved="saved" @dirty="value => dirty.account = value" />
      <PermissionsSection v-else-if="tab === 'permissions' && scene" :key="scene" :scene="scene" @saved="saved" @dirty="value => dirty.permissions = value" />
      <RetentionSection v-else-if="tab === 'retention'" :snapshot="data" @saved="saved" @dirty="value => dirty.retention = value" />
      <template v-else-if="tab === 'advanced'">
        <Panel title="开发者模式">
          <v-switch v-model="developerMode" label="显示内部编号、原始请求与响应、完整数据"
            hint="只对当前浏览器页签有效，刷新页面后关闭" persistent-hint />
        </Panel>
        <ProcessingSection :snapshot="data" @saved="saved" @dirty="value => dirty.processing = value" />
      </template>
    </ResourceState>
  </HostPage>
</template>
