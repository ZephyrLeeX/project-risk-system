<script setup lang="ts">
import type {
  ImportBatchStatus,
  LegalMatterMatchStatus,
  ProjectImportBatchDetail,
  ProjectImportBatchSummary,
  ProjectOption,
  ProjectRiskLevel,
  SupplementalCollectionRowItem,
  SupplementalMatchStatus,
} from "@risk-platform/contracts";
import { computed, onMounted, ref } from "vue";

import { ApiError } from "@/api/http";
import { projectImportApi } from "@/api/imports";
import AdminShell from "@/components/AdminShell.vue";
import ModalDialog from "@/components/ModalDialog.vue";

const selectedFile = ref<File | null>(null);
const currentBatch = ref<ProjectImportBatchDetail | null>(null);
const batches = ref<ProjectImportBatchSummary[]>([]);
const loading = ref(false);
const historyLoading = ref(true);
const acknowledgeWarnings = ref(false);
const errorMessage = ref("");
const fileInput = ref<HTMLInputElement | null>(null);
const projectOptions = ref<ProjectOption[]>([]);
const matchingRow = ref<SupplementalCollectionRowItem | null>(null);
const matchProjectId = ref("");
const matchLoading = ref(false);
const activeImportTab = ref<"preview" | "pending" | "rules">("preview");
const activeSheet = ref<"main" | "supplemental" | "legal" | "summary">(
  "main",
);
const batchMenuOpen = ref(false);
const noticeMessage = ref("");
const rollbackCandidate = ref<ProjectImportBatchSummary | null>(null);
const unmatchConfirming = ref(false);
const uploadDrawerOpen = ref(false);
const publishConfirmOpen = ref(false);
const historyKeyword = ref("");
const historyStatus = ref<"" | ImportBatchStatus>("");
const historyPage = ref(1);
const selectedHistoryBatch = ref<ProjectImportBatchSummary | null>(null);
const historyPageSize = 5;

const latestImportedBatch = computed(() =>
  batches.value.find((batch) => batch.status === "IMPORTED"),
);
const importedBatchCount = computed(
  () => batches.value.filter((batch) => batch.status === "IMPORTED").length,
);
const monthImportedBatchCount = computed(() => {
  const now = new Date();
  return batches.value.filter((batch) => {
    if (batch.status !== "IMPORTED" || !batch.confirmedAt) return false;
    const date = new Date(batch.confirmedAt);
    return (
      date.getFullYear() === now.getFullYear() &&
      date.getMonth() === now.getMonth()
    );
  }).length;
});
const pendingMatchCount = computed(() => {
  const batch = currentBatch.value;
  if (!batch) return 0;
  return (
    batch.supplementalUnmatchedRows +
    batch.supplementalAmbiguousRows +
    batch.legalUnmatchedRows +
    batch.legalAmbiguousRows
  );
});
const effectiveProjectCount = computed(() => {
  const rows = currentBatch.value?.rows ?? [];
  return new Set(
    rows.map((row) =>
      row.externalCode
        ? `CODE:${row.externalCode.normalize("NFKC").trim().toLocaleLowerCase("zh-CN")}`
        : `ROW:${row.id}`,
    ),
  ).size;
});
const parsedRecordCount = computed(() => {
  const batch = currentBatch.value;
  return batch
    ? batch.totalRows + batch.supplementalTotalRows + batch.legalTotalRows
    : 0;
});
const aggregateWarningCount = computed(() => {
  const batch = currentBatch.value;
  return batch
    ? batch.warningRows +
        batch.supplementalWarningRows +
        batch.legalWarningRows
    : 0;
});
const aggregateErrorCount = computed(() => {
  const batch = currentBatch.value;
  return batch
    ? batch.errorRows + batch.supplementalErrorRows + batch.legalErrorRows
    : 0;
});
const filteredBatches = computed(() => batches.value.filter((batch) =>
  (!historyStatus.value || batch.status === historyStatus.value) &&
  (!historyKeyword.value.trim() ||
    `${batch.fileName}${batch.uploadedByName}${batch.id}`
      .toLowerCase()
      .includes(historyKeyword.value.trim().toLowerCase())),
));
const historyTotalPages = computed(() =>
  Math.max(1, Math.ceil(filteredBatches.value.length / historyPageSize)),
);
const pagedBatches = computed(() =>
  filteredBatches.value.slice(
    (historyPage.value - 1) * historyPageSize,
    historyPage.value * historyPageSize,
  ),
);

const canConfirm = computed(
  () =>
    currentBatch.value?.status === "PREVIEWED" &&
    currentBatch.value.errorRows === 0 &&
    currentBatch.value.supplementalErrorRows === 0 &&
    currentBatch.value.legalErrorRows === 0 &&
    (currentBatch.value.warningRows +
        currentBatch.value.supplementalWarningRows +
        currentBatch.value.legalWarningRows ===
        0 ||
      acknowledgeWarnings.value),
);

onMounted(async () => {
  await loadBatches();
});

function chooseFile(): void {
  batchMenuOpen.value = false;
  noticeMessage.value = "";
  fileInput.value?.click();
}

function openUploadDrawer(): void {
  selectedFile.value = null;
  noticeMessage.value = "";
  errorMessage.value = "";
  uploadDrawerOpen.value = true;
}

function downloadImportGuide(): void {
  const content = [
    "项目风险管理平台 Excel 导入说明",
    "1. 文件格式必须为 .xlsx，大小不超过 20MB。",
    "2. 主工作表名称必须为“数据回款”。",
    "3. 可选工作表为“涵谷回款”和“发函-诉讼清单”。",
    "4. 上传后先预检，确认警告后才会事务入库。",
    "5. 同一文件中重复项目编码会合并更新同一项目。",
  ].join("\n");
  const url = URL.createObjectURL(
    new Blob([content], { type: "text/plain;charset=utf-8" }),
  );
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "项目数据导入说明.txt";
  anchor.style.display = "none";
  document.body.appendChild(anchor);
  anchor.click();
  window.setTimeout(() => {
    anchor.remove();
    URL.revokeObjectURL(url);
  }, 1_000);
}

