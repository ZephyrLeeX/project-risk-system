<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import {
  agentScopeRulesApi,
  type ScopeRule,
  type ScopeRuleTestResponse,
} from "@/api/agent-scope-rules";
import ModalDialog from "@/components/ModalDialog.vue";

/**
 * Agent 范围规则（Layer-1 runtime rules）管理面板.
 *
 * Scope rules are deliberately NOT part of the SystemConfig snapshot: every
 * mutation goes through the dedicated CRUD API and takes effect immediately
 * (per-process cache + Redis notification, worst-case ~15s TTL propagation),
 * so this panel never marks the config draft dirty nor enters the publish
 * flow. The backend remains the authority — a caller without
 * `agent.scope.manage` receives 403 regardless of whether this component is
 * rendered.
 */

type DecisionFilter = "ALL" | "ALLOW" | "BLOCK";
type EnabledFilter = "ALL" | "ENABLED" | "DISABLED";

interface RuleForm {
  name: string;
  decision: "ALLOW" | "BLOCK";
  matchType: "EXACT" | "PHRASE";
  pattern: string;
  priority: number;
  enabled: boolean;
  description: string;
}

const rules = ref<ScopeRule[]>([]);
const loading = ref(true);
const error = ref("");
const toast = ref("");

const search = ref("");
const decisionFilter = ref<DecisionFilter>("ALL");
const enabledFilter = ref<EnabledFilter>("ALL");

const editorOpen = ref(false);
const editingRuleId = ref<string | null>(null);
/** Optimistic-lock token of the rule being edited (null = create). */
const editingVersion = ref<number | null>(null);
const form = ref<RuleForm>(emptyForm());
const saving = ref(false);
const editorError = ref("");
const editorPreview = ref<ScopeRuleTestResponse | null>(null);
const previewing = ref(false);

const deleteTarget = ref<ScopeRule | null>(null);
const deleting = ref(false);
const deleteError = ref("");

const testMessage = ref("");
const testing = ref(false);
const testError = ref("");
const testResult = ref<ScopeRuleTestResponse | null>(null);

/** The rule whose enable/disable PATCH is in flight (row button disabled). */
const togglingId = ref<string | null>(null);

function emptyForm(): RuleForm {
  // New rules start disabled: verify with the test interface before enabling,
  // so a mistaken live rule cannot immediately mis-block production traffic.
  return { name: "", decision: "BLOCK", matchType: "PHRASE", pattern: "", priority: 0, enabled: false, description: "" };
}

const decisionLabel = computed(
  () => ({ ALLOW: "允许（ALLOW）", BLOCK: "拦截（BLOCK）" }) as const,
);
const matchLabel = computed(
  () => ({ EXACT: "精确匹配（EXACT）", PHRASE: "短语匹配（PHRASE）" }) as const,
);
const sourceLabel = computed(
  () =>
    ({ BUILTIN: "内置基线", RUNTIME_RULE: "运行时规则", DEFAULT: "默认策略" }) as const,
);

const filteredRules = computed(() => {
  const keyword = search.value.trim().toLowerCase();
  return rules.value.filter((rule) => {
    if (decisionFilter.value !== "ALL" && rule.decision !== decisionFilter.value) return false;
    if (enabledFilter.value === "ENABLED" && !rule.enabled) return false;
    if (enabledFilter.value === "DISABLED" && rule.enabled) return false;
    return !keyword || `${rule.name}${rule.pattern}`.toLowerCase().includes(keyword);
  });
});

const enabledCount = computed(() => rules.value.filter((rule) => rule.enabled).length);

function showToast(message: string): void {
  toast.value = message;
  window.setTimeout(() => {
    if (toast.value === message) toast.value = "";
  }, 3600);
}

