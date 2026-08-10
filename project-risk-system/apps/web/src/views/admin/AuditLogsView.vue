<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";

import type {
  AuditActionGroup,
  AuditDateRange,
  AuditExportFormat,
  AuditLogDetail,
  AuditLogIntegrity,
  AuditLogListItem,
  AuditLogOptions,
  AuditLogResult,
  AuditLogSummary,
  AuditModuleKey,
} from "@risk-platform/contracts";

import { auditLogsApi, type AuditLogFilters } from "@/api/audit-logs";
import AdminShell from "@/components/AdminShell.vue";
import ModalDialog from "@/components/ModalDialog.vue";
import { useAuthStore } from "@/stores/auth";

const auth = useAuthStore();
const search = ref("");
const moduleFilter = ref<AuditModuleKey>("ALL");
const actionFilter = ref<AuditActionGroup>("ALL");
const resultFilter = ref<AuditLogResult | "ALL">("ALL");
const dateRange = ref<AuditDateRange>("TODAY");
const startDate = ref(localDate(new Date()));
const endDate = ref(localDate(new Date()));
const sensitiveOnly = ref(false);
const page = ref(1);
const pageSize = 10;
const total = ref(0);
const logs = ref<AuditLogListItem[]>([]);
const selected = ref<AuditLogDetail | null>(null);
const exportOpen = ref(false);
const exportReason = ref("");
const exportFormat = ref<AuditExportFormat>("XLSX");
const loading = ref(false);
const detailLoading = ref(false);
const exporting = ref(false);
const error = ref("");
const toast = ref("");
const summary = ref<AuditLogSummary>({
  todayCount: 0,
  yesterdayCount: 0,
  dayChange: 0,
  failedCount: 0,
  sensitiveCount: 0,
  activeActorCount: 0,
  systemAdminActorCount: 0,
});
const options = ref<AuditLogOptions>({ modules: [], actions: [] });
const integrity = ref<AuditLogIntegrity>({
  status: "VALID",
  totalRecords: 0,
  verifiedRecords: 0,
  firstBrokenEventId: null,
  lastVerifiedAt: new Date(0).toISOString(),
  appendOnly: true,
});

const canExport = computed(
  () => auth.user?.permissions.includes("admin.audit.export") ?? false,
);
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)));
const displayStart = computed(() => (total.value ? (page.value - 1) * pageSize + 1 : 0));
const displayEnd = computed(() => Math.min(page.value * pageSize, total.value));
const dayChangeText = computed(() => {
  const value = summary.value.dayChange;
  return `较昨日 ${value >= 0 ? "+" : ""}${value}`;
});

