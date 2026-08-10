<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";
import type {
  MailMessageDetail,
  MailMessageListItem,
  MailMessageStatus,
  MailRiskCandidateItem,
  MailRiskCandidateUpdateInput,
  MailRiskReviewOptions,
  MailSyncBatchItem,
  MailSyncSummary,
} from "@risk-platform/contracts";

import { mailboxApi } from "@/api/mailbox";
import BusinessHeader from "@/components/BusinessHeader.vue";
import ModalDialog from "@/components/ModalDialog.vue";

const emptySummary: MailSyncSummary = {
  configured: false,
  maskedEmail: null,
  latestBatch: null,
  latestScannedCount: 0,
  latestNewCount: 0,
  latestSuccessCount: 0,
  latestSkippedCount: 0,
  latestDuplicateCount: 0,
  latestRuleMismatchCount: 0,
  latestFailedCount: 0,
  latestRiskCandidateCount: 0,
  latestPendingRiskCount: 0,
  historicalFailedCount: 0,
};

const tab = ref<"mail" | "batch">("mail");
const loading = ref(true);
const error = ref("");
const toast = ref("");
const summary = ref<MailSyncSummary>({ ...emptySummary });
const mails = ref<MailMessageListItem[]>([]);
const mailTotal = ref(0);
const batches = ref<MailSyncBatchItem[]>([]);
const batchTotal = ref(0);
const keyword = ref("");
const status = ref<"all" | "risk" | MailMessageStatus>("all");
const batchId = ref("");
const page = ref(1);
const pageSize = 10;
const batchPage = ref(1);
const syncing = ref(false);
const selected = ref<MailMessageDetail | null>(null);
const detailLoading = ref(false);
const actionBusy = ref("");
const editCandidate = ref<MailRiskCandidateItem | null>(null);
const reviewOptions = ref<MailRiskReviewOptions | null>(null);
const editForm = reactive<MailRiskCandidateUpdateInput>({
  projectId: "",
  categoryId: "",
  level: "UNKNOWN",
  description: "",
  evidence: "",
  suggestion: "",
});

let toastTimer = 0;
let searchTimer = 0;

const totalPages = computed(() => Math.max(1, Math.ceil(mailTotal.value / pageSize)));
const batchTotalPages = computed(() => Math.max(1, Math.ceil(batchTotal.value / pageSize)));
const mailRangeStart = computed(() => mailTotal.value ? (page.value - 1) * pageSize + 1 : 0);
const mailRangeEnd = computed(() => Math.min(page.value * pageSize, mailTotal.value));
const latestTerminal = computed(() => ["SUCCESS", "PARTIAL", "FAILURE"].includes(summary.value.latestBatch?.status ?? ""));

function notify(message: string) {
  window.clearTimeout(toastTimer);
  toast.value = message;
  toastTimer = window.setTimeout(() => { toast.value = ""; }, 2600);
}

function failureMessage(value: unknown) {
  return value instanceof Error ? value.message : "请求失败，请稍后重试";
}

function formatDate(value: string | null, includeYear = true) {
  if (!value) return "--";
  const date = new Date(value);
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    ...(includeYear ? { year: "numeric" as const } : {}),
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date).replace(/\//g, "-");
}

function formatDuration(batch: MailSyncBatchItem) {
  if (!batch.startedAt) return batch.status === "QUEUED" ? "排队中" : "--";
  const end = batch.finishedAt ? new Date(batch.finishedAt).getTime() : Date.now();
  const seconds = Math.max(0, Math.round((end - new Date(batch.startedAt).getTime()) / 1000));
  if (seconds < 60) return `${seconds}秒`;
  return `${Math.floor(seconds / 60)}分${String(seconds % 60).padStart(2, "0")}秒`;
}

function statusLabel(value: MailMessageStatus) {
  return { ANALYZING: "分析中", COMPLETED: "分析完成", SKIPPED: "已跳过", FAILED: "处理失败" }[value];
}

function batchStatusLabel(value: MailSyncBatchItem["status"]) {
  return { QUEUED: "等待执行", RUNNING: "执行中", SUCCESS: "全部完成", PARTIAL: "部分失败", FAILURE: "执行失败" }[value];
}

function triggerLabel(value: MailSyncBatchItem["trigger"]) {
  return { MANUAL: "手动同步", SCHEDULED: "自动同步", RETRY: "失败重试" }[value];
}

function attachmentSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

async function loadSummary() {
  summary.value = await mailboxApi.syncSummary();
}

