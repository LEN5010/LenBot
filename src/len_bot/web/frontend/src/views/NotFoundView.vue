<script setup>import PageHeader from '../components/PageHeader.vue'</script>
<template><div class="page-stack"><PageHeader title="页面未找到" description="这个地址没有对应页面，请检查链接或从导航进入。" /><v-card><v-card-text class="empty-state"><p>404 · 未知页面</p><v-btn color="primary" :to="{name:'overview'}">返回运行概览</v-btn></v-card-text></v-card></div></template>
