<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";

import type { AgentConversationListItem } from "@/api/agent";

/**
 * Collapsible Agent conversation-history sidebar (PART A of the history UX).
 *
 * Owns only presentation + the collapse UI preference (localStorage — never
 * conversation content): the conversation data, pagination and delete
 * confirm flow stay in the parent so the existing composable/API wiring is
 * not duplicated. Rendered as a static column ≥900px and as an overlay
 * drawer below that (opened from the Agent header, closed by backdrop, Esc
 * or selecting a conversation).
 */

/** localStorage key for the sidebar collapse UI preference (width only). */
const COLLAPSED_KEY = "risk-system.agent.history-sidebar-collapsed";

const props = defineProps<{
  conversations: AgentConversationListItem[];
  currentConversationId: string | null;
  /** History switching is locked while a live turn owns the conversation. */
  disabled: boolean;
  loading: boolean;
  hasMore: boolean;
  loadingMore: boolean;
  /** The conversation whose DELETE request is in flight. */
  deletingId: string | null;
  /** Overlay-drawer open state on narrow screens (<900px). */
  mobileOpen: boolean;
}>();

const emit = defineEmits<{
  "new-conversation": [];
  select: [conversationId: string];
  delete: [conversationId: string];
  "load-more": [];
  "update:mobileOpen": [open: boolean];
}>();

const collapsed = ref(readStoredCollapsed());
/** Which row's "⋯" action menu is open (only one at a time). */
const openMenuId = ref<string | null>(null);

/**
 * Effective collapse for rendering. On narrow screens an open overlay drawer
 * always shows the full list — a persisted desktop collapse must not produce
 * a buttons-only drawer.
 */
const isCollapsed = computed(() => collapsed.value && !props.mobileOpen);

function readStoredCollapsed(): boolean {
  try {
    return globalThis.localStorage.getItem(COLLAPSED_KEY) === "1";
  } catch {
    return false;
  }
}

function toggleCollapsed(): void {
  collapsed.value = !collapsed.value;
  try {
    globalThis.localStorage.setItem(COLLAPSED_KEY, collapsed.value ? "1" : "0");
  } catch {
    /* storage unavailable (private mode / disabled) — preference is best-effort */
  }
}

function toggleMenu(conversationId: string): void {
  openMenuId.value = openMenuId.value === conversationId ? null : conversationId;
}

function closeMenu(): void {
  openMenuId.value = null;
}

function requestDelete(conversationId: string): void {
  closeMenu();
  emit("delete", conversationId);
}

function selectConversation(conversationId: string): void {
  if (props.disabled) return;
  emit("select", conversationId);
  emit("update:mobileOpen", false);
}

function closeMobile(): void {
  emit("update:mobileOpen", false);
}

/** Delete is offered for every row; a live turn is rejected by the backend 409. */
function deleteDisabled(item: AgentConversationListItem): boolean {
  return (
    props.deletingId !== null ||
    (props.disabled && item.id === props.currentConversationId)
  );
}

/** 今天 / 昨天 / 更早 groupings over the loaded page (newest activity first). */
const groups = computed(() => {
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const startOfYesterday = new Date(startOfToday.getTime() - 86_400_000);
  const buckets: Array<{ key: string; label: string; items: AgentConversationListItem[] }> = [
    { key: "today", label: "今天", items: [] },
    { key: "yesterday", label: "昨天", items: [] },
    { key: "earlier", label: "更早", items: [] },
  ];
  for (const item of props.conversations) {
    const updated = new Date(item.updatedAt);
    if (updated >= startOfToday) buckets[0]!.items.push(item);
    else if (updated >= startOfYesterday) buckets[1]!.items.push(item);
    else buckets[2]!.items.push(item);
  }
  return buckets.filter((bucket) => bucket.items.length > 0);
});

function formatTime(value: string): string {
  const date = new Date(value);
  const now = new Date();
  const sameDay =
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate();
  return sameDay
    ? date.toLocaleTimeString("zh-CN", { hour12: false, hour: "2-digit", minute: "2-digit" })
    : date.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}