async function loadMails() {
  if (!summary.value.configured) {
    mails.value = [];
    mailTotal.value = 0;
    return;
  }
  const result = await mailboxApi.messages({
    keyword: keyword.value.trim() || undefined,
    status: status.value !== "all" && status.value !== "risk" ? status.value : undefined,
    batchId: batchId.value || undefined,
    withRisk: status.value === "risk" || undefined,
    page: page.value,
    pageSize,
  });
  mails.value = result.items;
  mailTotal.value = result.total;
  summary.value.historicalFailedCount = result.historicalFailedCount;
}

async function loadBatches() {
  if (!summary.value.configured) {
    batches.value = [];
    batchTotal.value = 0;
    return;
  }
  const result = await mailboxApi.batches(batchPage.value, pageSize);
  batches.value = result.items;
  batchTotal.value = result.total;
}

async function refreshAll() {
  loading.value = true;
  error.value = "";
  try {
    await loadSummary();
    await Promise.all([loadMails(), loadBatches()]);
  } catch (cause) {
    error.value = failureMessage(cause);
  } finally {
    loading.value = false;
  }
}

async function reloadMailPage() {
  error.value = "";
  try { await loadMails(); } catch (cause) { error.value = failureMessage(cause); }
}

function setStatus(value: typeof status.value) {
  status.value = value;
  page.value = 1;
}

function resetFilters() {
  keyword.value = "";
  status.value = "all";
  batchId.value = "";
  page.value = 1;
}

async function openMessage(id: string) {
  detailLoading.value = true;
  error.value = "";
  try { selected.value = await mailboxApi.message(id); }
  catch (cause) { error.value = failureMessage(cause); }
  finally { detailLoading.value = false; }
}

async function syncNow() {
  if (syncing.value || !summary.value.configured) return;
  syncing.value = true;
  error.value = "";
  try {
    const batch = await mailboxApi.sync();
    notify(`同步批次 ${batch.code} 已进入队列`);
    for (let attempt = 0; attempt < 60; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 1000));
      await loadSummary();
      if (summary.value.latestBatch?.id === batch.id && latestTerminal.value) break;
    }
    await Promise.all([loadMails(), loadBatches()]);
    notify(summary.value.latestBatch?.status === "FAILURE" ? "同步失败，请查看批次说明" : "最新周报同步完成");
  } catch (cause) {
    error.value = failureMessage(cause);
  } finally {
    syncing.value = false;
  }
}

async function retryMessage() {
  if (!selected.value || actionBusy.value) return;
  actionBusy.value = selected.value.id;
  try {
    await mailboxApi.retryMessage(selected.value.id);
    notify("失败邮件已进入重新处理队列");
    selected.value = null;
    await refreshAll();
  } catch (cause) { error.value = failureMessage(cause); }
  finally { actionBusy.value = ""; }
}

async function ensureReviewOptions() {
  if (!reviewOptions.value) reviewOptions.value = await mailboxApi.reviewOptions();
}

async function beginEdit(candidate: MailRiskCandidateItem) {
  try {
    await ensureReviewOptions();
    editCandidate.value = candidate;
    Object.assign(editForm, {
      projectId: candidate.projectId,
      categoryId: candidate.categoryId,
      level: candidate.level,
      description: candidate.description,
      evidence: candidate.evidence,
      suggestion: candidate.suggestion,
    });
  } catch (cause) { error.value = failureMessage(cause); }
}

async function saveCandidate() {
  if (!editCandidate.value || actionBusy.value) return;
  actionBusy.value = editCandidate.value.id;
  try {
    await mailboxApi.updateRiskCandidate(editCandidate.value.id, { ...editForm });
    notify("风险线索已调整");
    editCandidate.value = null;
    if (selected.value) selected.value = await mailboxApi.message(selected.value.id);
  } catch (cause) { error.value = failureMessage(cause); }
  finally { actionBusy.value = ""; }
}

async function ignoreCandidate(candidate: MailRiskCandidateItem) {
  if (actionBusy.value || !window.confirm("确认忽略该风险线索？该操作会保留审计记录。")) return;
  actionBusy.value = candidate.id;
  try {
    await mailboxApi.ignoreRiskCandidate(candidate.id);
    notify("风险线索已忽略");
    if (selected.value) selected.value = await mailboxApi.message(selected.value.id);
    await loadSummary();
  } catch (cause) { error.value = failureMessage(cause); }
  finally { actionBusy.value = ""; }
}