function formatTime(value: string): string {
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

async function load(): Promise<void> {
  loading.value = true;
  error.value = "";
  try {
    rules.value = await agentScopeRulesApi.list();
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "范围规则加载失败";
  } finally {
    loading.value = false;
  }
}

function openCreate(): void {
  editingRuleId.value = null;
  editingVersion.value = null;
  form.value = emptyForm();
  editorError.value = "";
  editorPreview.value = null;
  editorOpen.value = true;
}

function openEdit(rule: ScopeRule): void {
  editingRuleId.value = rule.id;
  editingVersion.value = rule.version;
  form.value = {
    name: rule.name,
    decision: rule.decision,
    matchType: rule.matchType,
    pattern: rule.pattern,
    priority: rule.priority,
    enabled: rule.enabled,
    description: rule.description ?? "",
  };
  editorError.value = "";
  editorPreview.value = null;
  editorOpen.value = true;
}

function closeEditor(): void {
  if (saving.value || previewing.value) return;
  editorOpen.value = false;
}

function validateForm(): string {
  if (form.value.name.trim().length < 2 || form.value.name.trim().length > 100) {
    return "规则名称长度需为 2-100 个字符";
  }
  if (!form.value.pattern.trim()) return "匹配模式不能为空";
  if (form.value.pattern.trim().length > 200) return "匹配模式最长 200 个字符";
  if (!Number.isInteger(form.value.priority) || form.value.priority < 0 || form.value.priority > 1000) {
    return "优先级需为 0-1000 的整数";
  }
  return "";
}

/** 409 means another admin changed the rule — surface it and refresh the list. */
function isConflict(reason: unknown): boolean {
  return reason instanceof Error && (reason as { status?: number }).status === 409;
}

const CONFLICT_MESSAGE = "该规则已被其他管理员修改，请刷新后重试";

async function saveRule(): Promise<void> {
  const validation = validateForm();
  if (validation) {
    editorError.value = validation;
    return;
  }
  saving.value = true;
  editorError.value = "";
  try {
    if (editingRuleId.value && editingVersion.value !== null) {
      await agentScopeRulesApi.update(editingRuleId.value, {
        version: editingVersion.value,
        name: form.value.name.trim(),
        decision: form.value.decision,
        matchType: form.value.matchType,
        pattern: form.value.pattern.trim(),
        priority: form.value.priority,
        enabled: form.value.enabled,
        description: form.value.description.trim() || null,
      });
      showToast("规则已保存并立即生效（跨进程缓存传播最长约 15 秒）");
    } else {
      await agentScopeRulesApi.create({
        name: form.value.name.trim(),
        decision: form.value.decision,
        matchType: form.value.matchType,
        pattern: form.value.pattern.trim(),
        priority: form.value.priority,
        enabled: form.value.enabled,
        description: form.value.description.trim() || null,
      });
      showToast("规则已创建（默认停用，请先用测试接口验证）");
    }
    editorOpen.value = false;
    await load();
  } catch (reason) {
    if (isConflict(reason)) {
      editorError.value = CONFLICT_MESSAGE;
      // Reload so the optimistic-lock token and row reflect the other admin's
      // change; if the rule still exists, re-arm the editor with the fresh
      // version so the admin can retry without retyping.
      await load();
      const fresh = rules.value.find((rule) => rule.id === editingRuleId.value);
      if (fresh) editingVersion.value = fresh.version;
      else editorOpen.value = false;
    } else {
      editorError.value = reason instanceof Error ? reason.message : "规则保存失败";
    }
  } finally {
    saving.value = false;
  }
}

/** Preview the unsaved draft against the live policy — server-side only. */
async function previewCandidate(): Promise<void> {
  const validation = validateForm();
  if (validation) {
    editorError.value = validation;
    return;
  }
  if (!testMessage.value.trim() && !form.value.pattern) return;
  previewing.value = true;
  editorError.value = "";
  try {
    editorPreview.value = await agentScopeRulesApi.test({
      message: testMessage.value.trim() || form.value.pattern,
      candidateRule: {
        decision: form.value.decision,
        matchType: form.value.matchType,
        pattern: form.value.pattern.trim(),
        priority: form.value.priority,
      },
    });
  } catch (reason) {
    editorError.value = reason instanceof Error ? reason.message : "草稿测试失败";
  } finally {
    previewing.value = false;
  }
}

async function toggleRule(rule: ScopeRule): Promise<void> {
  if (togglingId.value !== null || deleting.value) return;
  togglingId.value = rule.id;
  error.value = "";
  try {
    const updated = await agentScopeRulesApi.update(rule.id, {
      version: rule.version,
      enabled: !rule.enabled,
    });
    const index = rules.value.findIndex((item) => item.id === rule.id);
    if (index >= 0) rules.value[index] = updated;
    showToast(
      updated.enabled
        ? "规则已启用并立即生效（跨进程缓存传播最长约 15 秒）"
        : "规则已停用并立即生效（跨进程缓存传播最长约 15 秒）",
    );
  } catch (reason) {
    // Revert is implicit: the row keeps the server's last-known state and the
    // list is reloaded so the version token is fresh again.
    if (isConflict(reason)) {
      error.value = CONFLICT_MESSAGE;
    } else {
      error.value = reason instanceof Error ? reason.message : "规则状态切换失败";
    }
    await load();
  } finally {
    togglingId.value = null;
  }
}

function requestDelete(rule: ScopeRule): void {
  deleteError.value = "";
  deleteTarget.value = rule;
}

async function confirmDelete(): Promise<void> {
  const target = deleteTarget.value;
  if (!target || deleting.value) return;
  deleting.value = true;
  deleteError.value = "";
  try {
    await agentScopeRulesApi.remove(target.id, target.version);
    deleteTarget.value = null;
    showToast("规则已删除并立即生效（跨进程缓存传播最长约 15 秒）");
    await load();
  } catch (reason) {
    if (isConflict(reason)) {
      deleteError.value = CONFLICT_MESSAGE;
      await load();
      const fresh = rules.value.find((rule) => rule.id === target.id);
      if (fresh) deleteTarget.value = fresh;
      else deleteTarget.value = null;
    } else {
      deleteError.value = reason instanceof Error ? reason.message : "规则删除失败";
    }
  } finally {
    deleting.value = false;
  }
}

/** Evaluate a message against the current live policy (no rule target). */
async function runTest(): Promise<void> {
  const message = testMessage.value.trim();
  if (!message || testing.value) return;
  testing.value = true;
  testError.value = "";
  try {
    testResult.value = await agentScopeRulesApi.test({ message });
  } catch (reason) {
    testError.value = reason instanceof Error ? reason.message : "测试请求失败";
  } finally {
    testing.value = false;
  }
}

/** Preview one saved (possibly disabled) rule against the test message. */
async function previewSavedRule(rule: ScopeRule): Promise<void> {
  const message = testMessage.value.trim() || rule.pattern;
  if (testing.value) return;
  testing.value = true;
  testError.value = "";
  try {
    testResult.value = await agentScopeRulesApi.test({ message, ruleId: rule.id });
  } catch (reason) {
    testError.value = reason instanceof Error ? reason.message : "测试请求失败";
  } finally {
    testing.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="agent-scope-rules">
    <p v-if="error" class="scope-rule-error" role="alert">{{ error }}</p>

    <section class="config-card">
      <header class="scope-rule-card-header">
        <div>
          <p>AGENT LAYER-1 SCOPE RULES</p>
          <h3>Agent 范围规则</h3>
          <span>运行时规则在会话开始前决定数据范围：先于内置基线评估，修改后立即生效，不走配置发布流程。</span>
        </div>
        <button class="admin-primary-button" type="button" @click="openCreate">＋ 新增规则</button>
      </header>
      <div class="scope-rule-filters">
        <input
          v-model="search"
          class="config-search"
          placeholder="搜索规则名称或匹配模式"
          aria-label="搜索范围规则"
        />
        <select v-model="decisionFilter" aria-label="按决策筛选">
          <option value="ALL">全部决策</option>
          <option value="ALLOW">允许（ALLOW）</option>
          <option value="BLOCK">拦截（BLOCK）</option>
        </select>
        <select v-model="enabledFilter" aria-label="按状态筛选">
          <option value="ALL">全部状态</option>
          <option value="ENABLED">已启用</option>
          <option value="DISABLED">已停用</option>
        </select>
      </div>
      <p v-if="loading" class="prototype-empty">正在加载范围规则…</p>
      <p v-else-if="filteredRules.length === 0" class="prototype-empty">没有符合条件的范围规则。</p>
      <div v-else class="admin-table-scroll">
        <table class="admin-table scope-rule-table">
          <thead>
            <tr>
              <th>名称</th>
              <th>决策</th>
              <th>匹配方式</th>
              <th>匹配模式</th>
              <th>优先级</th>
              <th>状态</th>
              <th>提醒</th>
              <th>更新时间</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="rule in filteredRules" :key="rule.id">
              <td>
                <strong>{{ rule.name }}</strong>
                <small v-if="rule.description">{{ rule.description }}</small>
                <small>版本 v{{ rule.version }} · {{ rule.createdBy ?? "未知创建人" }}</small>
              </td>
              <td>
                <span
                  class="scope-rule-tag"
                  :class="rule.decision === 'ALLOW' ? 'is-allow' : 'is-block'"
                  :title="rule.decision === 'ALLOW' ? 'ALLOW：命中则允许访问' : 'BLOCK：命中则拒绝访问'"
                >
                  {{ rule.decision === "ALLOW" ? "允许" : "拦截" }}
                </span>
              </td>
              <td :title="rule.matchType === 'EXACT' ? 'EXACT：整条消息完全一致才命中' : 'PHRASE：消息包含该短语即命中'">
                {{ matchLabel[rule.matchType] }}
              </td>
              <td><code class="scope-rule-pattern">{{ rule.pattern }}</code></td>
              <td>{{ rule.priority }}</td>
              <td>
                <span class="scope-rule-tag" :class="rule.enabled ? 'is-allow' : 'is-off'">
                  {{ rule.enabled ? "已启用" : "已停用" }}
                </span>
              </td>
              <td>
                <ul v-if="rule.warnings?.length" class="scope-rule-warnings">
                  <li v-for="warning in rule.warnings" :key="warning.code" :title="warning.code">
                    {{ warning.message }}
                  </li>
                </ul>
                <small v-else>—</small>
              </td>
              <td>{{ formatTime(rule.updatedAt) }}</td>
              <td>
                <button type="button" :disabled="togglingId !== null" @click="toggleRule(rule)">
                  {{ togglingId === rule.id ? "处理中…" : rule.enabled ? "停用" : "启用" }}
                </button>
                <button type="button" @click="openEdit(rule)">编辑</button>
                <button type="button" :disabled="togglingId !== null" @click="requestDelete(rule)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="scope-rule-note">
        共 {{ rules.length }} 条规则，其中 {{ enabledCount }} 条启用。排序口径与服务端
        <code>_rule_sort_key</code> 一致：优先级降序 → 精确匹配（EXACT）优先于短语匹配（PHRASE）→
        拦截（BLOCK）优先于允许（ALLOW）→ 名称稳定排序。规则修改立即生效；各进程通过
        Redis 通知刷新缓存，极端情况下最长约 15 秒内全量生效。
      </p>
    </section>

    <section class="config-card">
      <header class="scope-rule-card-header">
        <div>
          <p>RULE TEST</p>
          <h3>规则测试</h3>
          <span>输入一条拟发送给 Agent 的消息，按当前线上策略评估 Layer-1 结果；匹配始终由服务端执行。</span>
        </div>
      </header>
      <div class="scope-rule-test-row">
        <input
          v-model="testMessage"
          maxlength="500"
          placeholder="例如：帮我查一下全部项目的回款情况"
          aria-label="测试消息"
          @keyup.enter="runTest"
        />
        <button class="admin-primary-button" type="button" :disabled="testing || !testMessage.trim()" @click="runTest">
          {{ testing ? "测试中…" : "测试消息" }}
        </button>
      </div>
      <p v-if="testError" class="scope-rule-error" role="alert">{{ testError }}</p>
      <dl v-if="testResult" class="prototype-detail-list scope-rule-test-result">
        <div><dt>决策</dt><dd>{{ testResult.decision }}</dd></div>
        <div><dt>来源</dt><dd>{{ sourceLabel[testResult.source] }}（{{ testResult.source }}）</dd></div>
        <div>
          <dt>命中规则</dt>
          <dd>
            <template v-if="testResult.matchedRule">
              {{ testResult.matchedRule.name }}
              <small>（{{ testResult.matchedRule.id === "" ? "未保存的候选规则" : testResult.matchedRule.id }} · {{ matchLabel[testResult.matchedRule.matchType] }} · {{ testResult.matchedRule.decision }} · 优先级 {{ testResult.matchedRule.priority }}）</small>
            </template>
            <template v-else>未命中任何规则</template>
          </dd>
        </div>
        <div><dt>预览</dt><dd>{{ testResult.preview ? "是（包含未生效规则）" : "否（仅线上策略）" }}</dd></div>
        <div v-if="testResult.previewRuleId"><dt>预览规则 ID</dt><dd>{{ testResult.previewRuleId }}</dd></div>
        <div>
          <dt>提醒</dt>
          <dd>
            <ul v-if="testResult.warnings?.length" class="scope-rule-warnings">
              <li v-for="warning in testResult.warnings" :key="warning.code" :title="warning.code">{{ warning.message }}</li>
            </ul>
            <small v-else>无</small>
          </dd>
        </div>
      </dl>
      <div v-if="rules.length" class="scope-rule-preview-list">
        <small>按规则预览（对已保存但未启用的规则同样有效）：</small>
        <button
          v-for="rule in rules"
          :key="rule.id"
          type="button"
          :disabled="testing"
          :title="`使用当前测试消息预览规则「${rule.name}」`"
          @click="previewSavedRule(rule)"
        >
          {{ rule.name }}
        </button>
      </div>
    </section>

    <ModalDialog
      v-if="editorOpen"
      :eyebrow="editingRuleId ? 'EDIT SCOPE RULE' : 'NEW SCOPE RULE'"
      :title="editingRuleId ? '编辑范围规则' : '新增范围规则'"
      @close="closeEditor"
    >
      <form class="prototype-form" @submit.prevent="saveRule">
        <label><span>规则名称 *</span><input v-model="form.name" required maxlength="100" placeholder="2-100 个字符"></label>
        <label>
          <span>决策 *</span>
          <select v-model="form.decision" title="ALLOW：命中则允许访问；BLOCK：命中则拒绝访问">
            <option value="ALLOW">允许（ALLOW）</option>
            <option value="BLOCK">拦截（BLOCK）</option>
          </select>
        </label>
        <label>
          <span>匹配方式 *</span>
          <select v-model="form.matchType" title="EXACT：整条消息完全一致才命中；PHRASE：消息包含该短语即命中">
            <option value="EXACT">精确匹配（EXACT）</option>
            <option value="PHRASE">短语匹配（PHRASE）</option>
          </select>
        </label>
        <label><span>匹配模式 *</span><input v-model="form.pattern" required maxlength="200" placeholder="项目名称、短语或完整消息"></label>
        <label><span>优先级（0-1000）</span><input v-model.number="form.priority" type="number" min="0" max="1000" step="1"></label>
        <label>
          <span>状态</span>
          <select v-model="form.enabled" title="新规则默认停用：请先用测试接口验证后再启用，避免误拦截线上流量">
            <option :value="false">停用</option>
            <option :value="true">启用</option>
          </select>
        </label>
        <label class="full"><span>说明</span><textarea v-model="form.description" maxlength="500" placeholder="可选，最多 500 字"></textarea></label>
      </form>
      <p class="modal-copy">
        优先级排序与服务端一致：优先级降序 → 精确匹配优先于短语匹配 → 拦截优先于允许 →
        名称稳定排序。保存后立即生效，不走系统配置发布流程；新规则默认停用，建议先用「测试当前草稿」验证。
      </p>
      <section class="prototype-modal-section">
        <h3>测试当前草稿</h3>
        <div class="scope-rule-test-row">
          <input v-model="testMessage" maxlength="500" placeholder="输入一条消息，用未保存的草稿预览评估结果" aria-label="草稿测试消息" @keyup.enter="previewCandidate">
          <button type="button" :disabled="previewing || saving" @click="previewCandidate">
            {{ previewing ? "测试中…" : "测试当前草稿" }}
          </button>
        </div>
        <dl v-if="editorPreview" class="prototype-detail-list">
          <div><dt>决策</dt><dd>{{ editorPreview.decision }}</dd></div>
          <div><dt>来源</dt><dd>{{ sourceLabel[editorPreview.source] }}（{{ editorPreview.source }}）</dd></div>
          <div>
            <dt>命中规则</dt>
            <dd>
              <template v-if="editorPreview.matchedRule">
                {{ editorPreview.matchedRule.name }}
                <small>（{{ editorPreview.matchedRule.id === "" ? "未保存的候选规则" : editorPreview.matchedRule.id }} · {{ matchLabel[editorPreview.matchedRule.matchType] }} · {{ editorPreview.matchedRule.decision }} · 优先级 {{ editorPreview.matchedRule.priority }}）</small>
              </template>
              <template v-else>未命中任何规则</template>
            </dd>
          </div>
          <div><dt>预览</dt><dd>{{ editorPreview.preview ? "是（包含未生效规则）" : "否（仅线上策略）" }}</dd></div>
          <div v-if="editorPreview.previewRuleId"><dt>预览规则 ID</dt><dd>{{ editorPreview.previewRuleId }}</dd></div>
          <div>
            <dt>提醒</dt>
            <dd>
              <ul v-if="editorPreview.warnings?.length" class="scope-rule-warnings">
                <li v-for="warning in editorPreview.warnings" :key="warning.code" :title="warning.code">{{ warning.message }}</li>
              </ul>
              <small v-else>无</small>
            </dd>
          </div>
        </dl>
        <p class="modal-copy">草稿预览不会写入数据库，也不会影响线上评估；命中草稿时规则名称显示为「(预览规则)」。</p>
      </section>
      <p v-if="editorError" class="scope-rule-error" role="alert">{{ editorError }}</p>
      <template #footer>
        <button type="button" :disabled="saving" @click="closeEditor">取消</button>
        <button class="admin-primary-button" type="button" :disabled="saving" @click="saveRule">
          {{ saving ? "保存中…" : editingRuleId ? "保存规则" : "创建规则" }}
        </button>
      </template>
    </ModalDialog>

    <ModalDialog
      v-if="deleteTarget"
      eyebrow="DELETE SCOPE RULE"
      title="删除范围规则？"
      @close="deleteTarget = null"
    >
      <p class="modal-copy">将删除规则「{{ deleteTarget.name }}」（版本 v{{ deleteTarget.version }}）。删除为软删除并立即生效；已被该规则影响的会话范围不会回溯变更。</p>
      <p v-if="deleteError" class="scope-rule-error" role="alert">{{ deleteError }}</p>
      <template #footer>
        <button type="button" :disabled="deleting" @click="deleteTarget = null">取消</button>
        <button class="admin-danger-button" type="button" :disabled="deleting" @click="confirmDelete">
          {{ deleting ? "删除中…" : "删除" }}
        </button>
      </template>
    </ModalDialog>

    <p v-if="toast" class="prototype-toast">{{ toast }}</p>
  </div>
</template>

<style scoped>
.scope-rule-card-header{display:flex;align-items:flex-start;justify-content:space-between;gap:14px;margin-bottom:12px}
.scope-rule-card-header p{margin:0 0 4px;color:#1475d2;font-size:12px;font-weight:800;letter-spacing:.12em}
.scope-rule-card-header h3{margin:0}
.scope-rule-card-header span{display:block;margin-top:5px;color:#8298a8;line-height:1.6}
.scope-rule-filters{display:grid;grid-template-columns:minmax(0,1fr) 170px 130px;gap:10px;margin-bottom:14px}
.scope-rule-filters select,.scope-rule-test-row input{min-height:44px;padding:0 13px;border:1px solid #d3e2ec;border-radius:11px;color:#315873;background:#fff}
.scope-rule-tag{display:inline-flex;padding:4px 9px;border-radius:8px;font-size:12px;font-weight:750}
.scope-rule-tag.is-allow{color:#15886b;background:#e7f7f1}
.scope-rule-tag.is-block{color:#c73d45;background:#ffeded}
.scope-rule-tag.is-off{color:#8b9aa6;background:#edf2f5}
.scope-rule-pattern{padding:3px 6px;border-radius:7px;color:#28628c;background:#edf6fb;font-size:12px;word-break:break-all}
.scope-rule-warnings{display:grid;margin:0;padding:0;gap:4px;list-style:none;color:#9a6506;font-size:12px;font-weight:700}
.scope-rule-warnings li{padding:3px 7px;border-radius:7px;background:#fff3dc}
.scope-rule-note{margin:14px 0 0;color:#8298a8;font-size:12px;line-height:1.8}
.scope-rule-note code{padding:2px 5px;border-radius:6px;background:#edf6fb;color:#28628c}
.scope-rule-test-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:10px;align-items:center}
.scope-rule-test-result{margin-top:14px}
.scope-rule-preview-list{display:flex;margin-top:12px;align-items:center;gap:7px;flex-wrap:wrap}
.scope-rule-preview-list small{color:#8298a8}
.scope-rule-preview-list button{min-height:34px;padding:0 11px;border:1px solid #d4e3ec;border-radius:9px;color:#226aa0;background:#fff;font-size:12px;font-weight:700;cursor:pointer}
.scope-rule-preview-list button:hover:not(:disabled){background:#f4faff}
.scope-rule-error{margin:0 0 12px;padding:12px 14px;border:1px solid #f0c5c8;border-radius:12px;color:#b9363f;background:#fff0f1}
.scope-rule-table td button{margin-right:8px;border:0;color:#176fc8;background:transparent;cursor:pointer;font-weight:700}
.scope-rule-table td button:disabled{cursor:not-allowed;opacity:.55}
@media(max-width:760px){
  .scope-rule-filters{grid-template-columns:1fr 1fr}
  .scope-rule-card-header{flex-direction:column}
}
</style>
