<script setup lang="ts">
import type {
  AiCallLogDetail,
  AiCallLogListItem,
  AiCallResult,
  AiCallScene,
  AiConnectionTestResult,
  AiProviderListItem,
  AiProviderStrategyItem,
  AiProviderSummary,
  AiUsageOverview,
} from "@risk-platform/contracts";
import { computed, onMounted, reactive, ref } from "vue";

import { aiProviderApi } from "@/api/ai-providers";
import AdminShell from "@/components/AdminShell.vue";
import ModalDialog from "@/components/ModalDialog.vue";

const emptySummary: AiProviderSummary = { total: 0, healthy: 0, expiring: 0, sevenDayCallTotal: 0, sevenDaySuccessRate: 0 };
const emptyUsage: AiUsageOverview = { rangeStart: new Date().toISOString(), rangeEnd: new Date().toISOString(), callTotal: 0, successTotal: 0, successRate: 0, averageDurationMs: 0, p95DurationMs: 0, totalTokens: 0, trend: [] };

const providers = ref<AiProviderListItem[]>([]);
const summary = ref<AiProviderSummary>({ ...emptySummary });
const strategy = ref<AiProviderStrategyItem[]>([]);
const usage = ref<AiUsageOverview>({ ...emptyUsage });
const calls = ref<AiCallLogListItem[]>([]);
const callTotal = ref(0);
const keyword = ref("");
const status = ref<"all" | "ACTIVE" | "DISABLED">("all");
const usageScene = ref<"all" | AiCallScene>("all");
const callResult = ref<"all" | AiCallResult>("all");
const callPage = ref(1);
const pageSize = 10;
const loading = ref(false);
const saving = ref(false);
const testing = ref(false);
const drawerOpen = ref(false);
const editingId = ref<string | null>(null);
const testTarget = ref<AiProviderListItem | "draft" | "batch" | null>(null);
const testResults = ref<AiConnectionTestResult[]>([]);
const securityOpen = ref(false);
const strategyHelpOpen = ref(false);
const defaultCandidate = ref<AiProviderListItem | null>(null);
const callDetail = ref<AiCallLogDetail | null>(null);
const toast = ref("");
const keyVisible = ref(false);
const form = reactive({ name: "", vendor: "OpenAI 兼容服务", endpoint: "", protocol: "OPENAI_CHAT_COMPLETIONS" as "OPENAI_CHAT_COMPLETIONS" | "OPENAI_RESPONSES" | "ANTHROPIC_MESSAGES", model: "", key: "", expiry: "", timeout: 60, retries: 2, enabled: true });

const filteredProviders = computed(() => providers.value.filter((item) => {
  const matchesStatus = status.value === "all" || (status.value === "ACTIVE" ? item.enabled : !item.enabled);
  const term = keyword.value.trim().toLowerCase();
  return matchesStatus && (!term || `${item.name} ${item.vendor} ${item.model} ${item.endpoint}`.toLowerCase().includes(term));
}));
const defaultProvider = computed(() => providers.value.find((item) => item.isDefault) ?? null);
const expiringProvider = computed(() => providers.value
  .filter((item) => item.expiresAt)
  .sort((a, b) => String(a.expiresAt).localeCompare(String(b.expiresAt)))[0] ?? null);
const pageCount = computed(() => Math.max(1, Math.ceil(callTotal.value / pageSize)));
const chartHeights = computed(() => {
  const max = Math.max(1, ...usage.value.trend.map((item) => item.count));
  return usage.value.trend.map((item) => Math.max(4, Math.round((item.count / max) * 100)));
});
const formProviderName = computed(() => testTarget.value === "draft" ? form.name : typeof testTarget.value === "object" && testTarget.value ? testTarget.value.name : "已启用服务");

function showToast(message: string): void {
  toast.value = message;
  window.setTimeout(() => { if (toast.value === message) toast.value = ""; }, 3000);
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : "操作失败，请稍后重试";
}

function formatDate(value: string | null): string {
  return value || "未设置";
}

function formatTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(value)).replace(/\//g, "-");
}

function formatRange(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(value)).replace(/\//g, "-");
}

function formatTokens(value: number): string {
  return value >= 1_000_000 ? `${(value / 1_000_000).toFixed(2)}M` : value.toLocaleString("zh-CN");
}

