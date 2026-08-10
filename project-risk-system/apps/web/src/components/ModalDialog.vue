<script setup lang="ts">
defineProps<{
  title: string;
  eyebrow?: string;
}>();

defineEmits<{
  close: [];
}>();
</script>

<template>
  <div class="modal-backdrop" role="presentation" @click.self="$emit('close')">
    <section class="app-modal" role="dialog" aria-modal="true">
      <header class="app-modal-header">
        <div>
          <p v-if="eyebrow">{{ eyebrow }}</p>
          <h2>{{ title }}</h2>
        </div>
        <button type="button" aria-label="关闭" @click="$emit('close')">×</button>
      </header>
      <div class="app-modal-body">
        <slot />
      </div>
      <footer v-if="$slots.footer" class="app-modal-footer">
        <slot name="footer" />
      </footer>
    </section>
  </div>
</template>