function localDate(value: Date): string {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function currentFilters(): AuditLogFilters {
  return {
    keyword: search.value.trim() || undefined,
    module: moduleFilter.value,
    action: actionFilter.value,
    result: resultFilter.value === "ALL" ? undefined : resultFilter.value,
    dateRange: dateRange.value,
    startDate: dateRange.value === "CUSTOM" ? startDate.value : undefined,
    endDate: dateRange.value === "CUSTOM" ? endDate.value : undefined,
    sensitiveOnly: sensitiveOnly.value,
    page: page.value,
    pageSize,
  };
}

async function loadList(): Promise<void> {
  loading.value = true;
  error.value = "";
  try {
    const response = await auditLogsApi.list(currentFilters());
    logs.value = response.items;
    total.value = response.total;
    if (page.value > totalPages.value) {
      page.value = totalPages.value;
      await loadList();
    }
  } catch (requestError) {
    error.value = requestError instanceof Error ? requestError.message : "审计日志加载失败";
  } finally {
    loading.value = false;
  }
}

async function loadHeader(): Promise<void> {
  try {
    [summary.value, options.value, integrity.value] = await Promise.all([
      auditLogsApi.summary(),
      auditLogsApi.options(),
      auditLogsApi.integrity(),
    ]);
  } catch (requestError) {
    error.value = requestError instanceof Error ? requestError.message : "审计汇总加载失败";
  }
}

async function refreshAll(): Promise<void> {
  await Promise.all([loadHeader(), loadList()]);
  showToast("审计日志已刷新，当前为最新记录。");
}

function reset(): void {
  search.value = "";
  moduleFilter.value = "ALL";
  actionFilter.value = "ALL";
  resultFilter.value = "ALL";
  dateRange.value = "TODAY";
  startDate.value = localDate(new Date());
  endDate.value = localDate(new Date());
  sensitiveOnly.value = false;
  page.value = 1;
  void loadList();
}

function filterFailed(): void {
  resultFilter.value = "FAILURE";
  sensitiveOnly.value = false;
  page.value = 1;
}

function filterSensitive(): void {
  resultFilter.value = "ALL";
  sensitiveOnly.value = true;
  page.value = 1;
}

function filterAll(): void {
  resultFilter.value = "ALL";
  sensitiveOnly.value = false;
  page.value = 1;
}

async function openDetail(item: AuditLogListItem): Promise<void> {
  detailLoading.value = true;
  error.value = "";
  try {
    selected.value = await auditLogsApi.detail(item.id);
  } catch (requestError) {
    error.value = requestError instanceof Error ? requestError.message : "审计事件详情加载失败";
  } finally {
    detailLoading.value = false;
  }
}

async function copyValue(value: string | null, label: string): Promise<void> {
  if (!value) return;
  try {
    await navigator.clipboard.writeText(value);
    showToast(`${label}已复制。`);
  } catch {
    showToast(`${label}：${value}`);
  }
}

async function exportLogs(): Promise<void> {
  if (!canExport.value || exportReason.value.trim().length < 4) return;
  exporting.value = true;
  error.value = "";
  try {
    const fileName = await auditLogsApi.export(
      currentFilters(),
      exportFormat.value,
      exportReason.value.trim(),
    );
    exportOpen.value = false;
    exportReason.value = "";
    showToast(`已生成 ${fileName}，导出行为已写入审计记录。`);
    await Promise.all([loadHeader(), loadList()]);
  } catch (requestError) {
    error.value = requestError instanceof Error ? requestError.message : "审计日志导出失败";
  } finally {
    exporting.value = false;
  }
}

function showToast(message: string): void {
  toast.value = message;
  window.setTimeout(() => {
    if (toast.value === message) toast.value = "";
  }, 2600);
}

function formatTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function snapshotJson(value: Record<string, unknown> | null): string {
  return value ? JSON.stringify(value, null, 2) : "无快照";
}

let searchTimer = 0;
watch(search, () => {
  window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => {
    page.value = 1;
    void loadList();
  }, 350);
});
watch([moduleFilter, actionFilter, resultFilter, sensitiveOnly], () => {
  page.value = 1;
  void loadList();
});
watch(dateRange, () => {
  page.value = 1;
  if (dateRange.value !== "CUSTOM") void loadList();
});

onMounted(async () => {
  await Promise.all([loadHeader(), loadList()]);
});
</script>