function sceneLabel(scene: AiCallScene): string {
  return { WEEKLY_REPORT: "周报分析", AGENT_QUERY: "Agent问答", RISK_EXTRACTION: "风险提取", CONNECTION_TEST: "连接测试" }[scene];
}

function connectionStatusLabel(status: AiProviderListItem["lastTestStatus"]): string {
  return { HEALTHY: "连接正常", FAILED: "测试失败", UNTESTED: "未测试" }[status];
}

async function loadPage(): Promise<void> {
  loading.value = true;
  try {
    const [summaryData, providerData, strategyData] = await Promise.all([
      aiProviderApi.summary(), aiProviderApi.list(), aiProviderApi.strategy(),
    ]);
    summary.value = summaryData;
    providers.value = providerData;
    strategy.value = strategyData;
    await Promise.all([loadUsage(), loadCalls()]);
  } catch (error) {
    showToast(messageOf(error));
  } finally {
    loading.value = false;
  }
}

async function loadUsage(): Promise<void> {
  usage.value = await aiProviderApi.usage(usageScene.value === "all" ? undefined : usageScene.value);
}

async function selectUsageScene(scene: "all" | AiCallScene): Promise<void> {
  usageScene.value = scene;
  try { await loadUsage(); } catch (error) { showToast(messageOf(error)); }
}

async function loadCalls(): Promise<void> {
  const result = await aiProviderApi.calls({ page: callPage.value, pageSize, result: callResult.value === "all" ? undefined : callResult.value });
  calls.value = result.items;
  callTotal.value = result.total;
}

async function selectCallResult(result: "all" | AiCallResult): Promise<void> {
  callResult.value = result;
  callPage.value = 1;
  try { await loadCalls(); } catch (error) { showToast(messageOf(error)); }
}

async function changePage(page: number): Promise<void> {
  if (page < 1 || page > pageCount.value) return;
  callPage.value = page;
  try { await loadCalls(); } catch (error) { showToast(messageOf(error)); }
}

function openCreate(provider?: AiProviderListItem): void {
  editingId.value = provider?.id ?? null;
  Object.assign(form, provider ? {
    name: provider.name, vendor: provider.vendor, endpoint: provider.endpoint, protocol: provider.protocol, model: provider.model,
    key: "", expiry: provider.expiresAt ?? "", timeout: provider.timeoutSeconds, retries: provider.retryCount, enabled: provider.enabled,
  } : { name: "", vendor: "OpenAI 兼容服务", endpoint: "", protocol: "OPENAI_CHAT_COMPLETIONS", model: "", key: "", expiry: "", timeout: 60, retries: 2, enabled: true });
  keyVisible.value = false;
  drawerOpen.value = true;
}

