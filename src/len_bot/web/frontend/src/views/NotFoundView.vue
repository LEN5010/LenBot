<script setup>
import { useAuth } from '../composables/useAuth.js'
import HostPage from '../host/ui/HostPage.vue'
import Panel from '../host/ui/Panel.vue'
import EmptyState from '../host/ui/EmptyState.vue'
const auth = useAuth()
</script>

<template>
  <HostPage title="页面未找到">
    <Panel>
      <EmptyState text="这个地址没有对应的页面。"><v-btn color="primary" :to="auth.panelContext.home">返回首页</v-btn></EmptyState>
    </Panel>
  </HostPage>
</template>
