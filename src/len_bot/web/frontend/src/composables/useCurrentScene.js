import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { host } from '../host/store.js'

// The scene every per-scene page shows. It lives in `?scene=`; without one,
// the scene used last (or the first) is shown. The shell's picker changes it.
const last = ref('')

export function resolveScene(value) {
  const scenes = host.state?.scenes || []
  const valid = name => typeof name === 'string' && scenes.some(item => item.scene === name)
  if (valid(value)) return value
  return valid(last.value) ? last.value : scenes[0]?.scene || ''
}

export function useCurrentScene() {
  const route = useRoute()
  const scene = computed(() => {
    const value = resolveScene(route.query.scene)
    if (value) last.value = value
    return value
  })
  const current = computed(() => host.state?.scenes.find(item => item.scene === scene.value) || null)
  return { scene, current }
}

// Switching scene keeps the page and tab, and drops what was selected in it.
export function sceneTarget(route, scene) {
  const query = { scene }
  if (typeof route.query.tab === 'string') query.tab = route.query.tab
  return { name: route.name, query }
}

export function showsScene(route) {
  const rule = route.meta.scene
  return typeof rule === 'function' ? rule(route) : Boolean(rule)
}
