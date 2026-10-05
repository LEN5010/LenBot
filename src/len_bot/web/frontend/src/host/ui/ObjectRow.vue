<script setup>
// One object in a list: title, subtitle, a status or meta slot and actions.
// With `to` the row is a link, with `clickable` a button; actions stay outside
// the clickable area.
defineProps({
  title: { type: String, default: '' },
  subtitle: { type: String, default: '' },
  to: { type: [Object, String], default: null },
  clickable: Boolean,
  active: Boolean,
  danger: Boolean,
})
defineEmits(['click'])
</script>
<template>
  <li class="object-row" :class="{ active, danger, interactive: to || clickable }">
    <component :is="to ? 'RouterLink' : clickable ? 'button' : 'div'" class="row-main" :to="to || undefined"
      :type="clickable && !to ? 'button' : undefined" :aria-current="active ? 'true' : undefined" @click="clickable && $emit('click')">
      <span v-if="$slots.prepend" class="row-prepend"><slot name="prepend" /></span>
      <span class="row-text">
        <span class="row-title"><slot name="title">{{ title }}</slot></span>
        <span v-if="subtitle || $slots.subtitle" class="row-subtitle"><slot name="subtitle">{{ subtitle }}</slot></span>
        <slot />
      </span>
      <span v-if="$slots.meta" class="row-meta"><slot name="meta" /></span>
    </component>
    <span v-if="$slots.actions" class="row-actions"><slot name="actions" /></span>
  </li>
</template>
<style scoped>
.object-row{display:flex;align-items:center;gap:var(--sp-2);min-width:0;border-radius:var(--radius);position:relative}
.row-main{flex:1;min-width:0;display:flex;align-items:center;gap:var(--sp-3);padding:var(--sp-2) var(--sp-3);border:0;background:none;font:inherit;color:inherit;text-align:left;border-radius:var(--radius)}
.interactive .row-main{cursor:pointer}
.object-row{transition:background-color var(--dur-1)}
.interactive:hover{background:var(--hover)}
.row-main:hover{text-decoration:none}
.active,.active:hover{background:var(--selected)}
.active::before{content:'';position:absolute;left:0;top:10px;bottom:10px;width:3px;border-radius:3px;background:var(--brand);animation:rise var(--dur-2) var(--ease-out)}
.active .row-title{color:var(--primary)}
.row-prepend{flex:none;display:inline-flex}
.row-text{flex:1;min-width:0;display:grid;gap:2px}
.row-title{font-weight:600;overflow-wrap:anywhere}
.danger .row-title{color:var(--error)}
.row-subtitle{font-size:var(--fs-sm);color:var(--muted);overflow-wrap:anywhere}
.row-meta{flex:none;display:inline-flex;align-items:center;gap:var(--sp-2);font-size:var(--fs-sm);color:var(--muted)}
.row-actions{flex:none;display:inline-flex;align-items:center;gap:var(--sp-1);padding-right:var(--sp-2)}
</style>
