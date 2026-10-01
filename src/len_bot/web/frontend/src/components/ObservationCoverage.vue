<script setup>
import EntityLink from './EntityLink.vue'
defineProps({ details: Object, sceneId: String })
</script>
<template>
  <section v-if="details" class="coverage-details" aria-label="浏览器与媒体实际取得范围">
    <v-alert v-if="details.error" type="warning" variant="tonal">{{ details.error }}</v-alert>
    <template v-else-if="details.kind==='browser_dom'">
      <h4>网页正文</h4>
      <p>页面 {{ details.page_ref }} · DOM 版本 {{ details.snapshot_revision }}</p>
      <p>本次 DOM 视图从第 {{ details.text_offset }} 字符开始，含 {{ details.text_page_chars }} 字符；已收集正文共 {{ details.text_total_chars }} 字符。</p>
      <p>
        {{ details.collected_text_saved ? '此资料已保存完整的受限采集正文' : '此资料只保存了当次 DOM 正文页' }}（{{ details.saved_text_chars }} 字符）。这些 DOM 坐标不是资料 JSON 的字符坐标。</p>
      <p v-if="details.collection_truncated" class="coverage-issue">采集已截断；上限 {{ details.collection_limit_chars }} 字符，当前记录不能代表网页全文。</p>
      <p v-if="!details.collected_text_saved && details.text_next_offset!==null">源端下一页 DOM 偏移 {{ details.text_next_offset }}。</p>
      <p class="muted-copy">保存的网页文字可以随时看，网页本身可能已经打不开了。</p>
    </template>
    <template v-else-if="details.kind==='browser_pixels'">
      <h4>已取得的网页图片资产</h4>
      <p>页面 {{ details.page_ref }} · {{ details.area==='full_page' ? '整页区域' : '视口区域' }} · 页面版本 {{ details.requested_snapshot_revision ?? '未指定或旧记录未保存' }}
      </p>
      <EntityLink type="media" :id="details.asset_id" :scene-id="sceneId" label="打开已保存的图片资产" />
      <p class="muted-copy">登记截图只说明工具取得了图片。模型是否实际收到该图，要看对应请求的图片装配清单；不是模型必然已看过，也不是网页后续状态。</p>
    </template>
    <template v-else-if="details.kind==='video_segment'">
      <h4>指定视频片段</h4>
      <p>{{ details.bvid }} · cid {{ details.cid }} · 来源时长 {{ details.source_duration_ms }} ms</p>
      <p>请求 [{{ details.requested_start_ms }}, {{ details.requested_end_ms }}) ms · 执行 {{ details.execution_id }}
      </p>
      <EntityLink
        type="job"
        :id="details.job_id"
        :scene-id="sceneId"
        :label="`查看来源工作（此资料属于 v${details.job_revision}）`"
      />
      <ul v-if="details.frames.length">
        <li v-for="frame in details.frames" :key="frame.asset_id">
          <EntityLink
            type="media"
            :id="frame.asset_id"
            :scene-id="sceneId"
            :label="`查看 ${frame.source_time_ms} ms 处的画面`"
          />
          <span>
            {{ frame.timestamp_basis==='decoder_pts_after_accurate_seek' ? '依据：解码器精确定位后的时间戳' : frame.timestamp_basis }}
          </span>
        </li>
      </ul>
      <p v-else>没有截取画面。</p>
      <p v-if="details.audio_asset_id">已取得音轨区间 [{{ details.audio_start_ms }}, {{ details.audio_end_ms }}) ms：<EntityLink type="media" :id="details.audio_asset_id" :scene-id="sceneId" label="打开音轨资料" />
      </p>
      <p v-else>没有已回收的音轨；不能从标题推测声音。</p>
      <p class="muted-copy">Bot 看过的是这些画面和音轨。</p>
    </template>
    <template v-else-if="details.kind==='audio_transcript'">
      <h4>ASR 派生转写</h4>
      <p>来源音轨 [{{ details.coverage_start_ms }}, {{ details.coverage_end_ms }}) ms · {{ details.segment_count }} 个返回分段 · 执行 {{ details.execution_id }}
      </p>
      <EntityLink type="media" :id="details.audio_asset_id" :scene-id="sceneId" label="回到原音轨" />
      <p>{{ details.qualification }}</p>
      <p class="muted-copy">识别出的文字在保存的资料里。</p>
    </template>
  </section>
</template>
<style scoped>
.coverage-details{display:grid;gap:8px;margin:8px 0;padding:14px;border:1px solid var(--line);border-radius:8px;min-width:0;font-size:13px;line-height:1.7}
.coverage-details h4,.coverage-details p{margin:0;overflow-wrap:anywhere}
.coverage-details ul{margin:0;padding-left:20px}
.coverage-details li{margin:7px 0;overflow-wrap:anywhere}
.coverage-details li>span{display:block;font-size:12px;color:var(--muted)}
.muted-copy{color:var(--muted)}
.coverage-issue{color:rgb(var(--v-theme-warning))}
</style>
