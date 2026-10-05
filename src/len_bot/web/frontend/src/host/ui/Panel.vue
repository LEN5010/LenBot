<script setup>
// A titled block on a page. `flush` removes the inner padding so a list can
// run edge to edge; `tag="form"` makes the whole block a form; `icon` puts a
// small pink badge before the title.
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
.panel{min-width:0;border:1px solid var(--line);border-radius:var(--radius-lg);background:var(--surface);padding:var(--sp-5);display:grid;gap:var(--sp-4);align-content:start;box-shadow:var(--shadow-card)}
.panel.flush{padding:0;gap:0}
.panel.flush .panel-head{padding:var(--sp-4) var(--sp-5) var(--sp-3)}
.panel.flush .panel-foot{padding:var(--sp-3) var(--sp-5)}
.panel-head{display:flex;justify-content:space-between;align-items:flex-start;gap:var(--sp-3);flex-wrap:wrap}
.panel-icon{flex:none;display:grid;place-items:center;width:32px;height:32px;border-radius:var(--radius-sm);background:var(--brand-soft);color:var(--primary)}
.panel-title{min-width:0;flex:1 1 240px;align-self:center}
.panel-title p{margin:var(--sp-1) 0 0;font-size:var(--fs-sm)}
.panel-actions{display:flex;align-items:center;gap:var(--sp-2);flex-wrap:wrap}
.panel-body{display:grid;gap:var(--sp-4);min-width:0}
.panel-body:empty{display:none}
.panel-foot{display:flex;align-items:center;gap:var(--sp-2);flex-wrap:wrap;border-top:1px solid var(--line);padding-top:var(--sp-4)}
@media(max-width:600px){.panel{padding:var(--sp-4)}}
</style>
