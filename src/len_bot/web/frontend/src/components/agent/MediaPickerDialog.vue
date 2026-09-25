<script setup>
const props = defineProps({state: {type: Object, required: true}})
const { mediaOpen, mediaRows, mediaTotal, mediaPage, mediaQuery, mediaSearch, mediaLoading,
  mediaError, mediaScope, mediaReadAt, imageUrl, loadMedia, chooseMedia } = props.state
</script>
<template>
  <v-dialog v-model="mediaOpen" max-width="900" scrollable>
    <v-card>
      <v-card-title class="section-header">选择运营素材<v-btn variant="text" @click="mediaOpen=false">关闭</v-btn>
      </v-card-title>
      <v-card-text>
        <p class="muted mb-4">可用范围：{{ mediaScope }}{{ mediaScope!=='global-safe'?' 与公共素材':'' }}
        </p>
        <v-form
          class="media-filter"
          @submit.prevent="mediaSearch=mediaQuery;mediaPage=1;loadMedia()"
        >
          <v-text-field v-model="mediaQuery" label="描述或标签" hide-details clearable />
          <v-btn type="submit" color="primary">查询</v-btn>
        </v-form>
        <v-progress-linear v-if="mediaLoading" indeterminate class="my-3" />
        <v-alert v-if="mediaError" type="error" variant="tonal" class="my-3">
          {{ mediaError }}
        </v-alert>
        <div class="media-picker mt-4">
          <v-card v-for="asset in mediaRows" :key="asset.id" tag="article" variant="outlined">
            <img
              :src="imageUrl(asset.id,asset.scope)"
              :alt="asset.description||'运营素材'"
              loading="lazy"
            />
            <div class="pa-3">
              <p class="clamp-2 mb-3">{{ asset.description||asset.id }}</p>
              <v-btn size="small" color="primary" variant="tonal" @click="chooseMedia(asset)">选用这张</v-btn>
            </div>
          </v-card>
        </div>
        <p v-if="mediaReadAt&&!mediaLoading&&!mediaError&&!mediaRows.length" class="muted py-6">没有匹配的已启用运营素材</p>
        <v-pagination
          v-if="mediaTotal>48"
          :model-value="mediaPage"
          :length="Math.ceil(mediaTotal/48)"
          :total-visible="5"
          @update:model-value="value=>{mediaPage=value;loadMedia()}"
        />
      </v-card-text>
    </v-card>
  </v-dialog>
</template>
<style scoped>
.section-header{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}
.media-filter{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;align-items:center}
.media-picker{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px}
.media-picker img{width:100%;height:150px;object-fit:contain;background:var(--page)}
.media-picker p{overflow-wrap:anywhere;min-height:3em}
@media(max-width:650px){
  .section-header{align-items:flex-start}
  .media-picker{grid-template-columns:repeat(2,minmax(0,1fr))}
}
</style>