<template>
  <AdminShell>
    <section class="admin-page-heading prototype-heading">
      <div>
        <p class="admin-eyebrow">SECURITY AUDIT &amp; TRACEABILITY</p>
        <h1>审计日志</h1>
        <p>统一追踪登录、权限、邮箱、AI服务、风险、Excel导入和系统配置操作，支持按授权范围查询与导出。</p>
      </div>
      <div class="prototype-heading-actions">
        <button class="admin-outline-button" type="button" :disabled="loading" @click="refreshAll">↻ 刷新日志</button>
        <button
          class="admin-primary-button"
          type="button"
          :disabled="!canExport"
          :title="canExport ? '导出当前筛选结果' : '当前账号只有查看权限'"
          @click="exportOpen = true"
        >导出筛选结果</button>
      </div>
    </section>

    <p v-if="error" class="audit-error" role="alert">{{ error }}</p>

    <section class="prototype-security-banner">
      <span>盾</span>
      <div>
        <strong>审计记录只追加，不允许修改或删除</strong>
        <p>密码、邮箱授权码、API Key及无关Excel内容不会写入日志；变更前后仅保存脱敏摘要。</p>
      </div>
      <em class="status-ok" :class="{ 'status-invalid': integrity.status === 'INVALID' }">
        <i></i>{{ integrity.status === "VALID" ? "完整性校验正常" : "完整性校验异常" }}
      </em>
    </section>

    <section class="prototype-metric-grid">
      <button class="prototype-metric tone-blue" type="button" @click="filterAll">
        <span class="metric-glyph">今</span><small>今日操作</small>
        <strong>{{ summary.todayCount }}<em>条</em></strong><p>{{ dayChangeText }}</p>
      </button>
      <button class="prototype-metric tone-red" type="button" @click="filterFailed">
        <span class="metric-glyph">!</span><small>失败记录</small>
        <strong>{{ summary.failedCount }}<em>条</em></strong><p>均已记录失败原因</p>
      </button>
      <button class="prototype-metric tone-orange" type="button" @click="filterSensitive">
        <span class="metric-glyph">敏</span><small>敏感操作</small>
        <strong>{{ summary.sensitiveCount }}<em>条</em></strong><p>权限、密钥与回滚</p>
      </button>
      <button class="prototype-metric tone-green" type="button" @click="filterAll">
        <span class="metric-glyph">人</span><small>活跃操作人</small>
        <strong>{{ summary.activeActorCount }}<em>人</em></strong><p>系统管理员 {{ summary.systemAdminActorCount }}人</p>
      </button>
    </section>

    <section class="prototype-panel">
      <header class="prototype-panel-heading">
        <div>
          <p>AUDIT EVENT STREAM</p><h2>操作记录</h2>
          <span>当前筛选共 {{ total }} 条真实记录</span>
        </div>
        <div class="audit-retention-note">
          <strong>日志保留策略</strong><small>首版长期保留 · 只追加存储</small>
        </div>
      </header>

      <div class="audit-filter-grid">
        <label><span>搜索</span><input v-model="search" type="search" placeholder="操作人、操作、资源ID或Trace ID"></label>
        <label><span>模块</span><select v-model="moduleFilter"><option value="ALL">全部模块</option><option v-for="item in options.modules" :key="item.value" :value="item.value">{{ item.label }}（{{ item.count }}）</option></select></label>
        <label><span>操作类型</span><select v-model="actionFilter"><option value="ALL">全部操作</option><option v-for="item in options.actions" :key="item.value" :value="item.value">{{ item.label }}（{{ item.count }}）</option></select></label>
        <label><span>执行结果</span><select v-model="resultFilter"><option value="ALL">全部结果</option><option value="SUCCESS">成功</option><option value="FAILURE">失败</option></select></label>
        <label><span>日期范围</span><select v-model="dateRange"><option value="TODAY">今天</option><option value="7_DAYS">近7天</option><option value="30_DAYS">近30天</option><option value="CUSTOM">自定义</option></select></label>
        <button type="button" @click="reset">重置</button>
      </div>
      <div v-if="dateRange === 'CUSTOM'" class="audit-custom-date">
        <label><span>开始日期</span><input v-model="startDate" type="date"></label>
        <label><span>结束日期</span><input v-model="endDate" type="date"></label>
        <button class="admin-outline-button" type="button" @click="page = 1; loadList()">应用时间范围</button>
      </div>
      <div class="audit-query-scope">
        <span>{{ sensitiveOnly ? "敏感操作 · " : "" }}{{ dateRange === "TODAY" ? "今天" : dateRange === "7_DAYS" ? "近7天" : dateRange === "30_DAYS" ? "近30天" : `${startDate} 至 ${endDate}` }}</span>
        <small>查询权限：admin.audit.view</small>
      </div>

      <div class="admin-table-scroll">
        <table class="admin-table audit-table">
          <thead><tr><th>时间 / 事件ID</th><th>模块 / 操作</th><th>操作人</th><th>资源与摘要</th><th>客户端</th><th>结果</th><th>详情</th></tr></thead>
          <tbody>
            <tr v-for="item in logs" :key="item.id">
              <td><strong>{{ formatTime(item.createdAt) }}</strong><small>{{ item.eventId }}</small></td>
              <td><strong>{{ item.moduleLabel }}</strong><small>{{ item.actionLabel }} <em v-if="item.isSensitive" class="sensitive-tag">敏感</em></small></td>
              <td><strong>{{ item.actorName }}</strong><small>{{ item.actorRole || item.actorAccount || "系统任务" }}</small></td>
              <td><strong>{{ item.resourceLabel }}</strong><small>{{ item.summary }}</small></td>
              <td><strong>{{ item.clientIp }}</strong><small>{{ item.client }}</small></td>
              <td><span class="status-pill" :class="item.result === 'SUCCESS' ? 'status-active' : 'status-disabled'">{{ item.result === "SUCCESS" ? "成功" : "失败" }}</span><small v-if="item.errorCode">{{ item.errorCode }}</small></td>
              <td><button type="button" :disabled="detailLoading" @click="openDetail(item)">查看</button></td>
            </tr>
            <tr v-if="!loading && !logs.length"><td colspan="7" class="prototype-empty">没有符合筛选条件的审计记录。</td></tr>
            <tr v-if="loading"><td colspan="7" class="prototype-empty">正在读取审计日志…</td></tr>
          </tbody>
        </table>
      </div>
      <footer class="prototype-pagination">
        <span>显示 {{ displayStart }}–{{ displayEnd }}，共{{ total }}条</span>
        <div><button type="button" :disabled="page <= 1 || loading" @click="page -= 1; loadList()">上一页</button><b>{{ page }} / {{ totalPages }}</b><button type="button" :disabled="page >= totalPages || loading" @click="page += 1; loadList()">下一页</button></div>
      </footer>
    </section>

    <ModalDialog v-if="selected" eyebrow="AUDIT EVENT DETAIL" title="审计事件详情" @close="selected = null">
      <div class="audit-detail-hero" :class="{ failed: selected.result === 'FAILURE' }">
        <span>{{ selected.result === "SUCCESS" ? "✓" : "!" }}</span>
        <div><strong>{{ selected.actionLabel }}</strong><p>{{ selected.moduleLabel }} · {{ formatTime(selected.createdAt) }}</p></div>
        <em>{{ selected.result === "SUCCESS" ? "成功" : "失败" }}</em>
      </div>
      <dl class="prototype-detail-list">
        <div><dt>事件编号</dt><dd>{{ selected.eventId }}</dd></div>
        <div><dt>操作人 / 角色</dt><dd>{{ selected.actorName }} / {{ selected.actorRole || "系统任务" }}（{{ selected.actorAccount || "无账号" }}）</dd></div>
        <div><dt>客户端</dt><dd>{{ selected.clientIp }} · {{ selected.client }}</dd></div>
        <div><dt>资源</dt><dd><span>{{ selected.resourceLabel }}</span><button type="button" @click="copyValue(selected.resourceId, '资源ID')">复制资源ID</button></dd></div>
        <div><dt>Trace ID</dt><dd><span>{{ selected.traceId }}</span><button type="button" @click="copyValue(selected.traceId, 'Trace ID')">复制</button></dd></div>
        <div><dt>变更前摘要</dt><dd>{{ selected.beforeSummary }}</dd></div>
        <div><dt>变更后摘要</dt><dd>{{ selected.afterSummary }}</dd></div>
        <div><dt>执行说明</dt><dd>{{ selected.context }}</dd></div>
        <div><dt>完整性摘要</dt><dd>{{ selected.integrityHash ? `SHA-256 · ${selected.integrityHash.slice(0, 16)}…` : "历史记录未生成摘要" }}</dd></div>
      </dl>
      <details class="audit-snapshot"><summary>查看脱敏前后快照</summary><div><section><h3>变更前</h3><pre>{{ snapshotJson(selected.beforeSnapshot) }}</pre></section><section><h3>变更后</h3><pre>{{ snapshotJson(selected.afterSnapshot) }}</pre></section></div></details>
    </ModalDialog>

    <ModalDialog v-if="exportOpen" eyebrow="CONTROLLED EXPORT" title="导出审计日志" @close="exportOpen = false">
      <div class="export-scope-summary"><strong>当前筛选结果：{{ total }}条</strong><p>导出文件不包含密码、授权码、API Key、邮件正文或未脱敏快照。</p></div>
      <div class="export-options">
        <label><input v-model="exportFormat" type="radio" value="XLSX" name="format"> Excel（.xlsx）</label>
        <label><input v-model="exportFormat" type="radio" value="CSV" name="format"> CSV（.csv）</label>
      </div>
      <label class="export-reason"><span>导出原因 *</span><textarea v-model="exportReason" maxlength="200" placeholder="请输入本次导出的业务用途，内容将写入审计记录"></textarea><small>{{ exportReason.length }} / 200，至少4个字符</small></label>
      <p class="modal-copy">导出内容按当前筛选条件生成，敏感字段保持脱敏；导出动作本身会追加一条不可修改的审计记录。</p>
      <template #footer><button type="button" :disabled="exporting" @click="exportOpen = false">取消</button><button class="admin-primary-button" type="button" :disabled="exportReason.trim().length < 4 || exporting" @click="exportLogs">{{ exporting ? "正在生成…" : "确认导出" }}</button></template>
    </ModalDialog>

    <p v-if="toast" class="prototype-toast">{{ toast }}</p>
  </AdminShell>