async function confirmCandidate(candidate: MailRiskCandidateItem) {
  if (actionBusy.value || !window.confirm("确认将该线索发布为正式项目风险，并自动生成关联待办？")) return;
  actionBusy.value = candidate.id;
  try {
    await mailboxApi.confirmRiskCandidate(candidate.id);
    notify("风险线索已确认并发布到看板");
    if (selected.value) selected.value = await mailboxApi.message(selected.value.id);
    await loadSummary();
  } catch (cause) { error.value = failureMessage(cause); }
  finally { actionBusy.value = ""; }
}

function openBatch(batch: MailSyncBatchItem) {
  batchId.value = batch.id;
  page.value = 1;
  tab.value = "mail";
}

watch([status, batchId, page], () => { void reloadMailPage(); });
watch(keyword, () => {
  window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => { page.value = 1; void reloadMailPage(); }, 300);
});
watch(batchPage, () => { void loadBatches(); });

onMounted(() => { void refreshAll(); });
</script>

<template>
  <div class="business-page">
    <BusinessHeader @agent="notify('Agent 智能对话已打开')" />
    <main class="sync-main">
      <nav class="page-breadcrumb">
        <RouterLink to="/">Web 风险看板</RouterLink><span>›</span>
        <RouterLink to="/mailbox-settings">个人邮箱配置</RouterLink><span>›</span><strong>邮箱同步结果</strong>
      </nav>

      <section class="business-page-heading">
        <div>
          <p>MAIL SYNC RESULTS</p>
          <h1>邮箱同步结果</h1>
          <span>查看每封周报邮件的同步、项目匹配和风险提取结果，处理失败任务与待确认线索。</span>
          <div class="scope-note">
            <b>风</b><span><strong>当前邮箱：{{ summary.maskedEmail || "尚未配置" }}</strong><small>仅本人可以查看邮件内容、重试失败任务和确认风险线索。</small></span>
          </div>
        </div>
        <div>
          <RouterLink class="admin-outline-button" to="/mailbox-settings">邮箱配置</RouterLink>
          <button class="admin-primary-button" type="button" :disabled="syncing || !summary.configured" @click="syncNow">{{ syncing ? "同步处理中…" : "同步最新周报" }}</button>
        </div>
      </section>

      <p v-if="error" class="sync-error" role="alert">{{ error }}</p>
      <section v-if="!loading && !summary.configured" class="prototype-panel unconfigured-state">
        <span>邮</span><h2>请先完成个人邮箱配置</h2>
        <p>邮箱配置仅属于风险管理员本人。保存并通过连接测试后，系统才会按规则增量同步周报邮件。</p>
        <RouterLink class="admin-primary-button" to="/mailbox-settings">前往配置邮箱</RouterLink>
      </section>

      <template v-else>
        <section class="latest-batch">
          <span>{{ summary.latestBatch && latestTerminal ? "✓" : "↻" }}</span>
          <div>
            <p>LATEST BATCH</p>
            <h2>{{ summary.latestBatch ? `最近一次同步${batchStatusLabel(summary.latestBatch.status)}` : "尚无同步记录" }}</h2>
            <small v-if="summary.latestBatch">批次 {{ summary.latestBatch.code }} · {{ triggerLabel(summary.latestBatch.trigger) }} · 用时 {{ formatDuration(summary.latestBatch) }}</small>
            <small v-else>点击“同步最新周报”创建首个同步批次</small>
          </div>
          <dl v-if="summary.latestBatch">
            <div><dt>开始时间</dt><dd>{{ formatDate(summary.latestBatch.startedAt || summary.latestBatch.createdAt) }}</dd></div>
            <div><dt>完成时间</dt><dd>{{ formatDate(summary.latestBatch.finishedAt) }}</dd></div>
          </dl>
          <em v-if="summary.latestBatch"><i></i>{{ batchStatusLabel(summary.latestBatch.status) }}</em>
        </section>

        <section class="prototype-metric-grid sync-metrics" aria-label="最新同步指标">
          <button class="prototype-metric tone-blue" type="button" @click="setStatus('all')"><span class="metric-glyph">邮</span><small>新增邮件</small><strong>{{ summary.latestNewCount }}<em>封</em></strong><p>本批次扫描{{ summary.latestScannedCount }}封</p></button>
          <button class="prototype-metric tone-green" type="button" @click="setStatus('COMPLETED')"><span class="metric-glyph">✓</span><small>分析成功</small><strong>{{ summary.latestSuccessCount }}<em>封</em></strong><p>成功率{{ summary.latestNewCount ? Math.round(summary.latestSuccessCount / summary.latestNewCount * 100) : 0 }}%</p></button>
          <button class="prototype-metric tone-violet" type="button" @click="setStatus('risk')"><span class="metric-glyph">险</span><small>提取风险线索</small><strong>{{ summary.latestRiskCandidateCount }}<em>项</em></strong><p>其中待确认{{ summary.latestPendingRiskCount }}项</p></button>
          <button class="prototype-metric tone-orange" type="button" @click="setStatus('SKIPPED')"><span class="metric-glyph">跳</span><small>跳过邮件</small><strong>{{ summary.latestSkippedCount }}<em>封</em></strong><p>重复{{ summary.latestDuplicateCount }} · 不符合规则{{ summary.latestRuleMismatchCount }}</p></button>
          <button class="prototype-metric tone-red" type="button" @click="setStatus('FAILED')"><span class="metric-glyph">!</span><small>处理失败</small><strong>{{ summary.latestFailedCount }}<em>封</em></strong><p>历史批次待重试{{ summary.historicalFailedCount }}封</p></button>
        </section>

        <section class="prototype-panel sync-workspace">
          <header class="sync-tabs">
            <button type="button" :class="{ active: tab === 'mail' }" @click="tab = 'mail'">邮件明细 <b>{{ mailTotal }}</b></button>
            <button type="button" :class="{ active: tab === 'batch' }" @click="tab = 'batch'">同步批次 <b>{{ batchTotal }}</b></button>
            <span><i></i>增量同步使用邮件 UID 游标，避免重复处理</span>
          </header>

          <template v-if="tab === 'mail'">
            <div class="prototype-panel-heading">
              <div><p>MAIL PROCESSING DETAILS</p><h2>邮件处理明细</h2><span>共{{ mailTotal }}封邮件 · 包含{{ summary.historicalFailedCount }}封历史失败邮件</span></div>
              <button v-if="summary.historicalFailedCount" class="pending-reminder" type="button" @click="setStatus('FAILED')">! {{ summary.historicalFailedCount }}封历史邮件需要处理</button>
            </div>
            <div class="sync-filter-row">
              <input v-model="keyword" type="search" placeholder="搜索邮件主题、发件人或项目名称" aria-label="搜索邮件">
              <select v-model="status" aria-label="处理状态"><option value="all">全部状态</option><option value="COMPLETED">分析完成</option><option value="risk">含风险线索</option><option value="SKIPPED">已跳过</option><option value="FAILED">处理失败</option><option value="ANALYZING">分析中</option></select>
              <select v-model="batchId" aria-label="同步批次"><option value="">全部同步批次</option><option v-for="batch in batches" :key="batch.id" :value="batch.id">{{ batch.code }}</option></select>
              <button type="button" @click="resetFilters">重置</button>
            </div>
            <div v-if="mails.length" class="admin-table-scroll">
              <table class="admin-table mail-result-table"><thead><tr><th>处理状态</th><th>邮件主题 / 发件人</th><th>发送时间</th><th>匹配项目</th><th>风险线索</th><th>处理结果</th><th>操作</th></tr></thead>
                <tbody><tr v-for="mail in mails" :key="mail.id" :class="{ 'failed-row': mail.status === 'FAILED' }" @dblclick="openMessage(mail.id)">
                  <td><span class="result-status" :class="`is-${mail.status.toLowerCase()}`">{{ statusLabel(mail.status) }}</span></td>
                  <td><div class="mail-subject-cell"><strong>{{ mail.subject }}</strong><small>{{ mail.senderName || "未知发件人" }} &lt;{{ mail.senderAddress || "--" }}&gt;</small></div></td>
                  <td>{{ formatDate(mail.sentAt, false) }}</td>
                  <td><div class="mail-project-cell"><strong>{{ mail.projectMatches[0]?.projectName || "待匹配" }}</strong><em v-if="mail.projectMatches.length > 1">另{{ mail.projectMatches.length - 1 }}个项目</em><small>匹配置信度 {{ mail.projectMatches[0]?.confidence ?? "--" }}%</small></div></td>
                  <td><span class="risk-count" :class="{ zero: !mail.riskCandidateCount }"><strong>{{ mail.riskCandidateCount }}</strong><small>项</small></span></td>
                  <td><div class="process-result-cell"><strong>{{ mail.resultLabel }}</strong><small>{{ mail.resultNote }}</small></div></td>
                  <td><button class="row-link" type="button" :disabled="detailLoading" @click="openMessage(mail.id)">{{ mail.status === "FAILED" ? "重新处理" : "详情" }}</button></td>
                </tr></tbody>
              </table>
            </div>
            <div v-else class="sync-empty-state"><span>邮</span><strong>没有符合条件的邮件</strong><p>调整搜索或筛选条件后再试；系统不会生成虚假同步记录。</p><button type="button" @click="resetFilters">清除筛选</button></div>
            <footer class="prototype-pagination"><span>显示{{ mailRangeStart }}–{{ mailRangeEnd }}，共{{ mailTotal }}封邮件</span><div><button type="button" :disabled="page <= 1" @click="page--">上一页</button><b>{{ page }}</b><button type="button" :disabled="page >= totalPages" @click="page++">下一页</button></div></footer>
          </template>

          <template v-else>
            <div class="prototype-panel-heading"><div><p>SYNC BATCH HISTORY</p><h2>同步批次记录</h2><span>记录手动、定时同步和失败重试的完整执行情况</span></div></div>
            <div v-if="batches.length" class="admin-table-scroll"><table class="admin-table"><thead><tr><th>批次编号</th><th>触发方式</th><th>开始时间</th><th>执行用时</th><th>扫描 / 新增</th><th>风险线索</th><th>结果</th><th>操作</th></tr></thead><tbody><tr v-for="batch in batches" :key="batch.id"><td><strong>{{ batch.code }}</strong><small>{{ batch.errorSummary || "执行记录完整" }}</small></td><td>{{ triggerLabel(batch.trigger) }}</td><td>{{ formatDate(batch.startedAt || batch.createdAt) }}</td><td>{{ formatDuration(batch) }}</td><td>{{ batch.scannedCount }} / {{ batch.newCount }} 封</td><td>{{ batch.riskCandidateCount }}项</td><td><span class="result-status" :class="`is-${batch.status.toLowerCase()}`">{{ batchStatusLabel(batch.status) }}</span></td><td><button class="row-link" type="button" @click="openBatch(batch)">查看邮件</button></td></tr></tbody></table></div>
            <div v-else class="sync-empty-state"><span>批</span><strong>尚无同步批次</strong><p>完成邮箱配置后，点击“同步最新周报”开始首次增量同步。</p></div>
            <footer class="prototype-pagination"><span>共{{ batchTotal }}个同步批次</span><div><button type="button" :disabled="batchPage <= 1" @click="batchPage--">上一页</button><b>{{ batchPage }}</b><button type="button" :disabled="batchPage >= batchTotalPages" @click="batchPage++">下一页</button></div></footer>
          </template>
        </section>
      </template>
    </main>

    <ModalDialog v-if="selected" eyebrow="MAIL PROCESSING DETAIL" :title="selected.subject" @close="selected = null">
      <div class="mail-detail-summary detail-summary-grid">
        <div><span>处理状态</span><strong>{{ statusLabel(selected.status) }}</strong></div><div><span>同步批次</span><strong>{{ selected.batchCode }}</strong></div><div><span>发送时间</span><strong>{{ formatDate(selected.sentAt) }}</strong></div><div><span>处理时间</span><strong>{{ formatDate(selected.processedAt) }}</strong></div><div><span>风险线索</span><strong>{{ selected.riskCandidateCount }}项</strong></div>
      </div>
      <section v-if="selected.failureSummary" class="failure-detail"><h3>处理失败说明</h3><p>{{ selected.failureSummary }}</p></section>
      <section class="detail-section"><header><span>01</span><div><h3>邮件摘要与关键要点</h3><small>仅展示安全清洗后的内容，不保存或执行外部指令</small></div></header><p class="sanitized-summary">{{ selected.sanitizedSummary || "正文未提取到可展示摘要。" }}</p><ul class="mail-key-points"><li v-for="point in selected.keyPoints" :key="point">{{ point }}</li><li v-if="!selected.keyPoints.length">暂无关键要点</li></ul></section>
      <section class="detail-section"><header><span>02</span><div><h3>项目匹配结果</h3><small>依据标准项目清单、别名及责任人信息进行匹配</small></div></header><div class="match-list"><article v-for="match in selected.projectMatches" :key="match.id"><strong>{{ match.projectName }}</strong><span>{{ match.matchType }} · 命中文本“{{ match.matchedText }}”</span><em>置信度 {{ match.confidence }}%</em></article><p v-if="!selected.projectMatches.length">尚未匹配项目，等待人工确认。</p></div></section>
      <section class="detail-section"><header><span>03</span><div><h3>提取的风险线索</h3><small>AI 仅生成待确认线索，不会自动发布风险</small></div></header><div class="risk-detail-list"><article v-for="candidate in selected.riskCandidates" :key="candidate.id" class="risk-detail-card"><div class="risk-card-head"><div><span class="risk-level" :class="candidate.level.toLowerCase()">{{ candidate.levelLabel }}</span><h4>{{ candidate.categoryName }} · {{ candidate.projectName }}</h4></div><em>AI置信度 {{ candidate.confidence }}%</em></div><dl><div><dt>风险描述</dt><dd>{{ candidate.description }}</dd></div><div><dt>原文证据</dt><dd>{{ candidate.evidence }}</dd></div><div><dt>建议措施</dt><dd>{{ candidate.suggestion }}</dd></div><div><dt>处理状态</dt><dd>{{ candidate.status === "PENDING" ? "待风险管理员确认" : candidate.status === "CONFIRMED" ? "已确认并发布" : "已忽略" }}</dd></div></dl><footer v-if="candidate.status === 'PENDING'"><button type="button" :disabled="!!actionBusy" @click="ignoreCandidate(candidate)">忽略线索</button><button type="button" :disabled="!!actionBusy" @click="beginEdit(candidate)">调整后确认</button><button class="admin-primary-button" type="button" :disabled="!!actionBusy" @click="confirmCandidate(candidate)">确认并发布</button></footer><RouterLink v-else-if="candidate.confirmedRiskId" to="/">查看关联风险</RouterLink></article><p v-if="!selected.riskCandidates.length" class="failure-detail">该邮件未识别到新增风险，系统不会凭空创建风险数据。</p></div></section>
      <section class="detail-section"><header><span>04</span><div><h3>附件解析结果</h3><small>仅支持 TXT、DOCX、PDF、XLSX，单个附件上限 10MB</small></div></header><div class="attachment-list"><article v-for="file in selected.attachments" :key="file.name"><b>{{ file.type }}</b><span><strong>{{ file.name }}</strong><small>{{ attachmentSize(file.sizeBytes) }} · {{ file.summary || "无摘要" }}</small></span><em>{{ file.status === "PARSED" ? "解析完成" : file.status === "FAILED" ? "解析失败" : "已跳过" }}</em></article><p v-if="!selected.attachments.length">该邮件无附件，正文已完成安全清洗。</p></div></section>
      <section class="detail-section"><header><span>05</span><div><h3>处理轨迹</h3><small>展示读取、去重、解析、匹配和 AI 提取全过程</small></div></header><ol class="trace-list"><li v-for="trace in selected.processingTrace" :key="`${trace.stage}-${trace.occurredAt}`"><i :class="trace.status.toLowerCase()"></i><span><strong>{{ trace.stage }}</strong><small>{{ trace.detail }} · {{ formatDate(trace.occurredAt) }}</small></span></li><li v-if="!selected.processingTrace.length">暂无处理轨迹</li></ol></section>
      <template #footer><button type="button" @click="selected = null">关闭</button><button v-if="selected.status === 'FAILED'" class="admin-outline-button" type="button" :disabled="!!actionBusy" @click="retryMessage">重新处理</button><RouterLink class="admin-primary-button" to="/">查看关联风险</RouterLink></template>
    </ModalDialog>

    <ModalDialog v-if="editCandidate" eyebrow="RISK CANDIDATE REVIEW" title="调整风险线索" @close="editCandidate = null">
      <form class="prototype-form" @submit.prevent="saveCandidate"><label>关联项目<select v-model="editForm.projectId" required><option v-for="item in reviewOptions?.projects || []" :key="item.id" :value="item.id">{{ item.name }}</option></select></label><label>风险分类<select v-model="editForm.categoryId" required><option v-for="item in reviewOptions?.categories || []" :key="item.id" :value="item.id">{{ item.name }}</option></select></label><label>风险等级<select v-model="editForm.level" required><option v-for="item in reviewOptions?.levels || []" :key="item.value" :value="item.value">{{ item.label }}</option></select></label><label class="full">风险描述<textarea v-model="editForm.description" minlength="4" required></textarea></label><label class="full">原文证据<textarea v-model="editForm.evidence" minlength="2" required></textarea></label><label class="full">建议措施<textarea v-model="editForm.suggestion" minlength="2" required></textarea></label><div class="full edit-actions"><button type="button" @click="editCandidate = null">取消</button><button class="admin-primary-button" type="submit" :disabled="!!actionBusy">保存调整</button></div></form>
    </ModalDialog>

    <div v-if="syncing" class="sync-progress-dialog" role="status"><section><span class="large-sync-spinner">↻</span><p>MAIL SYNC IN PROGRESS</p><h2>正在增量同步周报邮件</h2><span>系统正在读取新 UID、执行去重、安全解析、项目匹配和 AI 风险提取，请勿重复提交。</span><div><i></i></div><strong>{{ summary.latestBatch ? batchStatusLabel(summary.latestBatch.status) : "创建同步批次" }}</strong></section></div>
    <p v-if="toast" class="prototype-toast">{{ toast }}</p>
  </div>