function validateForm(requireKey = false): string | null {
  if (!form.name.trim() || !form.vendor.trim() || !form.endpoint.trim() || !form.model.trim()) return "请填写全部必填项";
  if (!/^https:\/\//i.test(form.endpoint.trim())) return "服务地址必须使用 HTTPS";
  if (requireKey && form.key.trim().length < 8) return "API Key 至少需要8个字符";
  if (form.key && form.key.trim().length < 8) return "API Key 至少需要8个字符";
  return null;
}

async function saveProvider(): Promise<void> {
  const validation = validateForm(!editingId.value);
  if (validation) return showToast(validation);
  const existing = editingId.value ? providers.value.find((item) => item.id === editingId.value) : null;
  const connectionChanged = Boolean(existing && (
    existing.endpoint !== form.endpoint.trim()
    || existing.protocol !== form.protocol
    || existing.model !== form.model.trim()
    || existing.timeoutSeconds !== Number(form.timeout)
    || existing.retryCount !== Number(form.retries)
  ));
  saving.value = true;
  try {
    const body = { name: form.name.trim(), vendor: form.vendor.trim(), endpoint: form.endpoint.trim(), protocol: form.protocol, model: form.model.trim(), expiresAt: form.expiry || null, timeoutSeconds: Number(form.timeout), retryCount: Number(form.retries), enabled: form.enabled };
    let savedProvider: AiProviderListItem;
    if (editingId.value) {
      savedProvider = await aiProviderApi.update(editingId.value, body);
      if (form.key.trim()) savedProvider = await aiProviderApi.rotateKey(editingId.value, { apiKey: form.key.trim(), expiresAt: form.expiry || null });
    } else {
      savedProvider = await aiProviderApi.create({ ...body, apiKey: form.key.trim() });
    }
    drawerOpen.value = false;
    showToast(
      savedProvider.lastTestStatus === "UNTESTED" && (connectionChanged || Boolean(form.key.trim()) || !existing)
        ? "AI 服务配置已保存，当前为未测试，请重新执行连接测试。"
        : "AI 服务配置已保存"
    );
    await loadPage();
  } catch (error) {
    showToast(messageOf(error));
  } finally {
    saving.value = false;
  }
}

async function openTest(provider: AiProviderListItem): Promise<void> {
  testTarget.value = provider;
  testResults.value = [];
  testing.value = true;
  try { testResults.value = [await aiProviderApi.test(provider.id)]; } catch (error) { showToast(messageOf(error)); testTarget.value = null; } finally { testing.value = false; }
  await loadPage();
}

async function testDraft(): Promise<void> {
  if (editingId.value && !form.key.trim()) {
    const provider = providers.value.find((item) => item.id === editingId.value);
    if (provider) return openTest(provider);
  }
  const validation = validateForm(true);
  if (validation) return showToast(validation);
  testTarget.value = "draft";
  testResults.value = [];
  testing.value = true;
  try {
    testResults.value = [await aiProviderApi.testDraft({ name: form.name.trim(), endpoint: form.endpoint.trim(), protocol: form.protocol, model: form.model.trim(), apiKey: form.key.trim(), timeoutSeconds: Number(form.timeout), retryCount: Number(form.retries) })];
  } catch (error) { showToast(messageOf(error)); testTarget.value = null; } finally { testing.value = false; }
  await loadCalls();
}

async function testAll(): Promise<void> {
  if (!providers.value.some((item) => item.enabled)) return showToast("当前没有已启用的AI服务");
  testTarget.value = "batch";
  testResults.value = [];
  testing.value = true;
  try { testResults.value = await aiProviderApi.testAll(); } catch (error) { showToast(messageOf(error)); testTarget.value = null; } finally { testing.value = false; }
  await loadPage();
}

async function toggleStatus(provider: AiProviderListItem): Promise<void> {
  try { await aiProviderApi.setStatus(provider.id, !provider.enabled); showToast(provider.enabled ? "AI服务已停用" : "AI服务已启用"); await loadPage(); } catch (error) { showToast(messageOf(error)); }
}

async function confirmDefault(): Promise<void> {
  if (!defaultCandidate.value) return;
  try { await aiProviderApi.setDefault(defaultCandidate.value.id); showToast(`已将“${defaultCandidate.value.name}”设为默认服务`); defaultCandidate.value = null; await loadPage(); } catch (error) { showToast(messageOf(error)); }
}

async function openCallDetail(id: string): Promise<void> {
  try { callDetail.value = await aiProviderApi.callDetail(id); } catch (error) { showToast(messageOf(error)); }
}

onMounted(() => void loadPage());
</script>

<template>
  <AdminShell>
    <section class="admin-page-heading prototype-heading"><div><p class="admin-eyebrow">AI SERVICE &amp; CREDENTIAL MANAGEMENT</p><h1>API Key 管理</h1><p>统一管理 AI 服务地址、模型、访问密钥、有效期与调用策略。</p></div><div class="prototype-heading-actions"><button class="admin-outline-button" type="button" :disabled="loading || testing" @click="testAll">{{ testing ? '测试中…' : '批量连接测试' }}</button><button class="admin-primary-button" type="button" @click="openCreate()">＋ 新增 AI 服务</button></div></section>

    <section class="prototype-security-banner"><span>锁</span><div><strong>密钥安全保护</strong><p>API Key 仅加密保存，列表、日志与审计记录均不显示明文。</p></div><button type="button" @click="securityOpen=true">查看安全规则</button></section>

    <section class="prototype-metric-grid"><button class="prototype-metric tone-blue" type="button" @click="status='all'"><span class="metric-glyph">AI</span><small>AI 服务</small><strong>{{ summary.total }}<em>项</em></strong><p>统一接入管理</p></button><button class="prototype-metric tone-green" type="button" @click="status='ACTIVE'"><span class="metric-glyph">✓</span><small>连接正常</small><strong>{{ summary.healthy }}<em>项</em></strong><p>最近测试通过</p></button><button class="prototype-metric tone-orange" type="button"><span class="metric-glyph">!</span><small>即将到期</small><strong>{{ summary.expiring }}<em>项</em></strong><p>30天内需要更新</p></button><article class="prototype-metric tone-violet"><span class="metric-glyph">%</span><small>近7日成功率</small><strong>{{ summary.sevenDaySuccessRate }}<em>%</em></strong><p>共 {{ summary.sevenDayCallTotal.toLocaleString('zh-CN') }} 次调用</p></article></section>

    <div class="prototype-two-columns api-main-grid">
      <section class="prototype-panel"><header class="prototype-panel-heading"><div><p>AI SERVICE CONFIGURATION</p><h2>AI 服务配置 <small>{{ filteredProviders.length }}项服务</small></h2></div></header><div class="prototype-filter-row"><input v-model="keyword" type="search" placeholder="搜索服务名称、厂商或模型"><div class="segmented"><button :class="{active:status==='all'}" type="button" @click="status='all'">全部</button><button :class="{active:status==='ACTIVE'}" type="button" @click="status='ACTIVE'">已启用</button><button :class="{active:status==='DISABLED'}" type="button" @click="status='DISABLED'">已停用</button></div></div>
        <div class="provider-list"><article v-for="provider in filteredProviders" :key="provider.id" class="provider-card"><header><span class="provider-avatar">AI</span><span><strong>{{ provider.name }}</strong><small>{{ provider.vendor }} · {{ provider.model }} · {{ provider.protocol }}</small></span><em v-if="provider.isDefault">默认</em><i :class="{off:!provider.enabled}">{{ provider.enabled?'已启用':'已停用' }}</i></header><dl><div><dt>服务地址</dt><dd>{{ provider.endpoint }}</dd></div><div><dt>API Key</dt><dd><code>{{ provider.maskedKey }}</code></dd></div><div><dt>密钥有效期</dt><dd>{{ formatDate(provider.expiresAt) }}</dd></div><div><dt>超时 / 重试</dt><dd>{{ provider.timeoutSeconds }}s / {{ provider.retryCount }}次</dd></div><div><dt>连接状态</dt><dd>{{ connectionStatusLabel(provider.lastTestStatus) }}<template v-if="provider.lastTestStatus === 'UNTESTED'">，请重新执行连接测试</template></dd></div></dl><footer><button type="button" @click="openTest(provider)">连接测试</button><button type="button" @click="openCreate(provider)">编辑</button><button type="button" :disabled="provider.isDefault" @click="defaultCandidate=provider">{{ provider.isDefault?'当前默认服务':'设为默认服务' }}</button><button type="button" @click="toggleStatus(provider)">{{ provider.enabled?'停用服务':'启用服务' }}</button></footer></article><p v-if="!filteredProviders.length" class="prototype-empty">{{ loading ? '正在加载 AI 服务配置…' : '没有符合条件的 AI 服务配置。' }}</p></div>
      </section>
      <aside class="stacked-panels"><section class="prototype-panel"><header class="prototype-panel-heading"><div><p>CALL STRATEGY</p><h2>调用策略</h2></div><button type="button" aria-label="查看调用策略说明" @click="strategyHelpOpen=true">?</button></header><ol class="strategy-list"><li v-for="(provider,index) in strategy" :key="provider.id"><b>{{ index+1 }}</b><span><strong>{{ provider.name }}</strong><small>{{ provider.isDefault?'默认主服务':'主服务失败后自动切换' }}</small></span><em>{{ provider.enabled?'可用':'停用' }}</em></li><li v-if="!strategy.length"><span><strong>暂无调用服务</strong><small>请先新增 AI 服务配置</small></span></li></ol></section><section class="prototype-panel expiry-card"><header class="prototype-panel-heading"><div><p>KEY LIFECYCLE</p><h2>密钥有效期</h2></div></header><template v-if="expiringProvider"><strong>{{ expiringProvider.name }}</strong><p>API Key 将于 {{ formatDate(expiringProvider.expiresAt) }} 到期，建议提前更新并完成连通性测试。</p><button type="button" @click="openCreate(expiringProvider)">立即更新密钥</button></template><template v-else><strong>暂无到期提醒</strong><p>当前没有已设置有效期的 API Key。</p></template></section></aside>
    </div>

    <section class="prototype-panel"><header class="prototype-panel-heading"><div><p>USAGE OVERVIEW</p><h2>近7日调用概览</h2><span>{{ formatRange(usage.rangeStart) }} 至 {{ formatRange(usage.rangeEnd) }}</span></div><div class="segmented"><button :class="{active:usageScene==='all'}" type="button" @click="selectUsageScene('all')">全部</button><button :class="{active:usageScene==='WEEKLY_REPORT'}" type="button" @click="selectUsageScene('WEEKLY_REPORT')">周报分析</button><button :class="{active:usageScene==='AGENT_QUERY'}" type="button" @click="selectUsageScene('AGENT_QUERY')">Agent问答</button><button :class="{active:usageScene==='RISK_EXTRACTION'}" type="button" @click="selectUsageScene('RISK_EXTRACTION')">风险提取</button></div></header><div class="usage-overview"><article><small>调用总量</small><strong>{{ usage.callTotal.toLocaleString('zh-CN') }}</strong><p>最近7日调用记录</p></article><article><small>成功调用</small><strong>{{ usage.successTotal.toLocaleString('zh-CN') }}</strong><p>成功率 {{ usage.successRate }}%</p></article><article><small>平均耗时</small><strong>{{ (usage.averageDurationMs/1000).toFixed(2) }}s</strong><p>P95 {{ (usage.p95DurationMs/1000).toFixed(2) }}s</p></article><article><small>Token 用量</small><strong>{{ formatTokens(usage.totalTokens) }}</strong><p>输入与输出合计</p></article><div class="usage-chart" aria-label="近7日调用趋势"><i v-for="(height,index) in chartHeights" :key="`${index}-${height}`" :style="{height:`${height}%`}" :title="`${usage.trend[index]?.date}: ${usage.trend[index]?.count ?? 0}次`"></i></div></div></section>


    <ModalDialog v-if="drawerOpen" eyebrow="AI SERVICE CONFIGURATION" :title="editingId?'编辑 AI 服务':'新增 AI 服务'" @close="drawerOpen=false"><form class="prototype-form" autocomplete="off" @submit.prevent="saveProvider"><label><span>配置名称 *</span><input v-model="form.name" name="ai-service-display-name" autocomplete="off" required></label><label><span>服务商 *</span><select v-model="form.vendor" name="ai-service-vendor"><option>OpenAI 兼容服务</option><option>通用大模型服务</option></select></label><label><span>接口协议 *</span><select v-model="form.protocol"><option value="OPENAI_CHAT_COMPLETIONS">OpenAI Chat Completions</option><option value="OPENAI_RESPONSES">OpenAI Responses / Codex</option><option value="ANTHROPIC_MESSAGES">Anthropic Claude Messages</option></select></label><label class="full"><span>服务 Base URL *</span><input v-model="form.endpoint" name="ai-service-endpoint" autocomplete="url" required placeholder="https://api.example.com/v1"><small>填写 API Base URL，不要填写 /chat/completions、/responses 或 /messages。</small></label><label><span>模型名称 *</span><input v-model="form.model" name="ai-model-identifier" autocomplete="off" required></label><label><span>API Key {{ editingId?'（留空则不修改）':'*' }}</span><span class="credential-input"><input v-model="form.key" name="ai-service-api-key" :type="keyVisible?'text':'password'" autocomplete="new-password" spellcheck="false" :required="!editingId"><button type="button" @click="keyVisible=!keyVisible">{{ keyVisible?'隐藏':'显示' }}</button></span></label><label><span>有效期</span><input v-model="form.expiry" name="ai-key-expiry" type="date"></label><label><span>超时时间（秒）</span><input v-model="form.timeout" name="ai-timeout-seconds" type="number" min="1" max="300"></label><label><span>失败重试次数</span><input v-model="form.retries" name="ai-retry-count" type="number" min="0" max="5"></label><label class="switch-row full"><span><strong>启用此服务</strong><small>停用后不会参与模型调用</small></span><input v-model="form.enabled" name="ai-service-enabled" type="checkbox"></label></form><template #footer><button type="button" @click="drawerOpen=false">取消</button><button type="button" :disabled="testing" @click="testDraft">{{ testing?'测试中…':'保存前测试' }}</button><button class="admin-primary-button" type="button" :disabled="saving" @click="saveProvider">{{ saving?'保存中…':'保存配置' }}</button></template></ModalDialog>
    <ModalDialog v-if="testTarget" eyebrow="CONNECTION TEST" :title="testTarget==='batch'?'批量连接测试':`测试 ${formProviderName} 连接`" @close="testTarget=null"><div v-if="testing" class="connection-result"><span>…</span><strong>正在测试连接</strong><p>正在安全验证服务地址、凭据和模型响应。</p></div><div v-else class="connection-result" :class="{failed:testResults.some(item=>!item.success)}"><span>{{ testResults.every(item=>item.success)?'✓':'!' }}</span><strong>{{ testResults.every(item=>item.success)?'连接测试通过':'连接测试未通过' }}</strong><p v-for="item in testResults" :key="item.traceId">{{ item.providerName }}：{{ item.success?`模型响应正常，耗时 ${(item.latencyMs/1000).toFixed(2)} 秒。`:item.errorSummary }}</p></div><template #footer><button type="button" @click="testTarget=null">关闭</button><button v-if="testTarget!=='draft'" class="admin-primary-button" type="button" :disabled="testing" @click="testTarget==='batch'?testAll():openTest(testTarget as AiProviderListItem)">重新测试</button></template></ModalDialog>
    <ModalDialog v-if="defaultCandidate" eyebrow="DEFAULT SERVICE" title="切换默认 AI 服务" @close="defaultCandidate=null"><p class="modal-copy">确认将“{{ defaultCandidate.name }}”设为默认服务？后续 AI 调用将优先使用该服务，原默认服务转为备用。</p><template #footer><button type="button" @click="defaultCandidate=null">取消</button><button class="admin-primary-button" type="button" @click="confirmDefault">确认切换</button></template></ModalDialog>
    <ModalDialog v-if="strategyHelpOpen" eyebrow="CALL STRATEGY" title="调用策略说明" @close="strategyHelpOpen=false"><ul class="prototype-rule-list"><li>默认服务作为首选调用节点。</li><li>默认服务不可用时，按优先级切换至已启用的备用服务。</li><li>停用服务不参与自动调用与批量连接测试。</li></ul><template #footer><button class="admin-primary-button" type="button" @click="strategyHelpOpen=false">我已了解</button></template></ModalDialog>
    <ModalDialog v-if="securityOpen" eyebrow="CREDENTIAL SECURITY" title="API Key 安全规则" @close="securityOpen=false"><ul class="prototype-rule-list"><li>API Key 使用加密字段存储，页面只显示掩码。</li><li>新增、轮换、测试、启停和默认服务切换全部写入审计日志。</li><li>调用日志不记录完整邮件、提示词、模型响应和密钥明文。</li><li>密钥到期前30天提醒系统管理员轮换。</li></ul><template #footer><button class="admin-primary-button" type="button" @click="securityOpen=false">我已了解</button></template></ModalDialog>
    <ModalDialog v-if="callDetail" eyebrow="CALL METADATA DETAIL" title="AI调用元数据" @close="callDetail=null"><dl class="prototype-detail-list"><div><dt>Trace ID</dt><dd>{{ callDetail.traceId }}</dd></div><div><dt>调用时间</dt><dd>{{ new Date(callDetail.createdAt).toLocaleString('zh-CN',{hour12:false}) }}</dd></div><div><dt>服务 / 模型</dt><dd>{{ callDetail.providerName }} / {{ callDetail.model }}</dd></div><div><dt>调用场景</dt><dd>{{ sceneLabel(callDetail.scene) }}</dd></div><div><dt>Token / 耗时</dt><dd>{{ callDetail.totalTokens.toLocaleString('zh-CN') }} / {{ (callDetail.durationMs/1000).toFixed(2) }}s</dd></div><div><dt>调用结果</dt><dd>{{ callDetail.result==='SUCCESS'?'成功':'失败' }}</dd></div><div><dt>错误摘要</dt><dd>{{ callDetail.errorSummary || '—' }}</dd></div><div><dt>操作人</dt><dd>{{ callDetail.actorDisplayName || '系统任务' }}</dd></div><div><dt>数据保护说明</dt><dd>{{ callDetail.dataProtectionNotice }}</dd></div></dl></ModalDialog>
    <p v-if="toast" class="prototype-toast" role="status">{{ toast }}</p>
  </AdminShell>
</template>
