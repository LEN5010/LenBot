<script setup>
import { useAuth } from '../composables/useAuth.js'
const auth = useAuth()
</script>

<template>
  <div class="page-stack">
    <h1>页面未找到</h1>
    <v-card>
      <v-card-text class="empty-state">
        <p>404 · 这个地址没有对应页面。</p>
        <v-btn color="primary" :to="auth.panelContext.home">返回首页</v-btn>
      </v-card-text>
    </v-card>
  </div>
</template>
