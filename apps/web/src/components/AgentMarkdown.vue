<script setup lang="ts">
import { computed } from "vue";

import { renderAgentMarkdown } from "@/utils/agent-markdown";

const props = defineProps<{
  content: string;
}>();

const renderedContent = computed(() => renderAgentMarkdown(props.content));
</script>

<template>
  <!-- renderAgentMarkdown disables raw HTML and unsafe links before v-html. -->
  <div class="agent-markdown" v-html="renderedContent"></div>
</template>

<style scoped>
.agent-markdown {
  min-width: 0;
  overflow-wrap: anywhere;
  word-break: break-word;
  line-height: 1.7;
}

.agent-markdown :deep(p) {
  margin: 0 0 0.8rem;
}

.agent-markdown :deep(p:last-child) {
  margin-bottom: 0;
}

.agent-markdown :deep(h1),
.agent-markdown :deep(h2),
.agent-markdown :deep(h3),
.agent-markdown :deep(h4) {
  margin: 1.1rem 0 0.55rem;
  color: #183f59;
  line-height: 1.35;
}

.agent-markdown :deep(h1) { font-size: 1.28rem; }
.agent-markdown :deep(h2) { font-size: 1.16rem; }
.agent-markdown :deep(h3) { font-size: 1.06rem; }
.agent-markdown :deep(h4) { font-size: 1rem; }
.agent-markdown :deep(h1:first-child),
.agent-markdown :deep(h2:first-child),
.agent-markdown :deep(h3:first-child),
.agent-markdown :deep(h4:first-child) { margin-top: 0; }

.agent-markdown :deep(ul),
.agent-markdown :deep(ol) {
  margin: 0 0 0.8rem;
  padding-left: 1.4rem;
}

.agent-markdown :deep(li + li) { margin-top: 0.3rem; }
.agent-markdown :deep(code) {
  padding: 0.12em 0.35em;
  border-radius: 4px;
  color: #245273;
  background: #eaf3f8;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.9em;
}
.agent-markdown :deep(pre) {
  margin: 0 0 0.8rem;
  padding: 0.8rem;
  overflow-x: auto;
  border-radius: 8px;
  color: #eaf4fa;
  background: #183f59;
}
.agent-markdown :deep(pre code) { padding: 0; color: inherit; background: transparent; }
.agent-markdown :deep(blockquote) {
  margin: 0 0 0.8rem;
  padding: 0.15rem 0 0.15rem 0.8rem;
  border-left: 3px solid #8cc5ea;
  color: #577386;
}
.agent-markdown :deep(hr) { margin: 1rem 0; border: 0; border-top: 1px solid #d8e6ee; }
.agent-markdown :deep(a) { color: #176fc8; text-decoration: underline; }

.agent-markdown :deep(.agent-markdown-table-wrap) { overflow-x: auto; }
.agent-markdown :deep(table) {
  width: 100%;
  min-width: 420px;
  border-collapse: collapse;
  margin: 0 0 0.8rem;
  font-size: 0.9rem;
}
.agent-markdown :deep(th), .agent-markdown :deep(td) {
  padding: 0.55rem 0.65rem;
  border: 1px solid #d8e6ee;
  text-align: left;
  vertical-align: top;
  overflow-wrap: anywhere;
}
.agent-markdown :deep(th) { color: #315b75; background: #edf6fb; font-weight: 700; }
</style>
