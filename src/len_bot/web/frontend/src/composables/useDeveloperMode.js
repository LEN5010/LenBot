import { computed, ref } from 'vue'
import { useAuth } from './useAuth.js'
const enabled = ref(false)
export const developerMode = enabled
export const developerDetails = computed(() => useAuth().panelContext?.mode !== 'isolated-multi' || enabled.value)
