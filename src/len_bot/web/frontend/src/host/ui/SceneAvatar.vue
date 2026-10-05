<script setup>
// A round badge with the first character of a scene's name, tinted per scene.
import { computed } from 'vue'
import { sceneName } from '../../api.js'
import { avatarTints } from '../../styles/theme.js'
const props = defineProps({ scene: { type: String, required: true }, size: { type: Number, default: 36 } })
const tint = computed(() => avatarTints[[...props.scene].reduce((sum, char) => sum + char.codePointAt(0), 0) % avatarTints.length])
const letter = computed(() => [...sceneName(props.scene).replace(/^(群|私聊)\s+/, '')][0] || '#')
</script>
<template>
  <span class="scene-avatar" :style="{ width: `${size}px`, height: `${size}px`, background: tint[0], color: tint[1], fontSize: `${Math.round(size * 0.42)}px` }"
    aria-hidden="true">{{ letter }}</span>
</template>
<style scoped>
.scene-avatar{display:inline-grid;place-items:center;flex:none;border-radius:32%;font-weight:700;line-height:1}
</style>