</template>

<style scoped>
.sync-error{padding:14px 18px;border:1px solid #f2b7ba;border-radius:13px;color:#b8323a;background:#fff0f1;font-weight:750}.unconfigured-state{display:grid;min-height:310px;padding:42px;place-items:center;text-align:center}.unconfigured-state>span{display:grid;width:64px;height:64px;border-radius:18px;place-items:center;color:#fff;background:linear-gradient(135deg,#247de5,#17aaa9);font-size:25px;font-weight:800}.unconfigured-state h2{margin:8px 0 0}.unconfigured-state p{max-width:640px;margin:0;color:#70899a;line-height:1.8}.result-status{display:inline-flex;padding:6px 9px;border-radius:9px;white-space:nowrap;font-size:12px;font-weight:800}.is-completed{color:#168c6e;background:#e6f7f1}.is-analyzing,.is-running,.is-queued{color:#176fc9;background:#e8f4ff}.is-skipped{color:#7b8994;background:#edf2f5}.is-failed,.is-partial{color:#d7464d;background:#ffebec}.mail-result-table{min-width:1120px}.mail-subject-cell,.mail-project-cell,.process-result-cell{display:grid;gap:5px}.mail-project-cell em{width:fit-content;padding:3px 7px;border-radius:7px;color:#176fc8;background:#eaf5ff;font-size:12px;font-style:normal}.risk-count{display:inline-flex;align-items:baseline;gap:3px;color:#df4b51}.risk-count strong{font-size:20px}.risk-count.zero{color:#8b9ba7}.row-link{padding:7px 10px;border:1px solid #d4e3ec;border-radius:8px;color:#176fc8;background:#fff;white-space:nowrap}.failed-row{background:#fffafa}.sync-empty-state{display:grid;min-height:250px;padding:34px;place-items:center;text-align:center}.sync-empty-state>span{display:grid;width:54px;height:54px;border-radius:16px;place-items:center;color:#176fc8;background:#eaf5ff;font-weight:800}.sync-empty-state strong{font-size:18px}.sync-empty-state p{margin:0;color:#8197a6}.sync-empty-state button{padding:8px 12px;border:1px solid #d4e3ec;border-radius:9px;color:#176fc8;background:#fff}.detail-summary-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px}.detail-summary-grid>div{display:grid;gap:5px}.detail-summary-grid span,.detail-section header small{color:#8198a8;font-size:12px}.failure-detail{margin:16px 0;padding:14px;border-left:4px solid #ec5358;border-radius:10px;color:#8d3b3f;background:#fff1f1}.failure-detail h3,.failure-detail p{margin:0}.failure-detail p{margin-top:7px;line-height:1.7}.detail-section{padding:20px 0;border-bottom:1px solid #e1ebf1}.detail-section>header{display:flex;margin-bottom:14px;align-items:center;gap:10px}.detail-section>header>span{display:grid;width:36px;height:36px;border-radius:10px;place-items:center;color:#176fc8;background:#e8f4ff;font-weight:800}.detail-section header h3{margin:0 0 3px}.sanitized-summary{padding:13px;border-radius:11px;background:#f4f9fc;line-height:1.75}.mail-key-points{display:grid;padding-left:22px;gap:8px;line-height:1.65}.match-list{display:grid;gap:9px}.match-list article{display:grid;padding:13px;border-radius:11px;grid-template-columns:minmax(0,1fr) minmax(0,1fr) auto;align-items:center;gap:12px;background:#f5f9fc}.match-list span{color:#718a9b}.match-list em{color:#168e72;font-style:normal;font-weight:750}.risk-detail-list{display:grid;gap:12px}.risk-detail-card{padding:16px;border:1px solid #dce8f0;border-radius:14px}.risk-card-head{display:flex;align-items:center;justify-content:space-between;gap:12px}.risk-card-head>div{display:flex;align-items:center;gap:10px}.risk-card-head h4{margin:0}.risk-card-head>em{color:#1770c9;font-style:normal}.risk-level{padding:5px 8px;border-radius:8px;font-size:12px;font-weight:800}.risk-level.high{color:#d94148;background:#ffebec}.risk-level.medium{color:#c67600;background:#fff0d8}.risk-level.low{color:#14876b;background:#e4f5ef}.risk-level.unknown{color:#61788a;background:#edf2f5}.risk-detail-card dl{display:grid;margin:14px 0}.risk-detail-card dl>div{display:grid;padding:9px 0;grid-template-columns:90px 1fr;border-bottom:1px solid #e4edf2;gap:10px}.risk-detail-card dt{color:#8197a6}.risk-detail-card dd{margin:0;line-height:1.65}.risk-detail-card footer{display:flex;justify-content:flex-end;gap:8px}.risk-detail-card footer button:not(.admin-primary-button),.risk-detail-card>a{padding:8px 11px;border:1px solid #d5e3ec;border-radius:9px;color:#176fc8;background:#fff;text-decoration:none}.attachment-list{display:grid;gap:9px}.attachment-list article{display:flex;padding:12px;border-radius:11px;align-items:center;gap:10px;background:#f5f9fc}.attachment-list b{display:grid;width:48px;height:38px;border-radius:9px;place-items:center;color:#176fc8;background:#e5f2ff;font-size:12px}.attachment-list span{display:grid;flex:1;gap:4px}.attachment-list small{color:#8196a5}.attachment-list em{font-style:normal}.trace-list{display:grid;margin:0;padding:0;list-style:none;gap:11px}.trace-list li{display:flex;align-items:flex-start;gap:10px}.trace-list i{width:11px;height:11px;margin-top:4px;border-radius:50%;background:#20ab87;box-shadow:0 0 0 4px #e3f6f0}.trace-list i.failed{background:#e65359;box-shadow:0 0 0 4px #ffe8e9}.trace-list i.running{background:#2781de;box-shadow:0 0 0 4px #e7f2ff}.trace-list span{display:grid;gap:4px}.trace-list small{color:#8197a6}.edit-actions{display:flex;justify-content:flex-end;gap:10px}.edit-actions>button:first-child{padding:0 16px;border:1px solid #d4e3ec;border-radius:11px;background:#fff}.sync-progress-dialog{position:fixed;z-index:120;inset:0;display:grid;padding:20px;place-items:center;background:rgba(19,49,70,.46);backdrop-filter:blur(5px)}.sync-progress-dialog>section{display:grid;width:min(520px,100%);padding:38px;border-radius:22px;place-items:center;text-align:center;background:#fff;box-shadow:0 24px 80px rgba(16,48,70,.3)}.large-sync-spinner{display:grid;width:62px;height:62px;border-radius:50%;place-items:center;color:#fff;background:linear-gradient(135deg,#237ce4,#18aaa9);font-size:28px;animation:spin 1s linear infinite}.sync-progress-dialog p{margin:16px 0 5px;color:#1773ce;font-size:12px;font-weight:800;letter-spacing:.13em}.sync-progress-dialog h2{margin:0}.sync-progress-dialog section>span:not(.large-sync-spinner){margin-top:10px;color:#708a9b;line-height:1.7}.sync-progress-dialog section>div{width:100%;height:7px;margin:22px 0 12px;border-radius:99px;overflow:hidden;background:#e4edf2}.sync-progress-dialog section>div i{display:block;width:45%;height:100%;border-radius:99px;background:linear-gradient(90deg,#1d76df,#19aaa9);animation:progress 1.5s ease-in-out infinite}.sync-progress-dialog strong{color:#176fc8}@keyframes spin{to{transform:rotate(360deg)}}@keyframes progress{0%{transform:translateX(-110%)}100%{transform:translateX(240%)}}
.is-success{color:#168c6e;background:#e6f7f1}.is-failure{color:#d7464d;background:#ffebec}
@media(max-width:760px){.detail-summary-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.match-list article{grid-template-columns:1fr}.risk-card-head,.risk-detail-card footer{align-items:flex-start;flex-direction:column}.risk-detail-card footer button{width:100%}.risk-detail-card dl>div{grid-template-columns:1fr}.sync-error{font-size:13px}}
</style>