/** Esc closes the overlay drawer (and the open row menu) on narrow screens. */
function onKeydown(event: KeyboardEvent): void {
  if (event.key !== "Escape") return;
  if (openMenuId.value !== null) {
    closeMenu();
    return;
  }
  if (props.mobileOpen) closeMobile();
}

onMounted(() => document.addEventListener("keydown", onKeydown));
onBeforeUnmount(() => document.removeEventListener("keydown", onKeydown));
</script>

<template>
  <div
    v-if="mobileOpen"
    class="agent-history-scrim"
    aria-hidden="true"
    @click="closeMobile"
  ></div>
  <nav
    class="agent-history-sidebar"
    :class="{ 'is-collapsed': isCollapsed, 'is-mobile-open': mobileOpen }"
    aria-label="历史会话"
  >
    <header class="agent-history-toolbar">
      <button
        class="agent-history-icon-button"
        type="button"
        :aria-label="collapsed ? '展开历史会话' : '收起历史会话'"
        :title="collapsed ? '展开历史会话' : '收起历史会话'"
        @click="toggleCollapsed"
      >
        {{ collapsed ? "»" : "«" }}
      </button>
      <strong v-if="!isCollapsed" class="agent-history-heading">历史会话</strong>
      <button
        class="agent-history-icon-button agent-history-new"
        type="button"
        aria-label="新建会话"
        title="新建会话"
        @click="emit('new-conversation')"
      >
        ＋
      </button>
    </header>

    <div v-if="!isCollapsed" class="agent-history-scroll" @focusout="closeMenu">
      <p v-if="loading && conversations.length === 0" class="agent-history-note">
        正在加载历史会话…
      </p>
      <template v-for="group in groups" :key="group.key">
        <p class="agent-history-group">{{ group.label }}</p>
        <ul>
          <li
            v-for="item in group.items"
            :key="item.id"
            :class="{ 'is-current': item.id === currentConversationId }"
          >
            <button
              type="button"
              class="agent-history-item"
              :disabled="disabled"
              :aria-disabled="disabled"
              :aria-current="item.id === currentConversationId ? 'true' : undefined"
              :title="disabled ? '当前有进行中的执行，请先完成或取消' : item.title"
              @click="selectConversation(item.id)"
            >
              <span class="agent-history-title">{{ item.title }}</span>
              <small v-if="item.activeProjectName" class="agent-history-project">
                {{ item.activeProjectName }}
              </small>
              <span class="agent-history-time">{{ formatTime(item.updatedAt) }}</span>
            </button>
            <div class="agent-history-row-actions">
              <button
                type="button"
                class="agent-history-icon-button agent-history-menu-button"
                :aria-label="`会话操作：${item.title}`"
                :aria-expanded="openMenuId === item.id"
                title="会话操作"
                @click.stop="toggleMenu(item.id)"
              >
                ⋯
              </button>
              <div v-if="openMenuId === item.id" class="agent-history-menu" role="menu">
                <button
                  type="button"
                  role="menuitem"
                  :disabled="deleteDisabled(item)"
                  :title="
                    deleteDisabled(item) ? '当前有进行中的执行，请先停止后再删除' : undefined
                  "
                  @click="requestDelete(item.id)"
                >
                  删除会话
                </button>
              </div>
            </div>
          </li>
        </ul>
      </template>
      <p
        v-if="!loading && conversations.length === 0"
        class="agent-history-note"
      >
        暂无历史会话。
      </p>
      <button
        v-if="hasMore"
        class="agent-history-more"
        type="button"
        :disabled="loadingMore"
        @click="emit('load-more')"
      >
        {{ loadingMore ? "加载中…" : "加载更多" }}
      </button>
    </div>
  </nav>
</template>

