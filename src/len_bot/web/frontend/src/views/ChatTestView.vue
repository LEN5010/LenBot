<script setup>
import HostPage from '../host/ui/HostPage.vue'
import ChatPanel from '../host/components/ChatPanel.vue'
</script>

<template>
  <HostPage title="对话测试" description="扮成群友和 Bot 聊几句。回复不会发到 QQ，但模型调用会消耗 token。" wide>
    <ChatPanel api-base="/api/chat-test" />
  </HostPage>
</template>
