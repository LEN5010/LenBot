<script setup>
import { computed, ref, watch } from 'vue'
const props = defineProps({ asset: { type: Object, required: true }, sceneId: String, interactive: Boolean })
const failed = ref(false)
const kind = computed(() => props.asset.mime_type?.startsWith('audio/') ? 'audio'
  : props.asset.mime_type?.startsWith('video/') ? 'video'
    : !props.asset.mime_type || props.asset.mime_type.startsWith('image/') ? 'image' : 'unknown')
const source = computed(() => `/api/media/${encodeURIComponent(props.asset.id)}/file?scene_id=${encodeURIComponent(props.sceneId || props.asset.scope)}`)
watch(() => [source.value, props.asset.mime_type], () => { failed.value = false })
</script>
<template>
  <div class="media-preview" :class="`media-preview--${kind}`">
    <span v-if="failed" class="media-placeholder" role="status">此媒体当前不可读取，未换源或重新生成。</span>
    <img v-else-if="kind==='image'" :src="source" :alt="asset.description || '已登记图片'" loading="lazy" @error="failed=true" />
    <audio v-else-if="kind==='audio' && interactive" :key="source" :src="source" controls preload="none" @error="failed=true">浏览器不支持此音轨，可打开原媒体。</audio>
    <video v-else-if="kind==='video' && interactive" :key="source" :src="source" controls preload="none" playsinline @error="failed=true">浏览器不支持此视频，可打开原媒体。</video>
    <span v-else class="media-placeholder">{{ kind==='audio' ? '音频 · 打开详情后手动播放' : kind==='video' ? '视频 · 打开详情后手动播放' : '此登记类型不提供内嵌预览' }}<small v-if="asset.mime_type">{{ asset.mime_type }}</small></span>
  </div>
</template>
<style scoped>
.media-preview{display:flex;align-items:center;justify-content:center;width:100%;height:100%;min-width:0;min-height:0}.media-preview img,.media-preview video{display:block;width:100%;height:100%;min-height:0;object-fit:contain}.media-preview audio{width:min(100%,560px)}.media-placeholder{padding:12px;text-align:center;color:var(--muted);font-size:12px;line-height:1.7;overflow-wrap:anywhere}.media-placeholder small{display:block;margin-top:5px}
</style>
