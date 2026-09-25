<script setup>
const props = defineProps({state: {type: Object, required: true}, page: {type: Object, required: true}})
const { busy, currentSaveOutcome } = props.page
const { shadow, toggleShadow } = props.state
</script>
<template>
  <v-card class="pa-5 form-card">
    <h2>全局发送控制</h2>
    <div class="delivery-state my-4">
      <v-chip :color="shadow.enabled?'warning':'primary'">
        {{ shadow.enabled?'Shadow · 不实际发送':'按各群规则发送' }}
      </v-chip>
      <v-btn
        :color="shadow.enabled?'warning':'primary'"
        variant="outlined"
        :loading="busy==='shadow'"
        :disabled="!!currentSaveOutcome||!!busy"
        @click="toggleShadow"
      >
        {{ shadow.enabled?'关闭 Shadow':'开启 Shadow' }}
      </v-btn>
    </div>
    <p class="muted mb-5">Shadow 只控制是否实际发送。群启用、普通聊天、命令、公告、主播订阅和 @全体统一在“本群设置”中保存。</p>
    <v-btn variant="tonal" color="primary" :to="{name:'scenes'}">管理各群设置</v-btn>
  </v-card>
</template>
<style scoped>
.form-card{max-width:1000px;width:100%}
.section-header h2,.form-card>h2{font-size:20px}
.actions,.meta,.delivery-state,.saved-scenes{display:flex;gap:8px 12px;flex-wrap:wrap;align-items:center}
.settings-view p{line-height:1.7}
</style>
