<script setup>
import ChatPanel from '../host/components/ChatPanel.vue'
</script>

<template>
  <div class="page-stack">
    <header class="page-intro"><div><h1>对话测试</h1>
      <p class="muted">扮成群友和 Bot 聊几句。回复不会发到 QQ，但模型调用会产生费用。</p></div></header>
    <ChatPanel api-base="/api/chat-test" />
  </div>
</template>