</template>

<style scoped>
.audit-error{margin:16px 0;padding:13px 16px;border:1px solid #f3b9bd;border-radius:12px;color:#bc3038;background:#fff0f1;font-weight:700}.status-invalid{color:#c83d45}.status-invalid i{background:#e34b52}.audit-retention-note{display:grid;text-align:right;color:#668397}.audit-retention-note small{margin-top:4px;color:#8fa4b2}.audit-custom-date{display:flex;padding:0 18px 18px;align-items:flex-end;gap:12px;background:#f6fafd}.audit-custom-date label{display:grid;gap:7px}.audit-custom-date label span{color:#71899b;font-size:12px;font-weight:700}.audit-custom-date input{min-height:44px;padding:0 14px;border:1px solid #d3e2ec;border-radius:11px;background:#fff}.audit-query-scope{display:flex;padding:10px 18px;border-top:1px solid #e3edf3;justify-content:space-between;color:#6f889a;background:#fbfdfe}.audit-table{min-width:1120px}.audit-table td{line-height:1.55}.audit-table td:nth-child(4){min-width:300px}.audit-table td button,.prototype-detail-list dd button{padding:6px 9px;border:1px solid #d3e2eb;border-radius:8px;color:#176fc8;background:#fff}.audit-detail-hero.failed>span{background:#e65359}.audit-detail-hero.failed>em{color:#d23b43}.prototype-detail-list dd{line-height:1.7}.prototype-detail-list dd button{margin-left:10px}.audit-snapshot{margin-top:16px;border:1px solid #dce8ef;border-radius:12px;overflow:hidden}.audit-snapshot summary{padding:13px 15px;color:#176fc8;background:#f5f9fc;cursor:pointer;font-weight:700}.audit-snapshot>div{display:grid;padding:14px;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.audit-snapshot section{min-width:0}.audit-snapshot h3{font-size:14px}.audit-snapshot pre{max-height:240px;margin:0;padding:12px;border-radius:10px;overflow:auto;white-space:pre-wrap;word-break:break-all;background:#15394f;color:#eaf6fc;font:12px/1.7 ui-monospace,SFMono-Regular,Menlo,monospace}.export-scope-summary{margin-bottom:16px;padding:14px;border-radius:12px;background:#f4f9fc}.export-scope-summary p{margin:6px 0 0;color:#728a9b;line-height:1.7}.export-reason small{color:#8ba0ae}.export-reason textarea{font:inherit}.prototype-pagination button:disabled{opacity:.45}.prototype-pagination b{font-weight:700}
@media(max-width:760px){.audit-retention-note{display:none}.audit-custom-date{align-items:stretch;flex-direction:column}.audit-custom-date label,.audit-custom-date button{width:100%}.audit-query-scope{align-items:flex-start;flex-direction:column;gap:5px}.audit-snapshot>div{grid-template-columns:1fr}.export-options{flex-direction:column}}
</style>
