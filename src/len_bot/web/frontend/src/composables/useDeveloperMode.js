import { computed, ref } from 'vue'
import { useAuth } from './useAuth.js'
// A browser-only display preference, never a Bot runtime/config override.
const enabled = ref(false)
export const developerMode = enabled
export const developerDetails = computed(() => useAuth().panelContext?.mode !== 'isolated-multi' || enabled.value)
