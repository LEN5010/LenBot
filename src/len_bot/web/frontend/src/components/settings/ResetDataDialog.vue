<script setup>
const props = defineProps({state: {type: Object, required: true}, page: {type: Object, required: true}})
const { busy, error, currentSaveOutcome } = props.page
const { resetConfirm, resetData } = props.state
</script>
<template>
  <v-dialog v-model="resetConfirm" max-width="620" :persistent="busy==='reset'">
    <v-card title="确认 Reset 全部对话数据">
      <v-card-text>
        <v-alert type="error" variant="tonal" class="mb-4">此操作会清空全部群聊和私聊的会话数据，无法从页面撤销。</v-alert>
        <p>先停止认知、维护、工作和投递，再删除对话、认识、任务、工作、工具资料、调用账、聊天图片和场景上下文。</p>
        <p class="mt-3">保留运行配置、人工表达样例与运营表情库（含来源记录），包括模型、OneBot、人格、登录、Shadow、QQ 白名单、各群设置与能力授予。</p>
        <p class="mt-3">额度预占与执行记录不在清理范围内，执行后仍需单独核对归属；页面的成功结果不表示外部容器已经结束。</p>
        <v-alert v-if="error" type="error" variant="tonal" class="mt-3">{{ error }}</v-alert>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn :disabled="!!currentSaveOutcome||!!busy" @click="resetConfirm=false">取消</v-btn>
        <v-btn
          color="error"
          :loading="busy==='reset'"
          :disabled="!!currentSaveOutcome||!!busy"
          @click="resetData"
        >确认清空对话数据</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