function scrollToImportSection(tab: "preview" | "pending" | "rules"): void {
  activeImportTab.value = tab;
  document
    .getElementById(`import-${tab}-section`)
    ?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function showSheet(
  sheet: "main" | "supplemental" | "legal" | "summary",
): void {
  activeSheet.value = sheet;
  const target = {
    main: "import-preview-section",
    supplemental: "sheet-supplemental-section",
    legal: "sheet-legal-section",
    summary: "import-rules-section",
  }[sheet];
  document
    .getElementById(target)
    ?.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function downloadCurrentSource(): Promise<void> {
  if (!currentBatch.value) return;
  loading.value = true;
  errorMessage.value = "";
  noticeMessage.value = "";
  batchMenuOpen.value = false;
  try {
    const fileName = await projectImportApi.downloadSource(
      currentBatch.value.id,
    );
    noticeMessage.value = `已下载源文件：${fileName}`;
  } catch (error) {
    errorMessage.value = getErrorMessage(error);
  } finally {
    loading.value = false;
  }
}

function saveCurrentDraft(): void {
  batchMenuOpen.value = false;
  noticeMessage.value = "当前批次的预检结果已自动保存，可从导入历史继续处理。";
}

function onFileChange(event: Event): void {
  const target = event.target as HTMLInputElement;
  selectedFile.value = target.files?.[0] ?? null;
  currentBatch.value = null;
  acknowledgeWarnings.value = false;
  errorMessage.value = "";
  noticeMessage.value = selectedFile.value
    ? `已选择文件：${selectedFile.value.name}`
    : "";
}

function onDrop(event: DragEvent): void {
  selectedFile.value = event.dataTransfer?.files?.[0] ?? null;
  currentBatch.value = null;
  acknowledgeWarnings.value = false;
  errorMessage.value = "";
  noticeMessage.value = selectedFile.value
    ? `已选择文件：${selectedFile.value.name}`
    : "";
}

async function preview(): Promise<void> {
  if (!selectedFile.value) return;
  loading.value = true;
  errorMessage.value = "";
  try {
    currentBatch.value = await projectImportApi.preview(
      selectedFile.value,
    );
    uploadDrawerOpen.value = false;
    acknowledgeWarnings.value = false;
    await loadBatches();
  } catch (error) {
    errorMessage.value = getErrorMessage(error);
  } finally {
    loading.value = false;
  }
}

async function confirmImport(): Promise<void> {
  if (!currentBatch.value || !canConfirm.value) return;
  loading.value = true;
  errorMessage.value = "";
  try {
    currentBatch.value = await projectImportApi.confirm(
      currentBatch.value.id,
      { acknowledgeWarnings: acknowledgeWarnings.value },
    );
    publishConfirmOpen.value = false;
    await loadBatches();
  } catch (error) {
    errorMessage.value = getErrorMessage(error);
  } finally {
    loading.value = false;
  }
}

function downloadBatchErrors(batch: ProjectImportBatchSummary): void {
  const content = `批次：${batch.id}\n文件：${batch.fileName}\n状态：${statusLabel(batch.status)}\n请进入批次详情查看解析和校验错误。`;
  const url = URL.createObjectURL(
    new Blob([content], { type: "text/plain;charset=utf-8" }),
  );
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${batch.fileName}-错误明细.txt`;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function openBatch(id: string): Promise<void> {
  loading.value = true;
  errorMessage.value = "";
  try {
    currentBatch.value = await projectImportApi.detail(id);
    selectedFile.value = null;
    acknowledgeWarnings.value = false;
    window.scrollTo({ top: 0, behavior: "smooth" });
  } catch (error) {
    errorMessage.value = getErrorMessage(error);
  } finally {
    loading.value = false;
  }
}

function requestRollback(batch: ProjectImportBatchSummary): void {
  batchMenuOpen.value = false;
  rollbackCandidate.value = batch;
}

async function confirmRollback(): Promise<void> {
  if (!rollbackCandidate.value) return;
  const batch = rollbackCandidate.value;
  loading.value = true;
  errorMessage.value = "";
  try {
    currentBatch.value = await projectImportApi.rollback(batch.id);
    rollbackCandidate.value = null;
    await loadBatches();
  } catch (error) {
    errorMessage.value = getErrorMessage(error);
  } finally {
    loading.value = false;
  }
}

async function loadBatches(): Promise<void> {
  historyLoading.value = true;
  try {
    batches.value = (await projectImportApi.batches()).items;
    if (!currentBatch.value && batches.value[0]) {
      currentBatch.value = await projectImportApi.detail(batches.value[0].id);
    }
  } catch (error) {
    errorMessage.value = getErrorMessage(error);
  } finally {
    historyLoading.value = false;
  }
}

async function openSupplementalMatch(
  row: SupplementalCollectionRowItem,
): Promise<void> {
  unmatchConfirming.value = false;
  matchingRow.value = row;
  matchProjectId.value = row.projectId ?? "";
  if (projectOptions.value.length > 0) return;
  matchLoading.value = true;
  try {
    projectOptions.value = await projectImportApi.projectOptions();
  } catch (error) {
    errorMessage.value = getErrorMessage(error);
    matchingRow.value = null;
  } finally {
    matchLoading.value = false;
  }
}

async function saveSupplementalMatch(): Promise<void> {
  if (!matchingRow.value || !matchProjectId.value) return;
  matchLoading.value = true;
  errorMessage.value = "";
  try {
    currentBatch.value = await projectImportApi.matchSupplemental(
      matchingRow.value.id,
      { projectId: matchProjectId.value },
    );
    matchingRow.value = null;
    matchProjectId.value = "";
    await loadBatches();
  } catch (error) {
    errorMessage.value = getErrorMessage(error);
  } finally {
    matchLoading.value = false;
  }
}

async function clearSupplementalMatch(): Promise<void> {
  if (!matchingRow.value?.projectId) return;
  matchLoading.value = true;
  errorMessage.value = "";
  try {
    currentBatch.value = await projectImportApi.unmatchSupplemental(
      matchingRow.value.id,
    );
    matchingRow.value = null;
    unmatchConfirming.value = false;
    matchProjectId.value = "";
    await loadBatches();
  } catch (error) {
    errorMessage.value = getErrorMessage(error);
  } finally {
    matchLoading.value = false;
  }
}

function getErrorMessage(error: unknown): string {
  return error instanceof ApiError || error instanceof Error
    ? error.message
    : "操作失败，请稍后重试";
}

function statusLabel(status: ImportBatchStatus): string {
  return {
    PREVIEWED: "待确认",
    IMPORTED: "已导入",
    ROLLED_BACK: "已回滚",
    FAILED: "失败",
  }[status];
}

function riskLabel(level: ProjectRiskLevel): string {
  return {
    HIGH: "高",
    MEDIUM: "中",
    LOW: "低",
    UNKNOWN: "未知",
  }[level];
}

function matchStatusLabel(
  status: SupplementalMatchStatus | LegalMatterMatchStatus,
): string {
  return {
    MATCHED: "已匹配",
    UNMATCHED: "待匹配",
    AMBIGUOUS: "多项匹配",
  }[status];
}

function formatAmount(value: string | null): string {
  if (value === null) return "—";
  return Number(value).toLocaleString("zh-CN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function formatTime(value: string | null): string {
  return value ? new Date(value).toLocaleString("zh-CN") : "—";
}
</script>

<template>
  <AdminShell>
    <section class="imports-page-heading">
      <div>
        <p class="admin-eyebrow">PROJECT &amp; PAYMENT DATA IMPORT</p>
        <h1>项目数据导入</h1>
        <p>通过 Excel 批次导入项目清单与回款数据，校验通过并发布后供风险看板和 Agent 使用。</p>
      </div>
      <div class="imports-heading-actions">
        <button class="template-button" type="button" @click="downloadImportGuide">
          <span aria-hidden="true"></span>下载导入说明
        </button>
        <button class="new-import-button" type="button" @click="openUploadDrawer">
          <span aria-hidden="true"></span>新建导入
        </button>
      </div>
    </section>

    <p v-if="errorMessage" class="admin-alert" role="alert">
      {{ errorMessage }}
    </p>
    <p v-if="noticeMessage" class="admin-notice" role="status">
      {{ noticeMessage }}
    </p>

    <input
      ref="fileInput"
      type="file"
      accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
      hidden
      @change="onFileChange"
    />

    <section class="prototype-import-summary-grid" aria-label="导入统计">
      <article class="prototype-summary-card summary-version">
        <span class="prototype-summary-icon" aria-hidden="true">V</span>
        <div><small>当前数据版本</small><strong>V{{ importedBatchCount }}</strong></div>
        <i>{{ latestImportedBatch ? `${formatTime(latestImportedBatch.confirmedAt)}发布` : "暂无已发布批次" }}</i>
      </article>
      <article class="prototype-summary-card summary-projects">
        <span class="prototype-summary-icon" aria-hidden="true">▣</span>
        <div><small>有效项目</small><strong>{{ effectiveProjectCount }}<em>个</em></strong></div>
        <i>来自当前主清单</i>
      </article>
      <button class="prototype-summary-card summary-pending" type="button" @click="scrollToImportSection('pending')">
        <span class="prototype-summary-icon" aria-hidden="true">!</span>
        <div><small>待确认匹配</small><strong>{{ pendingMatchCount }}<em>条</em></strong></div>
        <i>确认后方可计入统计</i>
      </button>
      <article class="prototype-summary-card summary-batches">
        <span class="prototype-summary-icon" aria-hidden="true">▤</span>
        <div><small>本月导入</small><strong>{{ monthImportedBatchCount }}<em>批</em></strong></div>
        <i>以正式发布批次计</i>
      </article>
    </section>

    <section v-if="!currentBatch || selectedFile" class="prototype-import-upload">
      <div
        class="import-dropzone"
        tabindex="0"
        role="button"
        @click="chooseFile"
        @keydown.enter="chooseFile"
        @dragover.prevent
        @drop.prevent="onDrop"
      >
        <span class="import-upload-icon" aria-hidden="true">XL</span>
        <div>
          <strong>{{
            selectedFile?.name || "选择或拖入项目清单 Excel"
          }}</strong>
          <p>仅支持 .xlsx，最大20MB；上传后只做预检，不会立即写入项目表。</p>
        </div>
      </div>
      <button
        class="admin-primary-button"
        type="button"
        :disabled="!selectedFile || loading"
        @click="preview"
      >
        {{ loading ? "处理中…" : "上传并预检" }}
      </button>
    </section>

    <section v-if="currentBatch" class="import-preview-card prototype-active-import">
      <header class="prototype-active-header">
        <div class="prototype-file-heading">
          <span class="prototype-excel-icon" aria-hidden="true">X</span>
          <div>
            <p>CURRENT IMPORT BATCH</p>
            <div><h2>{{ currentBatch.fileName }}</h2><code>{{ currentBatch.id.slice(0, 13).toUpperCase() }}</code></div>
            <span>{{ currentBatch.sheetName }} · 上传人：{{ currentBatch.uploadedByName }} · {{ formatTime(currentBatch.createdAt) }}</span>
          </div>
        </div>
        <div class="prototype-active-state">
          <span class="import-status-pill" :class="`status-${currentBatch.status.toLowerCase()}`">{{ statusLabel(currentBatch.status) }}</span>
          <button
            type="button"
            aria-label="更多批次操作"
            :aria-expanded="batchMenuOpen"
            @click="batchMenuOpen = !batchMenuOpen"
          >•••</button>
          <div v-if="batchMenuOpen" class="prototype-batch-menu">
            <button type="button" @click="downloadCurrentSource">下载原文件</button>
            <button
              v-if="currentBatch.status === 'PREVIEWED'"
              type="button"
              @click="saveCurrentDraft"
            >保存当前草稿</button>
            <button type="button" @click="chooseFile">新建导入</button>
            <button
              v-if="currentBatch.status === 'IMPORTED'"
              class="is-danger"
              type="button"
              @click="requestRollback(currentBatch)"
            >回滚当前批次</button>
          </div>
        </div>
      </header>

      <ol class="prototype-import-stepper" aria-label="导入进度">
        <li class="is-complete"><span>1</span><div><strong>上传文件</strong><small>文件摘要已生成</small></div></li>
        <li class="is-complete"><span>2</span><div><strong>解析校验</strong><small>{{ currentBatch.sourceMeta.sheetNames.length }}张工作表已识别</small></div></li>
        <li :class="{ 'is-complete': currentBatch.status !== 'PREVIEWED', 'is-current': currentBatch.status === 'PREVIEWED' }"><span>3</span><div><strong>确认差异</strong><small>{{ pendingMatchCount }}条匹配待确认</small></div></li>
        <li :class="{ 'is-current': currentBatch.status === 'IMPORTED', 'is-complete': currentBatch.status === 'IMPORTED' }"><span>4</span><div><strong>发布数据</strong><small>确认后整批生效</small></div></li>
      </ol>

      <div class="prototype-import-workspace">
        <main>
          <section class="prototype-sheet-recognition">
            <header><div><p>WORKBOOK STRUCTURE</p><h3>工作表识别结果</h3></div><span><i></i>{{ currentBatch.sourceMeta.sheetNames.length }}张工作表已识别</span></header>
            <div class="prototype-sheet-grid">
              <button :class="{ 'is-active': activeSheet === 'main' }" type="button" @click="showSheet('main')"><span class="sheet-token is-main">▣</span><span><strong>数据回款</strong><small>主清单</small></span><b>{{ currentBatch.totalRows }}条</b><i>✓</i></button>
              <button :class="{ 'is-active': activeSheet === 'supplemental' }" type="button" @click="showSheet('supplemental')"><span class="sheet-token is-payment">□</span><span><strong>涵谷回款</strong><small>补充回款</small></span><b>{{ currentBatch.supplementalTotalRows }}条</b><i>✓</i></button>
              <button :class="{ 'is-active': activeSheet === 'legal' }" type="button" @click="showSheet('legal')"><span class="sheet-token is-legal">□</span><span><strong>发函-诉讼清单</strong><small>风险补充</small></span><b>{{ currentBatch.legalTotalRows }}条</b><i>✓</i></button>
              <button :class="{ 'is-active': activeSheet === 'summary' }" type="button" class="is-muted" @click="showSheet('summary')"><span class="sheet-token">□</span><span><strong>汇总</strong><small>派生公式</small></span><b>不导入</b><i>−</i></button>
            </div>
          </section>

          <div class="prototype-validation-strip">
            <div><small>解析记录</small><strong>{{ parsedRecordCount }}</strong></div>
            <div><small>新增项目</small><strong class="is-blue">{{ currentBatch.createdRows }}</strong></div>
            <div><small>更新项目</small><strong class="is-cyan">{{ currentBatch.updatedRows }}</strong></div>
            <button type="button" @click="scrollToImportSection('pending')"><small>待确认</small><strong class="is-orange">{{ pendingMatchCount }}</strong></button>
            <div><small>聚合警告</small><strong class="is-yellow">{{ aggregateWarningCount }}</strong></div>
            <div><small>错误</small><strong class="is-green">{{ aggregateErrorCount }}</strong></div>
          </div>

          <nav class="prototype-import-tabs" aria-label="导入预览视图">
            <button :class="{ 'is-active': activeImportTab === 'preview' }" type="button" @click="scrollToImportSection('preview')">数据预览 <span>{{ parsedRecordCount }}</span></button>
            <button :class="{ 'is-active': activeImportTab === 'pending' }" type="button" @click="scrollToImportSection('pending')">待确认匹配 <span>{{ pendingMatchCount }}</span></button>
            <button :class="{ 'is-active': activeImportTab === 'rules' }" type="button" @click="scrollToImportSection('rules')">校验规则 <span>6</span></button>
          </nav>
        </main>

        <aside class="prototype-warning-panel">
          <header><span>!</span><strong>聚合警告</strong><b>{{ aggregateWarningCount }}类</b></header>
          <div><em>{{ currentBatch.warningRows }}</em><span><strong>主清单警告</strong><small>包含空值、重名或重复编码</small></span></div>
          <div><em>{{ currentBatch.supplementalWarningRows }}</em><span><strong>补充回款警告</strong><small>待确认项目匹配</small></span></div>
          <div><em>{{ currentBatch.legalWarningRows }}</em><span><strong>法务事项警告</strong><small>待确认项目匹配</small></span></div>
          <div><em>{{ aggregateErrorCount }}</em><span><strong>阻断错误</strong><small>必须为0才可发布</small></span></div>
        </aside>
      </div>

      <div class="batch-validation-grid">
        <article>
          <small>总行数</small>
          <strong>{{ currentBatch.totalRows }}</strong>
        </article>
        <article class="summary-ready">
          <small>可直接处理</small>
          <strong>{{ currentBatch.readyRows }}</strong>
        </article>
        <article class="summary-warning">
          <small>警告</small>
          <strong>{{ currentBatch.warningRows }}</strong>
        </article>
        <article class="summary-error">
          <small>错误</small>
          <strong>{{ currentBatch.errorRows }}</strong>
        </article>
        <article>
          <small>新增 / 更新</small>
          <strong>
            {{ currentBatch.createdRows }} /
            {{ currentBatch.updatedRows }}
          </strong>
        </article>
      </div>

      <section id="import-rules-section" class="prototype-rule-strip">
        <strong>校验规则</strong>
        <span>必填字段</span><span>金额格式</span><span>项目编码唯一</span><span>同编码合并</span><span>项目精确匹配</span><span>整批事务发布</span>
      </section>

      <span id="import-pending-section" class="import-anchor" aria-hidden="true"></span>

      <section
        v-if="currentBatch.supplementalTotalRows > 0"
        class="supplemental-summary"
      >
        <div>
          <p class="admin-eyebrow">SUPPLEMENTAL COLLECTION DATA</p>
          <h3>涵谷回款补充数据</h3>
          <span>
            独立保存回款记录，不新增主项目；未匹配记录可在后续人工关联。
          </span>
        </div>
        <dl>
          <div>
            <dt>记录数</dt>
            <dd>{{ currentBatch.supplementalTotalRows }}</dd>
          </div>
          <div class="summary-ready">
            <dt>已匹配</dt>
            <dd>{{ currentBatch.supplementalMatchedRows }}</dd>
          </div>
          <div class="summary-warning">
            <dt>待匹配</dt>
            <dd>{{ currentBatch.supplementalUnmatchedRows }}</dd>
          </div>
          <div class="summary-error">
            <dt>多项匹配 / 错误</dt>
            <dd>
              {{ currentBatch.supplementalAmbiguousRows }} /
              {{ currentBatch.supplementalErrorRows }}
            </dd>
          </div>
        </dl>
      </section>

      <section
        v-if="currentBatch.legalTotalRows > 0"
        class="supplemental-summary legal-summary"
      >
        <div>
          <p class="admin-eyebrow">LEGAL MATTERS</p>
          <h3>发函与诉讼事项</h3>
          <span>
            法务事项独立关联主项目，不重复创建项目或风险记录。
          </span>
        </div>
        <dl>
          <div>
            <dt>事项数</dt>
            <dd>{{ currentBatch.legalTotalRows }}</dd>
          </div>
          <div class="summary-ready">
            <dt>已匹配</dt>
            <dd>{{ currentBatch.legalMatchedRows }}</dd>
          </div>
          <div class="summary-warning">
            <dt>待匹配</dt>
            <dd>{{ currentBatch.legalUnmatchedRows }}</dd>
          </div>
          <div class="summary-error">
            <dt>多项匹配 / 错误</dt>
            <dd>
              {{ currentBatch.legalAmbiguousRows }} /
              {{ currentBatch.legalErrorRows }}
            </dd>
          </div>
        </dl>
      </section>

      <div
        v-if="currentBatch.status === 'PREVIEWED'"
        class="import-confirm-bar"
      >
        <label
          v-if="
            currentBatch.warningRows +
              currentBatch.supplementalWarningRows +
              currentBatch.legalWarningRows >
            0
          "
        >
          <input v-model="acknowledgeWarnings" type="checkbox" />
          我已核对主项目、补充回款及法务事项警告，确认按预检结果入库
        </label>
        <span v-else>预检未发现警告，可确认入库。</span>
        <button
          class="admin-primary-button"
          type="button"
          :disabled="!canConfirm || loading"
          @click="publishConfirmOpen = true"
        >
          确认入库
        </button>
      </div>

      <section
        v-if="currentBatch.supplementalRows.length > 0"
        id="sheet-supplemental-section"
        class="supplemental-rows-section"
      >
        <header>
          <div>
            <p class="admin-eyebrow">HANGGU COLLECTIONS</p>
            <h3>涵谷回款记录明细</h3>
          </div>
          <span>已读取隐藏金额列与 1—12 月回款数据</span>
        </header>
        <div class="admin-table-scroll supplemental-rows-scroll">
          <table class="admin-table supplemental-row-table">
            <thead>
              <tr>
                <th>Excel行</th>
                <th>匹配状态</th>
                <th>项目</th>
                <th>合同应收</th>
                <th>采购合同</th>
                <th>累计已收</th>
                <th>剩余未回</th>
                <th>回款风险</th>
                <th>关联项目</th>
                <th>校验信息</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="row in currentBatch.supplementalRows"
                :key="row.id"
              >
                <td>{{ row.rowNumber }}</td>
                <td>
                  <span
                    class="row-status"
                    :class="`match-${row.matchStatus.toLowerCase()}`"
                  >
                    {{ matchStatusLabel(row.matchStatus) }}
                  </span>
                </td>
                <td>
                  <strong>{{ row.projectName || "未填写项目名称" }}</strong>
                  <small>{{ row.externalCode || "无项目编码" }}</small>
                </td>
                <td>{{ formatAmount(row.contractReceivableAmount) }}</td>
                <td>{{ formatAmount(row.procurementContractAmount) }}</td>
                <td>{{ formatAmount(row.cumulativeCollectedAmount) }}</td>
                <td>{{ formatAmount(row.remainingUncollectedAmount) }}</td>
                <td>
                  <span
                    class="risk-mini-pill"
                    :class="`risk-${row.collectionRiskLevel.toLowerCase()}`"
                  >
                    {{ riskLabel(row.collectionRiskLevel) }}
                  </span>
                </td>
                <td class="supplemental-match-cell">
                  <span v-if="row.matchedProject">
                    <strong>{{ row.matchedProject.name }}</strong>
                    <small>
                      {{ row.matchedProject.departmentName || "未分配部门" }}
                    </small>
                  </span>
                  <small v-else>尚未关联</small>
                  <button
                    v-if="currentBatch.status === 'IMPORTED'"
                    type="button"
                    @click="openSupplementalMatch(row)"
                  >
                    {{ row.projectId ? "调整关联" : "关联项目" }}
                  </button>
                </td>
                <td class="validation-messages">
                  <span
                    v-for="message in row.errors"
                    :key="`se-${message}`"
                    class="message-error"
                  >
                    {{ message }}
                  </span>
                  <span
                    v-for="message in row.warnings"
                    :key="`sw-${message}`"
                    class="message-warning"
                  >
                    {{ message }}
                  </span>
                  <span
                    v-if="
                      row.errors.length === 0 &&
                      row.warnings.length === 0
                    "
                  >
                    校验通过
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section
        v-if="currentBatch.legalRows.length > 0"
        id="sheet-legal-section"
        class="supplemental-rows-section legal-rows-section"
      >
        <header>
          <div>
            <p class="admin-eyebrow">LEGAL REGISTER</p>
            <h3>发函与诉讼事项明细</h3>
          </div>
          <span>按项目名称或编码精确关联，不生成重复项目</span>
        </header>
        <div class="admin-table-scroll legal-rows-scroll">
          <table class="admin-table legal-row-table">
            <thead>
              <tr>
                <th>Excel行</th>
                <th>匹配状态</th>
                <th>项目</th>
                <th>部门 / 负责人</th>
                <th>风险</th>
                <th>法务进展</th>
                <th>校验信息</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in currentBatch.legalRows" :key="row.id">
                <td>{{ row.rowNumber }}</td>
                <td>
                  <span
                    class="row-status"
                    :class="`match-${row.matchStatus.toLowerCase()}`"
                  >
                    {{ matchStatusLabel(row.matchStatus) }}
                  </span>
                </td>
                <td>
                  <strong>{{ row.projectName || "未填写项目名称" }}</strong>
                  <small>{{ row.externalCode || "无项目编码" }}</small>
                </td>
                <td>
                  {{ row.departmentName || "—" }}
                  <small>{{ row.deliveryOwnerName || "—" }}</small>
                </td>
                <td>
                  <span
                    class="risk-mini-pill"
                    :class="`risk-${row.collectionRiskLevel.toLowerCase()}`"
                  >
                    {{ riskLabel(row.collectionRiskLevel) }}
                  </span>
                </td>
                <td class="legal-progress">
                  {{ row.legalProgress || "—" }}
                </td>
                <td class="validation-messages">
                  <span
                    v-for="message in row.errors"
                    :key="`le-${message}`"
                    class="message-error"
                  >
                    {{ message }}
                  </span>
                  <span
                    v-for="message in row.warnings"
                    :key="`lw-${message}`"
                    class="message-warning"
                  >
                    {{ message }}
                  </span>
                  <span
                    v-if="
                      row.errors.length === 0 &&
                      row.warnings.length === 0
                    "
                  >
                    校验通过
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <div id="import-preview-section" class="admin-table-scroll import-rows-scroll">
        <table class="admin-table import-row-table">
          <thead>
            <tr>
              <th>Excel行</th>
              <th>处理</th>
              <th>状态</th>
              <th>项目</th>
              <th>部门 / 负责人</th>
              <th>风险</th>
              <th>校验信息</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in currentBatch.rows" :key="row.id">
              <td>{{ row.rowNumber }}</td>
              <td>{{ row.action === "CREATE" ? "新增" : row.action === "UPDATE" ? "更新" : "跳过" }}</td>
              <td>
                <span
                  class="row-status"
                  :class="`row-${row.status.toLowerCase()}`"
                >
                  {{ row.status }}
                </span>
              </td>
              <td>
                <strong>{{ row.projectName || "未填写项目名称" }}</strong>
                <small>{{ row.externalCode || "无项目编码" }}</small>
              </td>
              <td>
                {{ row.departmentName || "—" }}
                <small>{{ row.deliveryOwnerName || "—" }}</small>
              </td>
              <td>
                <span
                  class="risk-mini-pill"
                  :class="`risk-${row.collectionRiskLevel.toLowerCase()}`"
                >
                  {{ riskLabel(row.collectionRiskLevel) }}
                </span>
              </td>
              <td class="validation-messages">
                <span v-for="message in row.errors" :key="`e-${message}`" class="message-error">
                  {{ message }}
                </span>
                <span v-for="message in row.warnings" :key="`w-${message}`" class="message-warning">
                  {{ message }}
                </span>
                <span v-if="row.errors.length === 0 && row.warnings.length === 0">
                  校验通过
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="admin-content-card import-history">
      <header class="content-card-header">
        <div>
          <p class="admin-eyebrow">IMPORT HISTORY</p>
          <h2>导入批次 <small>最近10个批次</small></h2>
        </div>
      </header>
      <div class="import-history-filters"><input v-model="historyKeyword" type="search" placeholder="搜索文件名、批次号或上传人" @input="historyPage=1"><select v-model="historyStatus" @change="historyPage=1"><option value="">全部状态</option><option value="PREVIEWED">待确认</option><option value="IMPORTED">已导入</option><option value="ROLLED_BACK">已回滚</option><option value="FAILED">失败</option></select><button type="button" @click="historyKeyword='';historyStatus='';historyPage=1">重置</button></div>
      <div v-if="historyLoading" class="admin-state">正在加载批次记录…</div>
      <div v-else-if="batches.length === 0" class="admin-state">
        暂无导入批次
      </div>
      <div v-else class="admin-table-scroll">
        <table class="admin-table">
          <thead>
            <tr>
              <th>文件</th>
              <th>状态</th>
              <th>总行数</th>
              <th>新增 / 更新</th>
              <th>上传人</th>
              <th>上传时间</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="batch in pagedBatches" :key="batch.id">
              <td><strong>{{ batch.fileName }}</strong></td>
              <td>
                <span
                  class="import-status-pill"
                  :class="`status-${batch.status.toLowerCase()}`"
                >
                  {{ statusLabel(batch.status) }}
                </span>
              </td>
              <td>
                {{ batch.totalRows }}
                <small v-if="batch.supplementalTotalRows > 0">
                  + {{ batch.supplementalTotalRows }} 条补充回款
                </small>
                <small v-if="batch.legalTotalRows > 0">
                  + {{ batch.legalTotalRows }} 条法务事项
                </small>
              </td>
              <td>{{ batch.createdRows }} / {{ batch.updatedRows }}</td>
              <td>{{ batch.uploadedByName }}</td>
              <td>{{ formatTime(batch.createdAt) }}</td>
              <td>
                <div class="table-actions">
                  <button type="button" @click="openBatch(batch.id)">
                    查看
                  </button>
                  <button type="button" @click="selectedHistoryBatch = batch">批次详情</button>
                  <button v-if="batch.status === 'FAILED'" type="button" @click="downloadBatchErrors(batch)">下载错误</button>
                  <button
                    v-if="batch.status === 'IMPORTED'"
                    type="button"
                    class="danger-text-button"
                    @click="requestRollback(batch)"
                  >
                    回滚
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <footer class="prototype-pagination"><span>显示 {{ pagedBatches.length }} 条，共 {{ filteredBatches.length }} 个批次</span><div><button type="button" :disabled="historyPage<=1" @click="historyPage--">上一页</button><b>{{ historyPage }}</b><button type="button" :disabled="historyPage>=historyTotalPages" @click="historyPage++">下一页</button></div></footer>
    </section>

    <div v-if="uploadDrawerOpen" class="drawer-backdrop" role="presentation" @click.self="uploadDrawerOpen=false"><section class="admin-drawer import-upload-drawer" role="dialog" aria-modal="true"><header class="admin-drawer-header"><div><p class="admin-eyebrow">CREATE IMPORT</p><h2>新建项目数据导入</h2></div><button type="button" aria-label="关闭" @click="uploadDrawerOpen=false">×</button></header><div class="import-drawer-body"><section class="upload-requirements"><h3>上传要求</h3><ul><li>仅支持 .xlsx 文件，最大20MB。</li><li>主工作表必须为“数据回款”。</li><li>可选“涵谷回款”和“发函-诉讼清单”。</li><li>上传仅生成预检批次，不会直接入库。</li></ul></section><div class="import-dropzone" tabindex="0" role="button" @click="chooseFile" @keydown.enter="chooseFile" @dragover.prevent @drop.prevent="onDrop"><span class="import-upload-icon">XL</span><div><strong>{{ selectedFile?.name || '选择或拖入项目清单 Excel' }}</strong><p>{{ selectedFile ? `${(selectedFile.size/1024).toFixed(1)} KB · 等待预检` : '支持点击选择文件或拖拽到此区域' }}</p></div></div><div class="drawer-file-check"><span :class="{ready:selectedFile}">{{ selectedFile?'✓':'1' }}</span><div><strong>文件检查</strong><small>{{ selectedFile?'格式符合要求，可开始解析':'等待选择Excel文件' }}</small></div></div></div><footer class="drawer-form-actions"><button type="button" @click="uploadDrawerOpen=false">取消</button><button class="admin-primary-button" type="button" :disabled="!selectedFile||loading" @click="preview">{{ loading?'解析中…':'上传并预检' }}</button></footer></section></div>

    <ModalDialog v-if="publishConfirmOpen && currentBatch" eyebrow="PUBLISH IMPORT BATCH" title="确认发布数据" @close="publishConfirmOpen=false"><div class="publish-confirm-summary"><span>!</span><div><strong>{{ currentBatch.fileName }}</strong><p>本次将新增 {{ currentBatch.createdRows }} 个项目、更新 {{ currentBatch.updatedRows }} 个项目，并发布补充回款与法务关联结果。</p></div></div><dl class="prototype-detail-list"><div><dt>解析记录</dt><dd>{{ parsedRecordCount }}条</dd></div><div><dt>待确认匹配</dt><dd>{{ pendingMatchCount }}条</dd></div><div><dt>警告 / 错误</dt><dd>{{ aggregateWarningCount }} / {{ aggregateErrorCount }}</dd></div><div><dt>生效方式</dt><dd>整批事务发布，成功后立即供风险看板与Agent使用。</dd></div></dl><template #footer><button type="button" @click="publishConfirmOpen=false">返回检查</button><button class="admin-primary-button" type="button" :disabled="loading" @click="confirmImport">{{ loading?'发布中…':'确认发布' }}</button></template></ModalDialog>

    <ModalDialog v-if="selectedHistoryBatch" eyebrow="IMPORT BATCH DETAIL" title="导入批次详情" @close="selectedHistoryBatch=null"><dl class="prototype-detail-list"><div><dt>批次编号</dt><dd>{{ selectedHistoryBatch.id }}</dd></div><div><dt>文件名</dt><dd>{{ selectedHistoryBatch.fileName }}</dd></div><div><dt>状态</dt><dd>{{ statusLabel(selectedHistoryBatch.status) }}</dd></div><div><dt>主清单 / 补充 / 法务</dt><dd>{{ selectedHistoryBatch.totalRows }} / {{ selectedHistoryBatch.supplementalTotalRows }} / {{ selectedHistoryBatch.legalTotalRows }}条</dd></div><div><dt>新增 / 更新</dt><dd>{{ selectedHistoryBatch.createdRows }} / {{ selectedHistoryBatch.updatedRows }}</dd></div><div><dt>上传人 / 时间</dt><dd>{{ selectedHistoryBatch.uploadedByName }} · {{ formatTime(selectedHistoryBatch.createdAt) }}</dd></div></dl><template #footer><button type="button" @click="selectedHistoryBatch=null">关闭</button><button class="admin-primary-button" type="button" @click="openBatch(selectedHistoryBatch.id);selectedHistoryBatch=null">打开完整批次</button></template></ModalDialog>

    <ModalDialog
      v-if="matchingRow"
      eyebrow="SUPPLEMENTAL COLLECTION MATCH"
      title="关联补充回款项目"
      @close="matchingRow = null; unmatchConfirming = false"
    >
      <div class="supplemental-match-dialog">
        <section>
          <span>Excel 项目名称</span>
          <strong>{{ matchingRow.projectName || "未填写项目名称" }}</strong>
          <small>
            合同应收 {{ formatAmount(matchingRow.contractReceivableAmount) }}
            元 · 累计已收
            {{ formatAmount(matchingRow.cumulativeCollectedAmount) }} 元
          </small>
        </section>
        <label>
          <span>选择主项目</span>
          <select v-model="matchProjectId" :disabled="matchLoading">
            <option value="">请选择主项目</option>
            <option
              v-for="project in projectOptions"
              :key="project.id"
              :value="project.id"
            >
              {{ project.name }} · {{ project.departmentName || "未分配部门" }}
            </option>
          </select>
          <small>
            人工确认后，该记录才会计入所选项目及部门回款统计。
          </small>
        </label>
      </div>
      <p v-if="unmatchConfirming" class="inline-confirmation" role="alert">
        解除后，这条补充回款记录将不再计入当前项目及部门统计。确认继续吗？
      </p>
      <template #footer>
        <button
          v-if="matchingRow.projectId && !unmatchConfirming"
          type="button"
          class="admin-danger-button"
          :disabled="matchLoading"
          @click="unmatchConfirming = true"
        >
          解除关联
        </button>
        <button
          v-if="unmatchConfirming"
          type="button"
          class="admin-danger-button"
          :disabled="matchLoading"
          @click="clearSupplementalMatch"
        >
          {{ matchLoading ? "解除中" : "确认解除" }}
        </button>
        <button type="button" @click="unmatchConfirming ? (unmatchConfirming = false) : (matchingRow = null)">
          {{ unmatchConfirming ? "返回" : "取消" }}
        </button>
        <button
          v-if="!unmatchConfirming"
          type="button"
          class="admin-primary-button"
          :disabled="!matchProjectId || matchLoading"
          @click="saveSupplementalMatch"
        >
          {{ matchLoading ? "保存中" : "确认关联" }}
        </button>
      </template>
    </ModalDialog>

    <ModalDialog
      v-if="rollbackCandidate"
      eyebrow="ROLLBACK IMPORT BATCH"
      title="确认回滚导入批次"
      @close="rollbackCandidate = null"
    >
      <div class="rollback-confirmation">
        <strong>{{ rollbackCandidate.fileName }}</strong>
        <p>本批次新增的项目将删除，更新过的项目将恢复至导入前数据。该操作会完整记录到审计日志。</p>
      </div>
      <template #footer>
        <button type="button" :disabled="loading" @click="rollbackCandidate = null">取消</button>
        <button
          type="button"
          class="admin-danger-button"
          :disabled="loading"
          @click="confirmRollback"
        >
          {{ loading ? "回滚中" : "确认回滚" }}
        </button>
      </template>
    </ModalDialog>
  </AdminShell>
</template>

<style scoped>
.imports-page-heading {
  display: flex;
  margin-bottom: 22px;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
}

.imports-page-heading h1 {
  margin: 0;
  color: #15364f;
  font-size: clamp(30px, 3vw, 38px);
  line-height: 1.2;
}

.imports-page-heading > div:first-child > p:last-child {
  margin: 10px 0 0;
  color: #7a93a5;
  font-size: 14px;
  line-height: 1.6;
}

.imports-heading-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.admin-notice {
  margin: 0 0 18px;
  padding: 12px 15px;
  border: 1px solid rgba(26, 165, 121, 0.16);
  border-radius: 12px;
  color: #167b5d;
  background: #ecfaf5;
  font-size: 13px;
  font-weight: 650;
  line-height: 1.6;
}

.template-button,
.new-import-button {
  display: inline-flex;
  min-height: 44px;
  padding: 0 17px;
  border: 1px solid #cfe0ec;
  border-radius: 11px;
  align-items: center;
  gap: 9px;
  color: #315f7c;
  background: #fff;
  cursor: pointer;
  font-size: 13px;
  font-weight: 750;
  white-space: nowrap;
}

.new-import-button {
  border-color: transparent;
  color: #fff;
  background: linear-gradient(100deg, #176de2, #16a9b3);
  box-shadow: 0 12px 24px rgba(23, 109, 226, 0.18);
}

.template-button span {
  width: 11px;
  height: 14px;
  border: 1.7px solid currentColor;
  border-radius: 2px;
}

.new-import-button span::before { content: "+"; font-size: 20px; font-weight: 500; }

.prototype-import-summary-grid {
  display: grid;
  margin-bottom: 18px;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
}

.prototype-summary-card {
  position: relative;
  display: grid;
  min-width: 0;
  min-height: 120px;
  padding: 20px;
  overflow: hidden;
  border: 1px solid #dbe8f2;
  border-radius: 18px;
  align-content: center;
  color: #34566f;
  background: #fff;
  box-shadow: 0 10px 30px rgba(35, 74, 108, 0.06);
  text-align: left;
}

button.prototype-summary-card { width: 100%; cursor: pointer; }
.prototype-summary-card::after { position: absolute; top: -32px; right: -24px; width: 108px; height: 108px; border-radius: 50%; background: var(--summary-soft); content: ""; }
.prototype-summary-card.summary-version { --summary-color: #1478df; --summary-soft: #eaf4ff; }
.prototype-summary-card.summary-projects { --summary-color: #059bac; --summary-soft: #e8f9fa; }
.prototype-summary-card.summary-pending { --summary-color: #d77c08; --summary-soft: #fff4df; }
.prototype-summary-card.summary-batches { --summary-color: #6b61d9; --summary-soft: #f1efff; }
.prototype-summary-icon { position: absolute; z-index: 1; top: 18px; right: 18px; display: grid; width: 40px; height: 40px; border: 1px solid color-mix(in srgb, var(--summary-color) 25%, white); border-radius: 13px; place-items: center; color: var(--summary-color); background: rgba(255,255,255,.76); font-style: normal; font-weight: 800; }
.prototype-summary-card > div { position: relative; z-index: 1; }
.prototype-summary-card small { display: block; margin-bottom: 7px; color: #6e879d; font-size: 13px; font-weight: 650; }
.prototype-summary-card strong { display: block; color: var(--summary-color); font-size: 29px; line-height: 1; }
.prototype-summary-card strong em { margin-left: 3px; font-size: 12px; font-style: normal; }
.prototype-summary-card > i { position: absolute; right: 18px; bottom: 15px; color: #8ca0b2; font-size: 12px; font-style: normal; }

.prototype-import-upload {
  display: grid;
  margin-bottom: 18px;
  padding: 18px;
  border: 1px solid #dbe7f1;
  border-radius: 18px;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 14px;
  background: #fff;
  box-shadow: 0 10px 30px rgba(35, 74, 108, 0.06);
}

.prototype-active-import { border-radius: 20px; box-shadow: 0 12px 34px rgba(36,74,108,.07); }
.prototype-active-header { position: relative; display: flex; padding: 22px 24px; border-bottom: 1px solid #e5edf4; align-items: flex-start; justify-content: space-between; gap: 20px; }
.prototype-file-heading { display: flex; min-width: 0; align-items: center; gap: 14px; }
.prototype-excel-icon { display: grid; width: 48px; height: 48px; border-radius: 12px; flex: 0 0 auto; place-items: center; color: #fff; background: #1ca361; box-shadow: 0 8px 18px rgba(28,163,97,.18); font-weight: 850; }
.prototype-file-heading > div { min-width: 0; }
.prototype-file-heading p { margin: 0 0 5px; color: #1880dc; font-size: 12px; font-weight: 800; letter-spacing: .15em; }
.prototype-file-heading > div > div { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
.prototype-file-heading h2 { margin: 0; color: #173a53; font-size: 20px; }
.prototype-file-heading code { color: #8ca0b2; font-family: inherit; font-size: 12px; }
.prototype-file-heading > div > span { display: block; margin-top: 6px; color: #8ca0b2; font-size: 12px; }
.prototype-active-state { position: relative; display: flex; align-items: center; gap: 9px; }
.prototype-active-state > button { width: 36px; height: 36px; border: 1px solid #d8e4ee; border-radius: 10px; color: #678096; background: #fff; }
.prototype-batch-menu { position: absolute; z-index: 20; top: 43px; right: 0; display: grid; width: 168px; padding: 6px; border: 1px solid #d9e5ee; border-radius: 12px; background: #fff; box-shadow: 0 14px 36px rgba(31,67,97,.16); }
.prototype-batch-menu button { width: 100%; min-height: 38px; padding: 0 10px; border: 0; border-radius: 8px; color: #35566f; background: transparent; font-size: 12px; text-align: left; }
.prototype-batch-menu button:hover { color: #1578cd; background: #edf7ff; }
.prototype-batch-menu button.is-danger { color: #c43d45; }

.prototype-import-stepper { display: grid; margin: 0; padding: 22px 24px 25px; list-style: none; grid-template-columns: repeat(4, minmax(0, 1fr)); }
.prototype-import-stepper li { position: relative; display: flex; min-width: 0; align-items: flex-start; gap: 11px; }
.prototype-import-stepper li:not(:last-child)::after { position: absolute; z-index: 0; top: 17px; right: 10px; left: 38px; height: 2px; background: #dfe8ef; content: ""; }
.prototype-import-stepper li.is-complete:not(:last-child)::after { background: linear-gradient(90deg,#1aa675,#66c8ad); }
.prototype-import-stepper li > span { position: relative; z-index: 1; display: grid; width: 34px; height: 34px; border: 2px solid #d8e4ed; border-radius: 50%; flex: 0 0 auto; place-items: center; color: #879aaa; background: #fff; font-size: 13px; font-weight: 800; }
.prototype-import-stepper li.is-complete > span { border-color: #23ad7d; color: #fff; background: #23ad7d; }
.prototype-import-stepper li.is-current > span { border-color: #1785e2; color: #1785e2; box-shadow: 0 0 0 5px #e7f4ff; }
.prototype-import-stepper li > div { min-width: 0; padding-right: 18px; }
.prototype-import-stepper strong { display: block; margin: 1px 0 4px; color: #234761; font-size: 13px; }
.prototype-import-stepper small { display: block; overflow: hidden; color: #8da0b0; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }

.prototype-import-workspace { display: grid; border-top: 1px solid #e5edf4; grid-template-columns: minmax(0,1fr) 292px; }
.prototype-import-workspace > main { min-width: 0; padding: 24px; }
.prototype-sheet-recognition > header { display: flex; margin-bottom: 16px; align-items: flex-start; justify-content: space-between; gap: 16px; }
.prototype-sheet-recognition header p { margin: 0 0 5px; color: #1782dc; font-size: 12px; font-weight: 800; letter-spacing: .15em; }
.prototype-sheet-recognition h3 { margin: 0; color: #173a53; font-size: 20px; }
.prototype-sheet-recognition header > span { display: flex; align-items: center; gap: 7px; color: #7990a2; font-size: 12px; white-space: nowrap; }
.prototype-sheet-recognition header > span i { width: 8px; height: 8px; border-radius: 50%; background: #21b99a; box-shadow: 0 0 0 5px #e8f8f5; }
.prototype-sheet-grid { display: grid; margin-bottom: 18px; grid-template-columns: repeat(4,minmax(0,1fr)); gap: 10px; }
.prototype-sheet-grid button { position: relative; display: grid; min-width: 0; min-height: 108px; padding: 14px; border: 1px solid #dbe7f0; border-radius: 14px; grid-template-columns: auto minmax(0,1fr) auto; align-content: start; align-items: center; gap: 8px; color: #34566f; background: #fbfdff; text-align: left; }
.prototype-sheet-grid button.is-muted { color: #718393; background: #f7f8fa; }
.prototype-sheet-grid button.is-active { border-color: #2288df; background: #f0f8ff; box-shadow: 0 0 0 3px #e3f2ff; }
.sheet-token { display: grid; width: 28px; height: 28px; border-radius: 8px; place-items: center; color: #73889a; background: #edf2f5; font-weight: 800; }
.sheet-token.is-main { color: #1683db; background: #e8f4fe; }
.sheet-token.is-payment { color: #179a65; background: #e7f7ef; }
.sheet-token.is-legal { color: #d27b0a; background: #fff2df; }
.prototype-sheet-grid button > span:nth-child(2) { min-width: 0; }
.prototype-sheet-grid strong { display: block; overflow: hidden; color: #264b65; font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
.prototype-sheet-grid small { display: block; margin-top: 4px; color: #8ca0af; font-size: 12px; }
.prototype-sheet-grid b { grid-column: 1 / -1; color: #4e6e84; font-size: 12px; }
.prototype-sheet-grid button > i { color: #20a873; font-size: 12px; font-style: normal; }

.prototype-validation-strip { display: grid; overflow: hidden; margin-bottom: 18px; border: 1px solid #dfe9f1; border-radius: 14px; grid-template-columns: repeat(6,minmax(0,1fr)); gap: 1px; background: #dfe9f1; }
.prototype-validation-strip > * { min-width: 0; padding: 13px 14px; border: 0; background: #f8fbfd; text-align: left; }
.prototype-validation-strip small { display: block; margin-bottom: 5px; color: #8095a6; font-size: 12px; white-space: nowrap; }
.prototype-validation-strip strong { color: #204964; font-size: 20px; }
.prototype-validation-strip .is-blue { color: #177ad1; }
.prototype-validation-strip .is-cyan { color: #099da8; }
.prototype-validation-strip .is-orange,.prototype-validation-strip .is-yellow { color: #bf7205; }
.prototype-validation-strip .is-green { color: #138658; }
.prototype-import-tabs { display: flex; gap: 26px; overflow-x: auto; border-bottom: 1px solid #e1eaf1; }
.prototype-import-tabs button { position: relative; flex: 0 0 auto; padding: 13px 2px 15px; border: 0; color: #708799; background: transparent; font-size: 13px; font-weight: 700; }
.prototype-import-tabs button.is-active { color: #137bd1; }
.prototype-import-tabs button.is-active::after { position: absolute; right: 0; bottom: -1px; left: 0; height: 3px; border-radius: 3px 3px 0 0; background: linear-gradient(90deg,#1684df,#13aeb1); content: ""; }
.prototype-import-tabs span { display: inline-grid; min-width: 21px; height: 21px; margin-left: 5px; padding: 0 6px; border-radius: 999px; place-items: center; color: #667f92; background: #eaf3fa; font-size: 12px; }

.prototype-warning-panel { padding: 24px 18px; border-left: 1px solid #e5edf4; background: #fbfdff; }
.prototype-warning-panel header { display: flex; margin-bottom: 14px; align-items: center; gap: 9px; }
.prototype-warning-panel header > span { display: grid; width: 28px; height: 28px; border-radius: 8px; place-items: center; color: #bd7106; background: #fff2d9; font-weight: 850; }
.prototype-warning-panel header strong { flex: 1; color: #244a65; font-size: 15px; }
.prototype-warning-panel header b { color: #d47808; font-size: 12px; }
.prototype-warning-panel > div { display: flex; margin-bottom: 10px; padding: 12px; border-radius: 12px; align-items: flex-start; gap: 10px; background: #fff6e7; }
.prototype-warning-panel em { display: grid; min-width: 34px; height: 28px; padding: 0 6px; border-radius: 8px; place-items: center; color: #fff; background: #f0a31b; font-size: 12px; font-style: normal; font-weight: 800; }
.prototype-warning-panel span { min-width: 0; }
.prototype-warning-panel div strong { display: block; color: #7a5929; font-size: 12px; }
.prototype-warning-panel div small { display: block; margin-top: 3px; color: #9d825c; font-size: 12px; line-height: 1.45; }

.batch-validation-grid { display: grid; padding: 20px 24px; border-top: 1px solid #e5edf4; grid-template-columns: repeat(5,minmax(0,1fr)); gap: 10px; }
.batch-validation-grid article { display: grid; min-height: 78px; padding: 14px; border: 1px solid #dfe9f1; border-radius: 12px; align-content: center; gap: 5px; background: #f8fbfd; }
.batch-validation-grid small { color: #7f94a5; font-size: 12px; }
.batch-validation-grid strong { color: #204964; font-size: 21px; }
.batch-validation-grid .summary-ready strong { color: #168658; }
.batch-validation-grid .summary-warning strong { color: #bd7205; }
.batch-validation-grid .summary-error strong { color: #d5454d; }
.prototype-rule-strip { display: flex; min-height: 58px; padding: 12px 24px; border-top: 1px solid #e5edf4; border-bottom: 1px solid #e5edf4; align-items: center; flex-wrap: wrap; gap: 8px; }
.prototype-rule-strip strong { margin-right: 4px; color: #274d67; font-size: 13px; }
.prototype-rule-strip span { padding: 5px 9px; border-radius: 999px; color: #59758a; background: #edf5fa; font-size: 12px; }
.import-anchor { display: block; scroll-margin-top: 90px; }
#import-preview-section { scroll-margin-top: 90px; }
#sheet-supplemental-section,
#sheet-legal-section { scroll-margin-top: 90px; }
.inline-confirmation {
  margin: 16px 0 0;
  padding: 13px 15px;
  border: 1px solid #f2c8cb;
  border-radius: 11px;
  color: #a9343b;
  background: #fff2f3;
  font-size: 13px;
  line-height: 1.65;
}
.rollback-confirmation {
  padding: 18px;
  border: 1px solid #f1cdd0;
  border-radius: 14px;
  background: #fff6f6;
}
.rollback-confirmation strong { color: #8f2e34; font-size: 17px; }
.rollback-confirmation p {
  margin: 9px 0 0;
  color: #6d5154;
  font-size: 13px;
  line-height: 1.7;
}

@media (max-width: 1180px) {
  .prototype-import-workspace { grid-template-columns: 1fr; }
  .prototype-warning-panel { display: grid; border-top: 1px solid #e5edf4; border-left: 0; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 10px; }
  .prototype-warning-panel header { grid-column: 1 / -1; }
  .prototype-warning-panel > div { margin-bottom: 0; }
}

@media (max-width: 900px) {
  .prototype-import-summary-grid { grid-template-columns: repeat(2,minmax(0,1fr)); }
  .prototype-sheet-grid { grid-template-columns: repeat(2,minmax(0,1fr)); }
  .prototype-validation-strip { grid-template-columns: repeat(3,minmax(0,1fr)); }
}

@media (max-width: 680px) {
  .imports-page-heading { align-items: stretch; flex-direction: column; }
  .imports-heading-actions { display: grid; grid-template-columns: 1fr 1fr; }
  .template-button,.new-import-button { justify-content: center; }
  .prototype-import-summary-grid { grid-template-columns: 1fr; }
  .prototype-summary-card { min-height: 108px; }
  .prototype-import-upload { padding: 14px; grid-template-columns: 1fr; }
  .prototype-import-upload > button { width: 100%; }
  .prototype-active-header { padding: 18px; }
  .prototype-file-heading { align-items: flex-start; }
  .prototype-excel-icon { width: 42px; height: 42px; }
  .prototype-file-heading h2 { overflow-wrap: anywhere; white-space: normal; }
  .prototype-active-state > button { display: none; }
  .prototype-import-stepper { padding: 18px; grid-template-columns: 1fr; gap: 14px; }
  .prototype-import-stepper li:not(:last-child)::after { top: 34px; bottom: -14px; left: 16px; width: 2px; height: auto; }
  .prototype-import-workspace > main { padding: 18px; }
  .prototype-sheet-recognition > header { align-items: stretch; flex-direction: column; }
  .prototype-sheet-grid { grid-template-columns: 1fr; }
  .prototype-validation-strip { grid-template-columns: repeat(2,minmax(0,1fr)); }
  .prototype-warning-panel { padding: 18px; grid-template-columns: 1fr; }
  .batch-validation-grid { padding: 16px; grid-template-columns: repeat(2,minmax(0,1fr)); }
  .batch-validation-grid article:last-child { grid-column: 1 / -1; }
  .prototype-rule-strip { padding: 12px 16px; }
}
</style>
