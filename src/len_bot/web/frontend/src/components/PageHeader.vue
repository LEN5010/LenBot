<script setup>
defineProps({ title: String, description: String })
</script>
<template>
  <header class="page-header">
    <div class="page-header__copy"><h1>{{ title }}</h1><p v-if="description">{{ description }}</p></div>
    <div v-if="$slots.default" class="page-header__actions"><slot /></div>
  </header>
</template>
<style scoped>
.page-header { display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap }
.page-header__copy { min-width:0;flex:1 1 240px }.page-header h1 { font-size:26px;line-height:1.3;font-weight:700;letter-spacing:-.025em;color:var(--ink) }
.page-header p { color:var(--muted);margin:8px 0 0;line-height:1.6;max-width:80ch;font-size:14px }
.page-header__actions { display:flex;align-items:center;gap:8px;flex-wrap:wrap }
@media(max-width:600px){.page-header h1{font-size:24px}.page-header__copy{flex-basis:100%}}
</style>
