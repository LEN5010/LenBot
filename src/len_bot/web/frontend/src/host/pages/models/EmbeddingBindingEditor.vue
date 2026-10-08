<script setup>
import { computed, ref, watch } from 'vue'
import { numberOrNull } from '../../forms.js'
import { modelChoices } from '../../providerModels.js'
import ProviderTools from './ProviderTools.vue'
const props = defineProps({ modelValue: { type: Object, default: null }, providers: { type: Array, required: true } })
const emit = defineEmits(['update:modelValue'])
const models = ref([])
const choices = computed(() => props.providers.map(row => ({ title: row.alias, value: row.id })))
const provider = computed(() => props.providers.find(row => row.id === props.modelValue?.provider))
watch(() => props.modelValue?.provider, () => { models.value = [] })
function update(fields) { emit('update:modelValue', { ...props.modelValue, ...fields }) }
function toggle(on) { emit('update:modelValue', on ? { provider: choices.value[0]?.value || '', model: '', dimensions: null } : null) }
</script>
<template>
  <v-switch :model-value="modelValue !== null" label="使用向量模型" @update:model-value="toggle" />
  <template v-if="modelValue">
    <div class="form-grid">
      <v-select :model-value="modelValue.provider" :items="choices" label="向量服务商" no-data-text="没有向量服务商" @update:model-value="value => update({ provider: value })" />
      <v-combobox :model-value="modelValue.model" :items="modelChoices(models)" :return-object="false" label="向量模型名" @update:model-value="value => update({ model: value })" />
      <v-text-field :model-value="modelValue.dimensions ?? ''" type="number" label="向量维数（选填）" placeholder="模型默认值" @update:model-value="value => update({ dimensions: numberOrNull(value) })" />
    </div>
    <ProviderTools v-if="provider" :provider="provider" :binding="modelValue" :can-probe="false" embedding :models="models"
      @models="value => models = value" @choose-model="value => update({ model: value })" />
  </template>
</template>
