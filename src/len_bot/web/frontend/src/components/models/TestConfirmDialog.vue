<script setup>
const props = defineProps({state: {type: Object, required: true}, page: {type: Object, required: true}})
const { busy, error, writeHeld } = props.page
const { testConfirm, testRoute } = props.state
</script>
<template>
  <v-dialog
    :model-value="!!testConfirm"
    max-width="560"
    :persistent="busy==='test'"
    @update:model-value="value=>!value&&(testConfirm=null)"
  >
    <v-card v-if="testConfirm" title="主动能力检查">
      <v-card-text>
        <p>检查 {{ testConfirm.name }}：{{ testConfirm.profile.provider_id }} / {{ testConfirm.profile.model }}。</p>
        <p class="mt-3">将进行最多 2 次真实模型请求，检查原图、指定工具与原生续接，可能产生供应商费用。检查使用合成资料，不向群聊发送消息。</p>
        <v-alert v-if="error" type="error" variant="tonal" class="mt-3">{{ error }}</v-alert>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn :disabled="busy==='test'" @click="testConfirm=null">取消</v-btn>
        <v-btn color="primary" :loading="busy==='test'" :disabled="writeHeld" @click="testRoute">确认发起检查</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
