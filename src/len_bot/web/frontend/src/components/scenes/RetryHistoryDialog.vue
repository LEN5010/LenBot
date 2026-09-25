<script setup>
const props = defineProps({state: {type: Object, required: true}})
const { retryConfirmation, retrySaving, retryError, retryHistory } = props.state
</script>
<template>
  <v-dialog
    :model-value="Boolean(retryConfirmation)"
    :persistent="retrySaving"
    max-width="580"
    @update:model-value="value => { if (!value && !retrySaving) retryConfirmation = null }"
  >
    <v-card>
      <v-card-title class="dialog-title">重试此历史区间</v-card-title>
      <v-card-text v-if="retryConfirmation">
        <p>场景 {{ retryConfirmation.scene }}</p>
        <p>原始范围 {{ retryConfirmation.range }}</p>
        <p v-if="retryConfirmation.error">原失败记录：{{ retryConfirmation.error }}</p>
        <p>通过已有维护入口提交一次重试，失败证据和原文仍可回查。</p>
        <v-alert v-if="retryError" type="error" variant="tonal" class="mt-4">
          {{ retryError }}
        </v-alert>
      </v-card-text>
      <v-card-actions class="dialog-actions">
        <v-btn variant="text" :disabled="retrySaving" @click="retryConfirmation = null">返回</v-btn>
        <v-btn
          color="primary"
          :loading="retrySaving"
          :disabled="retrySaving"
          @click="retryHistory"
        >确认重试</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
<style scoped>
.dialog-title{white-space:normal}
.dialog-actions{padding:16px;flex-wrap:wrap}
</style>
