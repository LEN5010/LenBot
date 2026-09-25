<script setup>
const props = defineProps({state: {type: Object, required: true}, page: {type: Object, required: true}})
const { busy, loading, currentSaveOutcome, conflicts, saveOutcomes } = props.page
const { timeDraft, timeConfigured, timeRestart, weekdays, timeDirty, beginTimeConfiguration,
  saveTime } = props.state
</script>
<template>
  <v-card class="pa-5 form-card">
    <h2>业务时间口径</h2>
    <p class="muted my-3">日程与群总结按照这里填写的时区、自然周和下午范围解释日期，不自动选择时区或补全天段。睡眠窗口成对填写；留空表示不启用睡眠。</p>
    <v-alert v-if="!timeConfigured" type="info" variant="tonal" class="mb-4">尚未保存业务时间；需要时间口径的新插件不能启用。</v-alert>
    <v-alert v-if="timeRestart" type="info" variant="tonal" class="mb-4">系统另有已保存配置等待手动重启。本页读取业务时间的保存值，当前查询与采集使用的时间口径仍须按对应组件核对。</v-alert>
    <v-btn
      v-if="!timeDraft&&!loading"
      variant="tonal"
      color="primary"
      :disabled="!!busy||!!saveOutcomes.time"
      @click="beginTimeConfiguration"
    >填写业务时间</v-btn>
    <v-form
      v-if="timeDraft"
      :disabled="!!currentSaveOutcome||!!busy"
      class="form-grid"
      @submit.prevent="saveTime"
    >
      <v-text-field
        v-model="timeDraft.timezone"
        label="IANA 时区"
        hint="填写业务实际采用的 IANA 时区名称。"
        persistent-hint
        required
      />
      <v-select v-model="timeDraft.week_start" label="自然周第一天" :items="weekdays" required />
      <v-text-field v-model="timeDraft.afternoon_start" label="下午开始" type="time" required />
      <v-text-field v-model="timeDraft.afternoon_end" label="下午结束（不含）" type="time" required />
      <v-text-field
        v-model="timeDraft.sleep_start"
        label="睡眠开始（可选）"
        type="time"
        clearable
        hint="与睡眠结束成对；跨日窗口允许开始晚于结束。"
        persistent-hint
      />
      <v-text-field
        v-model="timeDraft.sleep_end"
        label="睡眠结束（可选）"
        type="time"
        clearable
        hint="到点后各群按叫醒状态决定是否恢复普通发送。"
        persistent-hint
      />
      <v-btn
        type="submit"
        color="primary"
        :loading="busy==='time'"
        :disabled="!!currentSaveOutcome||!!busy||!!conflicts.entries.time||!timeDirty"
      >保存业务时间</v-btn>
    </v-form>
  </v-card>
</template>
<style scoped>
.form-card{max-width:1000px;width:100%}
.section-header h2,.form-card>h2{font-size:20px}
.form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;align-items:start}
.form-grid>.v-btn{justify-self:start}
.settings-view p{line-height:1.7}
@media(max-width:650px){
  .form-grid{grid-template-columns:minmax(0,1fr)}
}
</style>