<style scoped>
.agent-history-scrim{position:absolute;z-index:2;inset:0;border:0;background:rgba(16,45,66,.32);backdrop-filter:blur(2px)}
.agent-history-sidebar{display:flex;width:264px;min-width:0;min-height:0;flex-direction:column;border-right:1px solid #dfeaf1;background:#f9fcfd;transition:width .18s ease}
.agent-history-sidebar.is-collapsed{width:52px;border-right:1px solid #eaf2f7}
.agent-history-toolbar{display:flex;align-items:center;gap:8px;padding:12px 10px;border-bottom:1px solid #e6eff5}
.agent-history-sidebar.is-collapsed .agent-history-toolbar{flex-direction:column;gap:10px}
.agent-history-heading{flex:1;color:#5c778b;font-size:13px;font-weight:800;letter-spacing:.06em}
.agent-history-icon-button{display:grid;width:32px;height:32px;padding:0;border:0;border-radius:9px;place-items:center;color:#5c778b;background:#eff5f8;font-size:15px;cursor:pointer}
.agent-history-icon-button:hover:not(:disabled){background:#e2eef5}
.agent-history-icon-button:focus-visible{outline:2px solid #176fc8;outline-offset:2px}
.agent-history-new{color:#1a7fb8;font-weight:700}
.agent-history-scroll{display:grid;min-height:0;flex:1;align-content:start;gap:4px;padding:12px 10px;overflow-y:auto;overscroll-behavior:contain}
.agent-history-group{margin:8px 4px 2px;color:#8aa0af;font-size:11px;font-weight:800;letter-spacing:.08em}
.agent-history-scroll ul{display:grid;margin:0;padding:0;gap:3px;list-style:none}
.agent-history-scroll li{position:relative;display:flex;align-items:center}
.agent-history-item{display:grid;min-width:0;flex:1;padding:8px 30px 8px 10px;border:1px solid transparent;border-radius:9px;grid-template-columns:minmax(0,1fr) auto;grid-template-areas:"title time" "project time";align-items:center;gap:2px 8px;color:#28628c;background:transparent;text-align:left;cursor:pointer}
.agent-history-item:hover:not(:disabled){border-color:#cfe3f0;background:#f0f8fd}
.agent-history-item:disabled{cursor:not-allowed;opacity:.55}
.agent-history-item:focus-visible{outline:2px solid #176fc8;outline-offset:2px}
.agent-history-scroll li.is-current .agent-history-item{border-color:#b9d8ec;background:#e9f4fc}
.agent-history-title{grid-area:title;min-width:0;font-size:13px;font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.agent-history-project{grid-area:project;min-width:0;color:#8aa0af;font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.agent-history-time{grid-area:time;color:#8aa0af;font-size:11px;white-space:nowrap}
.agent-history-row-actions{position:absolute;top:50%;right:4px;transform:translateY(-50%)}
.agent-history-menu-button{width:26px;height:26px;border-radius:7px;font-size:16px}
.agent-history-menu{position:absolute;z-index:3;top:calc(100% + 4px);right:0;min-width:120px;padding:5px;border:1px solid #d7e4ec;border-radius:10px;background:#fff;box-shadow:0 14px 32px rgba(26,72,105,.18)}
.agent-history-menu button{display:block;width:100%;padding:8px 10px;border:0;border-radius:7px;color:#b23d45;background:transparent;font-size:13px;font-weight:700;text-align:left;cursor:pointer}
.agent-history-menu button:hover:not(:disabled){background:#fff0f1}
.agent-history-menu button:disabled{cursor:not-allowed;opacity:.55}
.agent-history-note{margin:8px 4px;color:#8aa0af;font-size:12px}
.agent-history-more{margin:6px 4px;padding:9px 10px;border:1px solid #d4e3ec;border-radius:9px;color:#226aa0;background:#fff;font-size:13px;font-weight:700;cursor:pointer}
.agent-history-more:hover:not(:disabled){background:#f4faff}
.agent-history-more:disabled{cursor:wait;opacity:.6}
@media(max-width:899px){
  .agent-history-sidebar{position:absolute;z-index:2;top:0;bottom:0;left:0;width:min(300px,84%);transform:translateX(-100%);transition:transform .2s ease;box-shadow:14px 0 40px rgba(18,57,84,.18)}
  .agent-history-sidebar.is-mobile-open{transform:translateX(0)}
  .agent-history-sidebar.is-collapsed{width:min(300px,84%)}
}
</style>
