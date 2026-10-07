<script setup>
import HostPage from '../host/ui/HostPage.vue'
import ChatPanel from '../host/components/ChatPanel.vue'
</script>

<template>
  <HostPage title="对话测试" wide>
    <ChatPanel api-base="/api/chat-test" />
  </HostPage>
</template>
