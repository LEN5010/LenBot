<script setup>
defineProps({
  title: { type: String, default: '' },
  description: { type: String, default: '' },
  tag: { type: String, default: 'section' },
  icon: { type: String, default: '' },
  flush: Boolean,
})
</script>
<template>
  <component :is="tag" class="panel" :class="{ flush }">
    <header v-if="title || $slots.actions || $slots.title" class="panel-head">
      <span v-if="icon" class="panel-icon"><v-icon :icon="icon" size="18" /></span>
      <div class="panel-title"><slot name="title"><h2>{{ title }}</h2></slot>
        <p v-if="description" class="muted">{{ description }}</p></div>
      <div v-if="$slots.actions" class="panel-actions"><slot name="actions" /></div>
    </header>
    <div class="panel-body"><slot /></div>
    <footer v-if="$slots.footer" class="panel-foot"><slot name="footer" /></footer>
  </component>
</template>
<style scoped>
.panel{min-width:0;display:grid;gap:var(--sp-4);align-content:start}
.panel:not(.flush) + .panel:not(.flush){border-top:1px solid var(--line);padding-top:var(--sp-5);margin-top:var(--sp-2)}
.panel.flush{gap:var(--sp-1)}
.panel.flush .panel-head{padding:0 var(--sp-5) var(--sp-1) calc(var(--sp-2) + var(--sp-3))}
.panel.flush .panel-foot{padding:var(--sp-3) calc(var(--sp-2) + var(--sp-3)) 0}
.panel-head{display:flex;justify-content:space-between;align-items:center;gap:var(--sp-3);flex-wrap:wrap;min-height:32px}
.panel-icon{flex:none;display:grid;place-items:center;width:30px;height:30px;border-radius:var(--radius);background:var(--fill);color:var(--muted)}
.panel-title{min-width:0;flex:1 1 240px;align-self:center}
.panel-title h2{font-size:var(--fs-lg)}
.panel-title p{margin:2px 0 0;font-size:var(--fs-sm)}
.panel-actions{display:flex;align-items:center;gap:var(--sp-2);flex-wrap:wrap}
.panel-body{display:grid;grid-template-columns:minmax(0,1fr);gap:var(--sp-4);min-width:0}
.panel-body:empty{display:none}
.panel-foot{display:flex;align-items:center;gap:var(--sp-2);flex-wrap:wrap;padding-top:var(--sp-1)}
</style>
