<script setup lang="ts">
import type {
  DepartmentCollectionDetail,
  DepartmentCollectionSummary,
  DepartmentCollectionSummaryItem,
  DashboardFocusItem,
  DashboardRiskDetail,
  DashboardRiskFilterOptions,
  DashboardRiskListItem,
  DashboardRiskListResponse,
  DashboardSummary,
  ManagerTodoDetail,
  ManagerTodoItem,
  ManagerTodoListResponse,
  ProjectRiskLevel,
  ResolvedRiskListResponse,
  RiskCollectionDetail,
  RiskCollectionListResponse,
  RiskTimelineDetail,
  RiskTimelineEventType,
  RiskTimelineListResponse,
} from "@risk-platform/contracts";
import { computed, onMounted, onUnmounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";

import { agentApi, type AgentHelpResponse } from "@/api/agent";
import { dashboardApi } from "@/api/dashboard";
import {
  weeklyReportsApi,
  type WeeklyProjectDetail,
  type WeeklyProjectSummary,
  type WeeklyReportResponse,
} from "@/api/weekly-reports";
import ModalDialog from "@/components/ModalDialog.vue";
import { useAgentConversation } from "@/composables/useAgentConversation";
import { useAuthStore } from "@/stores/auth";
import {
  agentErrorLabel,
  operationLabel,
  previewSummary,
} from "@/utils/agent-sse";
import {
  formatDateTime,
  formatWan,
  levelLabel,
} from "@/utils/dashboard";
import {
  levelCountsLabel,
  projectHasRisks,
  riskStatusLabel,
  staleLabel,
  summaryCount,
  weekRangeLabel,
} from "@/utils/weekly-reports";

type MetricKey = "projects" | "risks" | "high" | "remaining" | "collected";

const router = useRouter();
const auth = useAuthStore();
const loading = ref(true);
const listLoading = ref(false);
const error = ref("");
const summary = ref<DashboardSummary | null>(null);
const focusItems = ref<DashboardFocusItem[]>([]);
const options = ref<DashboardRiskFilterOptions>({
  categories: [],
  owners: [],
});
const riskPage = ref<DashboardRiskListResponse>({
  items: [],
  page: 1,
  pageSize: 20,
  total: 0,
});
const selectedRisk = ref<DashboardRiskDetail | null>(null);
const detailLoading = ref(false);
const departmentCollections = ref<DepartmentCollectionSummary | null>(null);
const departmentLoading = ref(false);
const selectedDepartment = ref<DepartmentCollectionDetail | null>(null);
const departmentDetailLoading = ref(false);
const managerTodos = ref<ManagerTodoListResponse | null>(null);
const todoPageSize = ref(20);
const todoLoading = ref(false);
const todoDetailLoading = ref(false);
const todoSaving = ref(false);
const selectedTodo = ref<ManagerTodoDetail | null>(null);
const riskCollections = ref<RiskCollectionListResponse | null>(null);
const collectionLoading = ref(false);
const collectionDetailLoading = ref(false);
const selectedCollection = ref<RiskCollectionDetail | null>(null);
const riskTimeline = ref<RiskTimelineListResponse | null>(null);
const timelineLoading = ref(false);
const timelineDetailLoading = ref(false);
const selectedTimeline = ref<RiskTimelineDetail | null>(null);
const resolvedRisks = ref<ResolvedRiskListResponse | null>(null);
const resolvedLoading = ref(false);
const lifecycleSaving = ref(false);
const lifecycleMode = ref<"resolve" | "reopen" | null>(null);
const lifecycleReason = ref("");
const selectedMetricKey = ref<MetricKey | null>(null);
const profileMenuOpen = ref(false);
const agentOpen = ref(false);
const agent = useAgentConversation();
const agentInput = ref("");
const agentHelp = ref<AgentHelpResponse | null>(null);
const agentSuggestions = [
  "当前有哪些高风险？",
  "风险项目待回款是多少？",
  "给出本周处理建议",
] as const;
const weeklyReport = ref<WeeklyReportResponse | null>(null);
const weeklyLoading = ref(false);
const weeklyError = ref("");
const selectedWeeklyProject = ref<WeeklyProjectSummary | null>(null);
const weeklyDetail = ref<WeeklyProjectDetail | null>(null);
const weeklyDetailLoading = ref(false);
const activeTab = ref("risks");
const filters = reactive({
  keyword: "",
  level: "" as ProjectRiskLevel | "",
  categoryId: "",
  owner: "",
});
const todoFilters = reactive({
  owner: "",
  status: "" as "" | "PENDING" | "IN_PROGRESS" | "COMPLETED",
});
const todoForm = reactive({
  status: "PENDING" as "PENDING" | "IN_PROGRESS" | "COMPLETED",
  assigneeName: "",
  dueDate: "",
  completionNote: "",
});
const collectionFilters = reactive({
  keyword: "",
  level: "" as ProjectRiskLevel | "",
  owner: "",
});
const timelineFilters = reactive({
  keyword: "",
  level: "" as ProjectRiskLevel | "",
  eventType: "" as RiskTimelineEventType | "",
  projectId: "",
});
const resolvedFilters = reactive({
  keyword: "",
  level: "" as ProjectRiskLevel | "",
  categoryId: "",
  owner: "",
});

const tabs = [
  { id: "risks", label: "项目风险清单", icon: "◎" },
  { id: "departments", label: "部门回款汇总", icon: "▥" },
  { id: "todos", label: "管理者待办", icon: "☑" },
  { id: "collections", label: "应收与回款", icon: "¥" },
  { id: "timeline", label: "风险时间线", icon: "◷" },
  { id: "resolved", label: "已解除风险", icon: "✓" },
] as const;

const adminEntry = computed(() => {
  if (auth.user?.permissions.includes("admin.user.manage")) {
    return "/admin/users";
  }
  if (auth.user?.permissions.includes("admin.role.manage")) {
    return "/admin/roles";
  }
  if (auth.user?.permissions.includes("admin.import.manage")) {
    return "/admin/imports";
  }
  return null;
});

const canManageTodos = computed(() =>
  Boolean(auth.user?.permissions.includes("risk.resolve")),
);
const canManageRisks = canManageTodos;

/** Agent drawer is only offered to roles granted `agent.use` (ADR 0019). */
const canUseAgent = computed(() =>
  Boolean(auth.user?.permissions.includes("agent.use")),
);

const totalPages = computed(() =>
  Math.max(1, Math.ceil(riskPage.value.total / riskPage.value.pageSize)),
);
const todoTotalPages = computed(() =>
  Math.max(
    1,
    Math.ceil(
      (managerTodos.value?.total ?? 0) /
        (managerTodos.value?.pageSize ?? todoPageSize.value),
    ),
  ),
);
const timelineTotalPages = computed(() =>
  Math.max(
    1,
    Math.ceil(
      (riskTimeline.value?.total ?? 0) /
        (riskTimeline.value?.pageSize ?? 20),
    ),
  ),
);
const resolvedTotalPages = computed(() =>
  Math.max(
    1,
    Math.ceil(
      (resolvedRisks.value?.total ?? 0) /
        (resolvedRisks.value?.pageSize ?? 20),
    ),
  ),
);

const metricCards = computed(() => [
  {
    key: "projects",
    label: "项目总数",
    value: summary.value ? String(summary.value.projectTotal) : "—",
    unit: "个",
    meta: `在交付项目 ${summary.value?.deliveryProjectTotal ?? "—"}`,
    tone: "blue",
    icon: "▣",
  },
  {
    key: "risks",
    label: "风险总数",
    value: summary.value ? String(summary.value.activeRiskTotal) : "—",
    unit: "项",
    meta: `涉及项目 ${summary.value?.riskProjectTotal ?? "—"}`,
    tone: "orange",
    icon: "!",
  },
  {
    key: "high",
    label: "高风险",
    value: summary.value ? String(summary.value.highRiskTotal) : "—",
    unit: "项",
    meta: "需优先关注",
    tone: "red",
    icon: "▲",
  },
  {
    key: "remaining",
    label: "待回款金额",
    scopeLabel: "风险项目",
    value: formatWan(summary.value?.riskRemainingAmountYuan),
    unit: "",
    meta: `数据完整项目 ${summary.value?.riskAmountCompleteProjectTotal ?? "—"}`,
    tone: "indigo",
    icon: "¥",
  },
  {
    key: "collected",
    label: "已回款",
    scopeLabel: "风险项目",
    value: formatWan(summary.value?.riskCollectedAmountYuan),
    unit: "",
    meta:
      summary.value?.riskCollectionCompletionRate === null ||
      summary.value?.riskCollectionCompletionRate === undefined
        ? "完成率待计算"
        : `风险项目完成率 ${summary.value.riskCollectionCompletionRate}%`,
    tone: "green",
    icon: "✓",
  },
]);

const metricDetail = computed(() => {
  if (!selectedMetricKey.value || !summary.value) return null;
  const data = summary.value;
  const riskComposition = ([
    ["高风险", data.highRiskTotal],
    ["中风险", data.mediumRiskTotal],
    ["低风险", data.lowRiskTotal],
    ["待确认", data.unknownRiskTotal],
  ] satisfies Array<[string, number]>)
    .filter(([, total]) => total > 0)
    .map(([label, total]) => `${label}${total}项`)
    .join("、");
  const riskSources = ([
    ["周报AI提炼", data.mailAiRiskTotal],
    ["日常上报", data.manualRiskTotal],
    ["项目清单Excel", data.excelRiskTotal],
    ["发函诉讼清单", data.litigationRiskTotal],
  ] satisfies Array<[string, number]>)
    .filter(([, total]) => total > 0)
    .map(([label, total]) => `${label}${total}项`)
    .join("，");
  const highRiskPriorityText = data.highRiskPriorityItems
    .map((item) => item.replace(/[。；，、]+$/u, ""))
    .join("、");
  const metricUpdatedAt = formatDateTime(data.updatedAt).replace(/\//g, "-");
  const details = {
    projects: {
      title: "项目总数详情",
      summary: [
        ["在交付项目", String(data.deliveryProjectTotal)],
        ["交付部门", String(data.deliveryDepartmentTotal)],
        ["本批新增", String(data.latestImportCreatedProjectTotal)],
      ],
      rows: [
        ["统计口径", "当前有效Excel导入批次中计入项目总数的在交付项目。"],
        ["数据批次", data.latestImportBatchCode ?? "暂无有效批次"],
        ["更新时间", metricUpdatedAt],
      ],
    },
    risks: {
      title: "风险总数详情",
      summary: [
        ["风险总数", String(data.activeRiskTotal)],
        ["涉及项目", String(data.riskProjectTotal)],
        ["本周新增", String(data.weeklyNewRiskTotal)],
      ],
      rows: [
        ["风险构成", riskComposition ? `${riskComposition}。` : "暂无风险数据。"],
        ["数据来源", riskSources ? `${riskSources}。` : "暂无风险数据。"],
        ["统计口径", "仅统计当前状态为有效且未解除的风险。"],
      ],
    },
    high: {
      title: "高风险详情",
      summary: [
        ["高风险", String(data.highRiskTotal)],
        ["涉及项目", String(data.highRiskProjectTotal)],
        ["本周新增", String(data.weeklyNewHighRiskTotal)],
      ],
      rows: [
        [
          "重点项目",
          data.highRiskFocusProjectNames.length > 0
            ? `${data.highRiskFocusProjectNames.join("、")}。`
            : "暂无高风险重点项目。",
        ],
        [
          "优先事项",
          highRiskPriorityText
            ? `${highRiskPriorityText}。`
            : "暂无高风险优先事项。",
        ],
        ["更新时间", metricUpdatedAt],
      ],
    },
    remaining: {
      title: "风险项目待回款详情",
      summary: [
        ["待回款", formatWan(data.riskRemainingAmountYuan).replace(" 万", "万")],
        ["风险项目", String(data.riskProjectTotal)],
        ["金额待补充", String(data.riskAmountMissingProjectTotal)],
      ],
      rows: [
        ["统计口径", "仅汇总存在有效风险项目的剩余待回款金额。"],
        ["空值规则", "Excel金额为空不按0计算。"],
        ["数据来源", "项目清单Excel与补充回款记录。"],
      ],
    },
    collected: {
      title: "风险项目已回款详情",
      summary: [
        ["已回款", formatWan(data.riskCollectedAmountYuan).replace(" 万", "万")],
        ["风险项目", String(data.riskProjectTotal)],
        [
          "完成率",
          data.riskCollectionCompletionRate === null
            ? "待计算"
            : `${data.riskCollectionCompletionRate}%`,
        ],
      ],
      rows: [
        ["统计口径", "仅汇总存在有效风险项目的实际已回款金额。"],
        ["数据批次", data.latestImportBatchCode ?? "暂无有效批次"],
        ["更新时间", metricUpdatedAt],
      ],
    },
  } as const;
  return details[selectedMetricKey.value];
});

function openMetricDetail(key: string): void {
  if (
    ["projects", "risks", "high", "remaining", "collected"].includes(key)
  ) {
    selectedMetricKey.value = key as MetricKey;
  }
}

function openAgent(): void {
  agentOpen.value = true;
  profileMenuOpen.value = false;
  void loadAgentHelp();
}

function closeAgent(): void {
  agentOpen.value = false;
}

async function loadAgentHelp(): Promise<void> {
  if (agentHelp.value) return;
  try {
    agentHelp.value = await agentApi.help();
  } catch {
    // The tool directory is optional context; the drawer still works without it.
  }
}

function sendAgent(prompt?: string): void {
  const message = (prompt ?? agentInput.value).trim();
  if (!message) return;
  agentInput.value = "";
  void agent.send(message);
}

async function loadWeeklyReport(): Promise<void> {
  weeklyLoading.value = true;
  weeklyError.value = "";
  try {
    weeklyReport.value = await weeklyReportsApi.current();
  } catch (requestError) {
    weeklyError.value =
      requestError instanceof Error
        ? requestError.message
        : "周报汇总加载失败";
    weeklyReport.value = null;
  } finally {
    weeklyLoading.value = false;
  }
}

async function openWeeklyProject(
  project: WeeklyProjectSummary,
): Promise<void> {
  if (!weeklyReport.value || !projectHasRisks(project)) return;
  selectedWeeklyProject.value = project;
  weeklyDetail.value = null;
  weeklyDetailLoading.value = true;
  try {
    weeklyDetail.value = await weeklyReportsApi.detail(
      weeklyReport.value.weekStart,
      project.project.id,
    );
  } catch {
    weeklyDetail.value = null;
  } finally {
    weeklyDetailLoading.value = false;
  }
}

function closeWeeklyProject(): void {
  selectedWeeklyProject.value = null;
  weeklyDetail.value = null;
}

async function loadDashboard(): Promise<void> {
  loading.value = true;
  error.value = "";
  try {
    const [summaryData, focusData, optionData] = await Promise.all([
      dashboardApi.summary(),
      dashboardApi.focus(),
      dashboardApi.riskOptions(),
    ]);
    summary.value = summaryData;
    focusItems.value = focusData;
    options.value = optionData;
    await loadRisks(1);
  } catch (requestError) {
    error.value =
      requestError instanceof Error
        ? requestError.message
        : "看板数据加载失败";
  } finally {
    loading.value = false;
  }
}

async function loadRisks(page = 1): Promise<void> {
  listLoading.value = true;
  error.value = "";
  try {
    riskPage.value = await dashboardApi.risks({
      keyword: filters.keyword.trim(),
      level: filters.level,
      categoryId: filters.categoryId,
      owner: filters.owner,
      page,
      pageSize: riskPage.value.pageSize,
    });
  } catch (requestError) {
    error.value =
      requestError instanceof Error
        ? requestError.message
        : "风险清单加载失败";
  } finally {
    listLoading.value = false;
  }
}

async function selectTab(tabId: string): Promise<void> {
  activeTab.value = tabId;
  if (tabId === "departments" && !departmentCollections.value) {
    await loadDepartmentCollections();
  }
  if (tabId === "todos" && !managerTodos.value) {
    await loadManagerTodos();
  }
  if (tabId === "collections" && !riskCollections.value) {
    await loadRiskCollections();
  }
  if (tabId === "timeline" && !riskTimeline.value) {
    await loadRiskTimeline(1);
  }
  if (tabId === "resolved" && !resolvedRisks.value) {
    await loadResolvedRisks(1);
  }
}

async function loadResolvedRisks(page = 1): Promise<void> {
  resolvedLoading.value = true;
  error.value = "";
  try {
    resolvedRisks.value = await dashboardApi.resolvedRisks({
      keyword: resolvedFilters.keyword.trim(),
      level: resolvedFilters.level,
      categoryId: resolvedFilters.categoryId,
      owner: resolvedFilters.owner,
      page,
      pageSize: resolvedRisks.value?.pageSize ?? 20,
    });
  } catch (requestError) {
    error.value =
      requestError instanceof Error
        ? requestError.message
        : "已解除风险加载失败";
  } finally {
    resolvedLoading.value = false;
  }
}

function resetResolvedFilters(): void {
  resolvedFilters.keyword = "";
  resolvedFilters.level = "";
  resolvedFilters.categoryId = "";
  resolvedFilters.owner = "";
  void loadResolvedRisks(1);
}

async function loadRiskCollections(): Promise<void> {
  collectionLoading.value = true;
  error.value = "";
  try {
    riskCollections.value = await dashboardApi.riskCollections({
      keyword: collectionFilters.keyword.trim(),
      level: collectionFilters.level,
      owner: collectionFilters.owner,
    });
  } catch (requestError) {
    error.value =
      requestError instanceof Error
        ? requestError.message
        : "应收与回款加载失败";
  } finally {
    collectionLoading.value = false;
  }
}

async function openRiskCollection(projectId: string): Promise<void> {
  collectionDetailLoading.value = true;
  selectedCollection.value = null;
  try {
    selectedCollection.value =
      await dashboardApi.riskCollectionDetail(projectId);
  } catch (requestError) {
    error.value =
      requestError instanceof Error
        ? requestError.message
        : "项目回款详情加载失败";
  } finally {
    collectionDetailLoading.value = false;
  }
}

function resetCollectionFilters(): void {
  collectionFilters.keyword = "";
  collectionFilters.level = "";
  collectionFilters.owner = "";
  void loadRiskCollections();
}

async function loadRiskTimeline(page = 1): Promise<void> {
  timelineLoading.value = true;
  error.value = "";
  try {
    riskTimeline.value = await dashboardApi.riskTimeline({
      keyword: timelineFilters.keyword.trim(),
      level: timelineFilters.level,
      eventType: timelineFilters.eventType,
      projectId: timelineFilters.projectId,
      page,
      pageSize: riskTimeline.value?.pageSize ?? 20,
    });
  } catch (requestError) {
    error.value =
      requestError instanceof Error
        ? requestError.message
        : "风险时间线加载失败";
  } finally {
    timelineLoading.value = false;
  }
}

async function openTimelineEvent(id: string): Promise<void> {
  timelineDetailLoading.value = true;
  selectedTimeline.value = null;
  try {
    selectedTimeline.value =
      await dashboardApi.riskTimelineDetail(id);
  } catch (requestError) {
    error.value =
      requestError instanceof Error
        ? requestError.message
        : "时间线详情加载失败";
  } finally {
    timelineDetailLoading.value = false;
  }
}

function resetTimelineFilters(): void {
  timelineFilters.keyword = "";
  timelineFilters.level = "";
  timelineFilters.eventType = "";
  timelineFilters.projectId = "";
  void loadRiskTimeline(1);
}

function actionStatusLabel(
  value: "PENDING" | "IN_PROGRESS" | "COMPLETED",
): string {
  return {
    PENDING: "待处理",
    IN_PROGRESS: "处理中",
    COMPLETED: "已完成",
  }[value];
}

function nextCollectionLabel(
  item: RiskCollectionListResponse["items"][number],
): string {
  if (item.nextCollection.amountYuan) {
    return `${item.nextCollection.label} · ${formatWan(item.nextCollection.amountYuan)}`;
  }
  return item.nextCollection.label;
}

async function loadManagerTodos(page = 1): Promise<void> {
  todoLoading.value = true;
  error.value = "";
  try {
    managerTodos.value = await dashboardApi.managerTodos({
      ...todoFilters,
      page,
      pageSize: todoPageSize.value,
    });
  } catch (requestError) {
    error.value =
      requestError instanceof Error
        ? requestError.message
        : "管理者待办加载失败";
  } finally {
    todoLoading.value = false;
  }
}

async function openTodo(item: Pick<ManagerTodoItem, "id">): Promise<void> {
  todoDetailLoading.value = true;
  selectedTodo.value = null;
  try {
    const detail = await dashboardApi.managerTodoDetail(item.id);
    selectedTodo.value = detail;
    todoForm.status = detail.status;
    todoForm.assigneeName = detail.assigneeName;
    todoForm.dueDate = detail.dueDate ?? "";
    todoForm.completionNote = detail.completionNote ?? "";
  } catch (requestError) {
    error.value =
      requestError instanceof Error
        ? requestError.message
        : "待办详情加载失败";
  } finally {
    todoDetailLoading.value = false;
  }
}

async function saveTodo(): Promise<void> {
  if (!selectedTodo.value || !canManageTodos.value) return;
  todoSaving.value = true;
  error.value = "";
  try {
    selectedTodo.value = await dashboardApi.updateManagerTodo(
      selectedTodo.value.id,
      {
        status: todoForm.status,
        assigneeName: todoForm.assigneeName.trim(),
        dueDate: todoForm.dueDate || null,
        completionNote: todoForm.completionNote.trim() || null,
      },
    );
    await loadManagerTodos();
  } catch (requestError) {
    error.value =
      requestError instanceof Error
        ? requestError.message
        : "待办事项更新失败";
  } finally {
    todoSaving.value = false;
  }
}

function urgencyLabel(value: ManagerTodoItem["urgency"]): string {
  return { EMERGENCY: "紧急", HIGH: "高", NORMAL: "普通" }[value];
}

function todoStatusLabel(value: ManagerTodoItem["status"]): string {
  return {
    PENDING: "待处理",
    IN_PROGRESS: "处理中",
    COMPLETED: "已完成",
  }[value];
}

async function loadDepartmentCollections(): Promise<void> {
  departmentLoading.value = true;
  error.value = "";
  try {
    departmentCollections.value =
      await dashboardApi.departmentCollections();
  } catch (requestError) {
    error.value =
      requestError instanceof Error
        ? requestError.message
        : "部门回款汇总加载失败";
  } finally {
    departmentLoading.value = false;
  }
}

async function openDepartment(
  department: DepartmentCollectionSummaryItem,
): Promise<void> {
  departmentDetailLoading.value = true;
  selectedDepartment.value = null;
  try {
    selectedDepartment.value =
      await dashboardApi.departmentCollectionDetail(
        department.departmentKey,
      );
  } catch (requestError) {
    error.value =
      requestError instanceof Error
        ? requestError.message
        : "部门回款明细加载失败";
  } finally {
    departmentDetailLoading.value = false;
  }
}

function collectionRateLabel(value: number | null): string {
  return value === null ? "待计算" : `${value.toFixed(1)}%`;
}

function resetFilters(): void {
  filters.keyword = "";
  filters.level = "";
  filters.categoryId = "";
  filters.owner = "";
  void loadRisks(1);
}

async function openRisk(item: DashboardRiskListItem): Promise<void> {
  detailLoading.value = true;
  selectedRisk.value = null;
  try {
    selectedRisk.value = await dashboardApi.riskDetail(item.id);
    lifecycleMode.value = null;
    lifecycleReason.value = "";
  } catch (requestError) {
    error.value =
      requestError instanceof Error
        ? requestError.message
        : "风险详情加载失败";
  } finally {
    detailLoading.value = false;
  }
}

function beginRiskLifecycle(mode: "resolve" | "reopen"): void {
  lifecycleMode.value = mode;
  lifecycleReason.value = "";
}

function cancelRiskLifecycle(): void {
  lifecycleMode.value = null;
  lifecycleReason.value = "";
}

function closeRiskDetail(): void {
  selectedRisk.value = null;
  cancelRiskLifecycle();
}

async function saveRiskLifecycle(): Promise<void> {
  if (
    !selectedRisk.value ||
    !lifecycleMode.value ||
    !canManageRisks.value
  ) {
    return;
  }
  const reason = lifecycleReason.value.trim();
  if (reason.length < 5) {
    error.value =
      lifecycleMode.value === "resolve"
        ? "解除原因至少填写5个字符"
        : "重新打开原因至少填写5个字符";
    return;
  }

  lifecycleSaving.value = true;
  error.value = "";
  try {
    selectedRisk.value =
      lifecycleMode.value === "resolve"
        ? await dashboardApi.resolveRisk(selectedRisk.value.id, {
            reason,
          })
        : await dashboardApi.reopenRisk(selectedRisk.value.id, {
            reason,
          });
    cancelRiskLifecycle();
    const refreshes: Array<Promise<unknown>> = [
      dashboardApi.summary().then((value) => {
        summary.value = value;
      }),
      dashboardApi.focus().then((value) => {
        focusItems.value = value;
      }),
      loadRisks(riskPage.value.page),
      loadResolvedRisks(resolvedRisks.value?.page ?? 1),
    ];
    if (managerTodos.value) refreshes.push(loadManagerTodos(managerTodos.value.page));
    if (riskTimeline.value) {
      refreshes.push(loadRiskTimeline(riskTimeline.value.page));
    }
    await Promise.all(refreshes);
  } catch (requestError) {
    error.value =
      requestError instanceof Error
        ? requestError.message
        : "风险状态更新失败";
  } finally {
    lifecycleSaving.value = false;
  }
}

async function logout(): Promise<void> {
  await auth.logout();
  await router.replace("/login");
}

onMounted(() => {
  void loadDashboard();
  void loadWeeklyReport();
});

onUnmounted(() => {
  agent.reset();
});
</script>

<template>
  <div class="dashboard-app">
    <header class="dashboard-header">
      <RouterLink class="dashboard-brand" to="/" aria-label="项目风险管理首页">
        <span class="dashboard-brand-mark" aria-hidden="true">
          <i></i><i></i><i></i>
        </span>
        <span>
          <strong>项目风险管理</strong>
          <small>PROJECT RISK INTELLIGENCE</small>
        </span>
      </RouterLink>
      <nav class="dashboard-primary-nav" aria-label="主导航">
        <RouterLink to="/" class="is-active">
          <span aria-hidden="true">⊞</span>
          Web 风险看板
        </RouterLink>
        <button v-if="canUseAgent" type="button" @click="openAgent">
          <span aria-hidden="true">◌</span>
          Agent 智能对话
        </button>
      </nav>
      <div class="dashboard-user-actions">
        <RouterLink v-if="adminEntry" :to="adminEntry" class="admin-entry">
          后台管理
        </RouterLink>
        <button type="button" class="dashboard-profile-button" @click="profileMenuOpen=!profileMenuOpen"><span class="dashboard-avatar" aria-hidden="true">{{ auth.user?.displayName.slice(0, 1) }}</span><span class="dashboard-user-name">{{ auth.user?.displayName }}</span><i>⌄</i></button>
        <div v-if="profileMenuOpen" class="dashboard-profile-menu"><RouterLink v-if="auth.user?.permissions.includes('mailbox.manage_self')" to="/mailbox-settings" @click="profileMenuOpen=false">个人邮箱配置</RouterLink><RouterLink v-if="auth.user?.permissions.includes('mailbox.sync_self')" to="/mail-sync-results" @click="profileMenuOpen=false">邮箱同步结果</RouterLink><RouterLink to="/change-password" @click="profileMenuOpen=false">修改密码</RouterLink><button type="button" @click="logout">退出登录</button></div>
      </div>
    </header>

    <main class="dashboard-main">
      <section class="dashboard-heading">
        <div>
          <p class="dashboard-eyebrow">PROJECT RISK OVERVIEW</p>
          <h1>Web 风险看板</h1>
          <p>从项目清单、回款数据与风险线索中，快速识别当前关键风险。</p>
        </div>
        <div class="dashboard-refresh">
          <span>
            <i aria-hidden="true"></i>
            数据已更新
            <small>{{ formatDateTime(summary?.updatedAt ?? null) }}</small>
          </span>
          <button type="button" :disabled="loading" @click="loadDashboard">
            ↻ {{ loading ? "刷新中" : "刷新数据" }}
          </button>
        </div>
      </section>

      <p v-if="error" class="dashboard-alert" role="alert">
        {{ error }}
      </p>

      <section class="dashboard-metrics" aria-label="风险看板关键指标">
        <button
          v-for="card in metricCards"
          :key="card.key"
          type="button"
          class="dashboard-metric-card"
          :class="`metric-${card.tone}`"
          @click="openMetricDetail(card.key)"
        >
          <span class="metric-icon" aria-hidden="true">{{ card.icon }}</span>
          <span class="metric-label">
            {{ card.label }}
            <small v-if="'scopeLabel' in card">{{ card.scopeLabel }}</small>
          </span>
          <strong>{{ card.value }} <small>{{ card.unit }}</small></strong>
          <span class="metric-meta">{{ card.meta }}</span>
        </button>
      </section>

      <section class="dashboard-focus" aria-labelledby="focus-heading">
        <header>
          <span class="focus-symbol" aria-hidden="true">!</span>
          <div>
            <p>WEEKLY FOCUS</p>
            <h2 id="focus-heading">本周重点关注</h2>
          </div>
          <small>按风险等级、待回款金额与更新时间综合排序</small>
        </header>
        <div v-if="focusItems.length" class="focus-list">
          <button
            v-for="item in focusItems"
            :key="item.id"
            type="button"
            @click="openRisk(item)"
          >
            <span class="risk-level-badge" :class="`level-${item.level.toLowerCase()}`">
              {{ levelLabel(item.level).slice(0, 1) }}
            </span>
            <span>
              <strong>{{ item.projectName }}</strong>
              <small>{{ item.description }}</small>
            </span>
            <span aria-hidden="true">›</span>
          </button>
        </div>
        <p v-else class="dashboard-empty">当前数据范围内暂无重点风险。</p>
      </section>

      <section class="dashboard-workbench">
        <nav class="dashboard-tabs" aria-label="业务功能">
          <button
            v-for="tab in tabs"
            :key="tab.id"
            type="button"
            :class="{ 'is-active': activeTab === tab.id }"
            @click="selectTab(tab.id)"
          >
            <span aria-hidden="true">{{ tab.icon }}</span>
            {{ tab.label }}
            <small v-if="tab.id === 'risks'">{{ riskPage.total }}</small>
            <small
              v-else-if="
                tab.id === 'departments' && departmentCollections
              "
            >
              {{ departmentCollections.items.length }}
            </small>
            <small v-else-if="tab.id === 'todos' && managerTodos">
              {{ managerTodos.summary.pending + managerTodos.summary.inProgress }}
            </small>
            <small v-else-if="tab.id === 'collections' && riskCollections">
              {{ riskCollections.riskProjectTotal }}
            </small>
            <small v-else-if="tab.id === 'timeline' && riskTimeline">
              {{ riskTimeline.summary.total }}
            </small>
            <small v-else-if="tab.id === 'resolved' && resolvedRisks">
              {{ resolvedRisks.total }}
            </small>
          </button>
        </nav>

        <div v-if="activeTab === 'risks'" class="risk-register">
          <header class="risk-register-heading">
            <div>
              <p>RISK REGISTER</p>
              <h2>项目风险清单 <small>共 {{ riskPage.total }} 条风险</small></h2>
            </div>
            <span>● 项目清单 Excel + 发函诉讼清单</span>
          </header>

          <form class="risk-filter-bar" @submit.prevent="loadRisks(1)">
            <label class="risk-search">
              <span aria-hidden="true">⌕</span>
              <input
                v-model="filters.keyword"
                type="search"
                placeholder="搜索项目或风险描述"
                aria-label="搜索项目或风险描述"
              />
            </label>
            <select v-model="filters.level" aria-label="风险等级">
              <option value="">全部等级</option>
              <option value="HIGH">高风险</option>
              <option value="MEDIUM">中风险</option>
              <option value="LOW">低风险</option>
              <option value="UNKNOWN">待确认</option>
            </select>
            <select v-model="filters.categoryId" aria-label="风险类别">
              <option value="">全部类别</option>
              <option
                v-for="category in options.categories"
                :key="category.id"
                :value="category.id"
              >
                {{ category.name }}
              </option>
            </select>
            <select v-model="filters.owner" aria-label="项目负责人">
              <option value="">全部负责人</option>
              <option v-for="owner in options.owners" :key="owner" :value="owner">
                {{ owner }}
              </option>
            </select>
            <button type="submit" class="risk-filter-submit">查询</button>
            <button type="button" class="risk-filter-reset" @click="resetFilters">
              重置
            </button>
          </form>

          <div class="risk-table-wrap" :class="{ 'is-loading': listLoading }">
            <table class="risk-table">
              <thead>
                <tr>
                  <th>等级</th>
                  <th>项目</th>
                  <th>类别</th>
                  <th>风险描述</th>
                  <th>上报人 / 来源</th>
                  <th>周次</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="item in riskPage.items"
                  :key="item.id"
                  tabindex="0"
                  @click="openRisk(item)"
                  @keydown.enter="openRisk(item)"
                >
                  <td data-label="等级">
                    <span class="risk-level-chip" :class="`level-${item.level.toLowerCase()}`">
                      {{ levelLabel(item.level) }}
                    </span>
                  </td>
                  <td data-label="项目" class="risk-project-cell">
                    <strong>{{ item.projectName }}</strong>
                    <small>负责人：{{ item.projectOwnerName || "待补充" }}</small>
                  </td>
                  <td data-label="类别">{{ item.category.name }}</td>
                  <td data-label="风险描述" class="risk-description-cell">
                    <strong>{{ item.description }}</strong>
                    <small>点击查看完整证据与建议措施</small>
                  </td>
                  <td data-label="上报人 / 来源">
                    {{ item.reporterName || "系统导入" }}
                    <small class="source-chip">{{ item.sourceLabel }}</small>
                  </td>
                  <td data-label="周次">{{ item.weekCode || "—" }}</td>
                </tr>
              </tbody>
            </table>
            <p v-if="!listLoading && riskPage.items.length === 0" class="dashboard-empty">
              没有符合当前筛选条件的风险。
            </p>
          </div>

          <footer v-if="riskPage.total > riskPage.pageSize" class="risk-pagination">
            <button
              type="button"
              :disabled="riskPage.page <= 1 || listLoading"
              @click="loadRisks(riskPage.page - 1)"
            >
              上一页
            </button>
            <span>第 {{ riskPage.page }} / {{ totalPages }} 页</span>
            <button
              type="button"
              :disabled="riskPage.page >= totalPages || listLoading"
              @click="loadRisks(riskPage.page + 1)"
            >
              下一页
            </button>
          </footer>
        </div>

        <div
          v-else-if="activeTab === 'departments'"
          class="department-collections"
        >
          <header class="risk-register-heading department-heading">
            <div>
              <p>COLLECTION BY DEPARTMENT</p>
              <h2>
                部门回款汇总
                <small>
                  金额单位：万元 · 仅统计已关联项目
                </small>
              </h2>
            </div>
            <span>
              ● 项目清单 Excel + 已匹配涵谷回款
            </span>
          </header>

          <div
            v-if="
              departmentCollections?.pendingSupplementalCount !== null &&
              departmentCollections?.pendingSupplementalCount !== undefined &&
              departmentCollections.pendingSupplementalCount > 0
            "
            class="collection-pending-notice"
          >
            <span aria-hidden="true">!</span>
            <div>
              <strong>
                还有
                {{ departmentCollections.pendingSupplementalCount }}
                条补充回款记录待关联
              </strong>
              <small>
                待分配合同应收
                {{
                  formatWan(
                    departmentCollections
                      .pendingSupplementalReceivableAmountYuan,
                  )
                }}，未计入下方部门合计。
              </small>
            </div>
            <RouterLink
              v-if="
                auth.user?.permissions.includes('admin.import.manage')
              "
              to="/admin/imports"
            >
              去关联项目
            </RouterLink>
          </div>

          <dl v-if="departmentCollections" class="collection-total-strip">
            <div>
              <dt>项目总数</dt>
              <dd>{{ departmentCollections.totals.projectTotal }} 个</dd>
            </div>
            <div>
              <dt>已统计应收</dt>
              <dd>
                {{
                  formatWan(
                    departmentCollections.totals.receivableAmountYuan,
                  )
                }}
              </dd>
            </div>
            <div>
              <dt>累计已收</dt>
              <dd>
                {{
                  formatWan(
                    departmentCollections.totals.collectedAmountYuan,
                  )
                }}
              </dd>
            </div>
            <div class="collection-remaining-total">
              <dt>剩余未回</dt>
              <dd>
                {{
                  formatWan(
                    departmentCollections.totals.remainingAmountYuan,
                  )
                }}
              </dd>
            </div>
            <div>
              <dt>数据完整项目</dt>
              <dd>
                {{
                  departmentCollections.totals
                    .amountCompleteProjectTotal
                }}
                /
                {{ departmentCollections.totals.projectTotal }}
              </dd>
            </div>
          </dl>

          <div
            class="department-table-wrap"
            :class="{ 'is-loading': departmentLoading }"
          >
            <table class="department-collection-table">
              <thead>
                <tr>
                  <th>部门</th>
                  <th>项目数</th>
                  <th>已统计应收</th>
                  <th>累计已收</th>
                  <th>剩余未回</th>
                  <th>完成率</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="department in departmentCollections?.items ?? []"
                  :key="department.departmentKey"
                  tabindex="0"
                  @click="openDepartment(department)"
                  @keydown.enter="openDepartment(department)"
                >
                  <td data-label="部门">
                    <strong>{{ department.departmentName }}</strong>
                    <small>
                      点击查看项目明细 · 缺失
                      {{ department.amountMissingProjectTotal }} 项
                    </small>
                  </td>
                  <td data-label="项目数">
                    {{ department.projectTotal }} 个
                  </td>
                  <td data-label="已统计应收">
                    {{ formatWan(department.receivableAmountYuan) }}
                  </td>
                  <td data-label="累计已收">
                    {{ formatWan(department.collectedAmountYuan) }}
                  </td>
                  <td data-label="剩余未回" class="collection-remaining">
                    {{ formatWan(department.remainingAmountYuan) }}
                  </td>
                  <td data-label="完成率">
                    <div class="collection-progress">
                      <span>
                        <i
                          :style="{
                            width: `${Math.min(
                              department.completionRate ?? 0,
                              100,
                            )}%`,
                          }"
                        ></i>
                      </span>
                      <strong>
                        {{ collectionRateLabel(department.completionRate) }}
                      </strong>
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
            <p
              v-if="
                !departmentLoading &&
                (departmentCollections?.items.length ?? 0) === 0
              "
              class="dashboard-empty"
            >
              当前数据范围内暂无部门项目。
            </p>
            <p v-if="departmentLoading" class="dashboard-empty">
              正在加载部门回款汇总…
            </p>
          </div>
        </div>

        <div v-else-if="activeTab === 'todos'" class="manager-todos">
          <header class="risk-register-heading todo-heading">
            <div>
              <p>MANAGEMENT ACTIONS</p>
              <h2>
                管理者待办事项
                <small>共 {{ managerTodos?.total ?? 0 }} 条待办 · 由风险建议与责任人自动形成</small>
              </h2>
            </div>
            <span>● 风险建议 + 处理跟踪</span>
          </header>

          <dl v-if="managerTodos" class="todo-summary-strip">
            <div class="todo-summary-emergency">
              <dt>紧急待办</dt>
              <dd>{{ managerTodos.summary.emergency }} 项</dd>
            </div>
            <div>
              <dt>待处理</dt>
              <dd>{{ managerTodos.summary.pending }} 项</dd>
            </div>
            <div>
              <dt>处理中</dt>
              <dd>{{ managerTodos.summary.inProgress }} 项</dd>
            </div>
            <div>
              <dt>已完成</dt>
              <dd>{{ managerTodos.summary.completed }} 项</dd>
            </div>
          </dl>

          <section v-if="managerTodos?.schedule.length" class="todo-schedule-card">
            <header>
              <div>
                <span>WEEKLY PLAN</span>
                <h3>本周日程建议</h3>
              </div>
              <small>按紧急度和截止日期智能排序</small>
            </header>
            <ol>
              <li v-for="schedule in managerTodos.schedule" :key="schedule.actionItemId">
                <button type="button" @click="openTodo({ id: schedule.actionItemId })">
                  <time :datetime="schedule.date">
                    <strong>{{ schedule.weekday }}</strong>
                    <small>{{ schedule.date.slice(5) }}</small>
                  </time>
                  <span>
                    <strong>{{ schedule.title }}</strong>
                    <small>{{ schedule.projectName }} · {{ schedule.assigneeName }}</small>
                  </span>
                  <i :class="`urgency-${schedule.urgency.toLowerCase()}`">
                    {{ urgencyLabel(schedule.urgency) }}
                  </i>
                </button>
              </li>
            </ol>
          </section>

          <form class="todo-filter-bar" @submit.prevent="loadManagerTodos(1)">
            <label>
              <span>负责人</span>
              <select v-model="todoFilters.owner">
                <option value="">全部负责人</option>
                <option v-for="owner in managerTodos?.owners ?? []" :key="owner" :value="owner">
                  {{ owner }}
                </option>
              </select>
            </label>
            <label>
              <span>处理状态</span>
              <select v-model="todoFilters.status">
                <option value="">全部状态</option>
                <option value="PENDING">待处理</option>
                <option value="IN_PROGRESS">处理中</option>
                <option value="COMPLETED">已完成</option>
              </select>
            </label>
            <button type="submit" :disabled="todoLoading">
              {{ todoLoading ? "查询中" : "查询" }}
            </button>
            <button
              type="button"
              @click="todoFilters.owner = ''; todoFilters.status = ''; loadManagerTodos(1)"
            >
              重置
            </button>
          </form>

          <div class="todo-table-wrap" :class="{ 'is-loading': todoLoading }">
            <table class="todo-table">
              <thead>
                <tr>
                  <th>紧急度</th>
                  <th>项目</th>
                  <th>待办事项</th>
                  <th>负责人</th>
                  <th>类型</th>
                  <th>状态</th>
                  <th>截止日期</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="item in managerTodos?.items ?? []"
                  :key="item.id"
                  tabindex="0"
                  @click="openTodo(item)"
                  @keydown.enter="openTodo(item)"
                >
                  <td data-label="紧急度">
                    <span class="todo-urgency" :class="`urgency-${item.urgency.toLowerCase()}`">
                      {{ urgencyLabel(item.urgency) }}
                    </span>
                  </td>
                  <td data-label="项目">
                    <strong>{{ item.projectName }}</strong>
                    <small>{{ item.departmentName || "未分配部门" }}</small>
                  </td>
                  <td data-label="待办事项" class="todo-title-cell">
                    <strong>{{ item.title }}</strong>
                    <small>{{ item.description }}</small>
                  </td>
                  <td data-label="负责人">{{ item.assigneeName }}</td>
                  <td data-label="类型">{{ item.typeLabel }}</td>
                  <td data-label="状态">
                    <span class="todo-status" :class="`status-${item.status.toLowerCase()}`">
                      {{ todoStatusLabel(item.status) }}
                    </span>
                  </td>
                  <td data-label="截止日期">{{ item.dueDate || "待安排" }}</td>
                </tr>
              </tbody>
            </table>
            <p v-if="!todoLoading && !(managerTodos?.items.length)" class="dashboard-empty">
              当前筛选条件下暂无待办事项。
            </p>
            <p v-if="todoLoading" class="dashboard-empty">正在加载管理者待办…</p>
          </div>

          <footer
            v-if="managerTodos && managerTodos.total > managerTodos.pageSize"
            class="risk-pagination"
          >
            <button
              type="button"
              :disabled="managerTodos.page <= 1 || todoLoading"
              @click="loadManagerTodos(managerTodos.page - 1)"
            >
              上一页
            </button>
            <span>第 {{ managerTodos.page }} / {{ todoTotalPages }} 页</span>
            <button
              type="button"
              :disabled="managerTodos.page >= todoTotalPages || todoLoading"
              @click="loadManagerTodos(managerTodos.page + 1)"
            >
              下一页
            </button>
          </footer>
        </div>

        <div
          v-else-if="activeTab === 'collections'"
          class="risk-collections"
        >
          <header class="risk-register-heading collection-heading">
            <div>
              <p>RISK COLLECTION</p>
              <h2>
                应收账款与回款
                <small>仅统计当前存在有效风险的项目</small>
              </h2>
            </div>
            <span>● 项目清单 Excel + 已匹配涵谷回款</span>
          </header>

          <dl v-if="riskCollections" class="risk-collection-summary">
            <div>
              <dt>风险项目</dt>
              <dd>{{ riskCollections.riskProjectTotal }} 个</dd>
            </div>
            <div>
              <dt>已统计应收</dt>
              <dd>{{ formatWan(riskCollections.totals.receivableAmountYuan) }}</dd>
            </div>
            <div>
              <dt>累计已回款</dt>
              <dd>{{ formatWan(riskCollections.totals.collectedAmountYuan) }}</dd>
            </div>
            <div class="collection-remaining-total">
              <dt>剩余待回款</dt>
              <dd>{{ formatWan(riskCollections.totals.remainingAmountYuan) }}</dd>
            </div>
            <div>
              <dt>金额完整项目</dt>
              <dd>
                {{ riskCollections.totals.amountCompleteProjectTotal }}
                /
                {{ riskCollections.riskProjectTotal }}
              </dd>
            </div>
          </dl>

          <form
            class="collection-filter-bar"
            @submit.prevent="loadRiskCollections"
          >
            <label class="risk-search">
              <span aria-hidden="true">⌕</span>
              <input
                v-model="collectionFilters.keyword"
                type="search"
                placeholder="搜索项目、编码或回款进展"
                aria-label="搜索项目、编码或回款进展"
              />
            </label>
            <select v-model="collectionFilters.level" aria-label="风险等级">
              <option value="">全部风险等级</option>
              <option value="HIGH">高风险</option>
              <option value="MEDIUM">中风险</option>
              <option value="LOW">低风险</option>
              <option value="UNKNOWN">待确认</option>
            </select>
            <select v-model="collectionFilters.owner" aria-label="项目负责人">
              <option value="">全部负责人</option>
              <option
                v-for="owner in riskCollections?.owners ?? []"
                :key="owner"
                :value="owner"
              >
                {{ owner }}
              </option>
            </select>
            <button type="submit" :disabled="collectionLoading">
              {{ collectionLoading ? "查询中" : "查询" }}
            </button>
            <button type="button" @click="resetCollectionFilters">重置</button>
          </form>

          <div
            class="risk-collection-table-wrap"
            :class="{ 'is-loading': collectionLoading }"
          >
            <table class="risk-collection-table">
              <thead>
                <tr>
                  <th>项目</th>
                  <th>风险</th>
                  <th>计划金额</th>
                  <th>已回款</th>
                  <th>待回款</th>
                  <th>下一笔回款</th>
                  <th>进度</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="item in riskCollections?.items ?? []"
                  :key="item.projectId"
                  tabindex="0"
                  @click="openRiskCollection(item.projectId)"
                  @keydown.enter="openRiskCollection(item.projectId)"
                >
                  <td data-label="项目" class="collection-project-cell">
                    <strong>{{ item.projectName }}</strong>
                    <small>
                      {{ item.externalCode || "无项目编码" }} ·
                      {{ item.ownerName || "负责人待补充" }}
                    </small>
                  </td>
                  <td data-label="风险">
                    <span
                      class="risk-level-chip"
                      :class="`level-${item.riskLevel.toLowerCase()}`"
                    >
                      {{ levelLabel(item.riskLevel) }}
                    </span>
                    <small class="collection-risk-count">
                      {{ item.activeRiskTotal }} 项
                    </small>
                  </td>
                  <td data-label="计划金额">
                    {{ formatWan(item.receivableAmountYuan) }}
                  </td>
                  <td data-label="已回款">
                    {{ formatWan(item.collectedAmountYuan) }}
                  </td>
                  <td data-label="待回款" class="collection-remaining">
                    {{ formatWan(item.remainingAmountYuan) }}
                  </td>
                  <td data-label="下一笔回款" class="next-collection-cell">
                    <strong>{{ nextCollectionLabel(item) }}</strong>
                    <small>{{ item.amountSourceLabel }}</small>
                  </td>
                  <td data-label="进度">
                    <div class="collection-progress">
                      <span>
                        <i
                          :style="{
                            width: `${Math.min(item.completionRate ?? 0, 100)}%`,
                          }"
                        ></i>
                      </span>
                      <strong>{{ collectionRateLabel(item.completionRate) }}</strong>
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
            <p
              v-if="
                !collectionLoading &&
                (riskCollections?.items.length ?? 0) === 0
              "
              class="dashboard-empty"
            >
              当前筛选条件下暂无风险项目回款记录。
            </p>
            <p v-if="collectionLoading" class="dashboard-empty">
              正在加载应收与回款…
            </p>
          </div>
        </div>

        <div
          v-else-if="activeTab === 'timeline'"
          class="risk-timeline"
        >
          <header class="risk-register-heading timeline-heading">
            <div>
              <p>RISK TIMELINE</p>
              <h2>
                风险进展时间线
                <small>
                  共 {{ riskTimeline?.summary.total ?? 0 }} 条事件
                </small>
              </h2>
            </div>
            <span>
              ● 风险识别、等级变化、待办推进与解除全程留痕
            </span>
          </header>

          <dl v-if="riskTimeline" class="timeline-summary">
            <div>
              <dt>全部事件</dt>
              <dd>{{ riskTimeline.summary.total }}</dd>
            </div>
            <div>
              <dt>风险新增</dt>
              <dd>{{ riskTimeline.summary.riskCreated }}</dd>
            </div>
            <div>
              <dt>风险变化</dt>
              <dd>{{ riskTimeline.summary.riskChanged }}</dd>
            </div>
            <div>
              <dt>处理推进</dt>
              <dd>{{ riskTimeline.summary.actionProgress }}</dd>
            </div>
            <div class="timeline-resolved-total">
              <dt>风险解除</dt>
              <dd>{{ riskTimeline.summary.resolved }}</dd>
            </div>
          </dl>

          <form
            class="timeline-filter-bar"
            @submit.prevent="loadRiskTimeline(1)"
          >
            <label class="risk-search">
              <span aria-hidden="true">⌕</span>
              <input
                v-model="timelineFilters.keyword"
                type="search"
                placeholder="搜索项目、风险或事件内容"
                aria-label="搜索项目、风险或事件内容"
              />
            </label>
            <select
              v-model="timelineFilters.eventType"
              aria-label="事件类型"
            >
              <option value="">全部事件类型</option>
              <option value="RISK_CREATED">新增风险</option>
              <option value="RISK_UPDATED">风险更新</option>
              <option value="LEVEL_CHANGED">等级变化</option>
              <option value="ACTION_CREATED">生成待办</option>
              <option value="ACTION_UPDATED">待办更新</option>
              <option value="ACTION_STATUS_CHANGED">处理推进</option>
              <option value="ACTION_COMPLETED">待办完成</option>
              <option value="RISK_RESOLVED">风险解除</option>
              <option value="RISK_REOPENED">风险重启</option>
            </select>
            <select v-model="timelineFilters.level" aria-label="风险等级">
              <option value="">全部风险等级</option>
              <option value="HIGH">高风险</option>
              <option value="MEDIUM">中风险</option>
              <option value="LOW">低风险</option>
              <option value="UNKNOWN">待确认</option>
            </select>
            <select v-model="timelineFilters.projectId" aria-label="项目">
              <option value="">全部项目</option>
              <option
                v-for="project in riskTimeline?.projects ?? []"
                :key="project.id"
                :value="project.id"
              >
                {{ project.name }}
              </option>
            </select>
            <button type="submit" :disabled="timelineLoading">
              {{ timelineLoading ? "查询中" : "查询" }}
            </button>
            <button type="button" @click="resetTimelineFilters">
              重置
            </button>
          </form>

          <div
            class="timeline-list"
            :class="{ 'is-loading': timelineLoading }"
          >
            <button
              v-for="item in riskTimeline?.items ?? []"
              :key="item.id"
              type="button"
              class="timeline-item"
              :class="`tone-${item.tone.toLowerCase()}`"
              @click="openTimelineEvent(item.id)"
            >
              <span class="timeline-marker" aria-hidden="true"></span>
              <time :datetime="item.occurredAt">
                {{ formatDateTime(item.occurredAt) }}
              </time>
              <span class="timeline-event-content">
                <span class="timeline-event-heading">
                  <strong>{{ item.projectName }}</strong>
                  <span
                    class="timeline-event-chip"
                    :class="`tone-${item.tone.toLowerCase()}`"
                  >
                    {{ item.eventLabel }}
                  </span>
                  <span
                    class="risk-level-chip"
                    :class="`level-${item.riskLevel.toLowerCase()}`"
                  >
                    {{ levelLabel(item.riskLevel) }}
                  </span>
                </span>
                <strong>{{ item.title }}</strong>
                <small>{{ item.description }}</small>
                <span class="timeline-event-meta">
                  {{ item.categoryName }} · {{ item.actorName }} ·
                  {{ item.sourceLabel }}
                </span>
              </span>
              <span class="timeline-arrow" aria-hidden="true">›</span>
            </button>
            <p
              v-if="
                !timelineLoading &&
                (riskTimeline?.items.length ?? 0) === 0
              "
              class="dashboard-empty"
            >
              当前筛选条件下暂无风险进展事件。
            </p>
            <p v-if="timelineLoading" class="dashboard-empty">
              正在加载风险时间线…
            </p>
          </div>

          <footer
            v-if="
              (riskTimeline?.total ?? 0) >
              (riskTimeline?.pageSize ?? 20)
            "
            class="risk-pagination timeline-pagination"
          >
            <button
              type="button"
              :disabled="
                (riskTimeline?.page ?? 1) <= 1 || timelineLoading
              "
              @click="loadRiskTimeline((riskTimeline?.page ?? 1) - 1)"
            >
              上一页
            </button>
            <span>
              第 {{ riskTimeline?.page ?? 1 }} /
              {{ timelineTotalPages }} 页
            </span>
            <button
              type="button"
              :disabled="
                (riskTimeline?.page ?? 1) >= timelineTotalPages ||
                timelineLoading
              "
              @click="loadRiskTimeline((riskTimeline?.page ?? 1) + 1)"
            >
              下一页
            </button>
          </footer>
        </div>

        <div
          v-else-if="activeTab === 'resolved'"
          class="resolved-risks"
        >
          <header class="risk-register-heading resolved-heading">
            <div>
              <p>RESOLVED RISKS</p>
              <h2>
                已解除风险
                <small>共 {{ resolvedRisks?.total ?? 0 }} 条记录</small>
              </h2>
            </div>
            <span>● 日常解除后自动进入历史记录，可按权限重新打开</span>
          </header>

          <form
            class="risk-filter-bar resolved-filter-bar"
            @submit.prevent="loadResolvedRisks(1)"
          >
            <label class="risk-search">
              <span aria-hidden="true">⌕</span>
              <input
                v-model="resolvedFilters.keyword"
                type="search"
                placeholder="搜索项目、风险或解除原因"
                aria-label="搜索项目、风险或解除原因"
              />
            </label>
            <select v-model="resolvedFilters.level" aria-label="原风险等级">
              <option value="">全部原风险等级</option>
              <option value="HIGH">高风险</option>
              <option value="MEDIUM">中风险</option>
              <option value="LOW">低风险</option>
              <option value="UNKNOWN">待确认</option>
            </select>
            <select v-model="resolvedFilters.categoryId" aria-label="原风险类别">
              <option value="">全部原风险类别</option>
              <option
                v-for="category in options.categories"
                :key="category.id"
                :value="category.id"
              >
                {{ category.name }}
              </option>
            </select>
            <select v-model="resolvedFilters.owner" aria-label="项目负责人">
              <option value="">全部负责人</option>
              <option
                v-for="owner in resolvedRisks?.owners ?? []"
                :key="owner"
                :value="owner"
              >
                {{ owner }}
              </option>
            </select>
            <button
              type="submit"
              class="risk-filter-submit"
              :disabled="resolvedLoading"
            >
              {{ resolvedLoading ? "查询中" : "查询" }}
            </button>
            <button
              type="button"
              class="risk-filter-reset"
              @click="resetResolvedFilters"
            >
              重置
            </button>
          </form>

          <div
            class="risk-table-wrap"
            :class="{ 'is-loading': resolvedLoading }"
          >
            <table class="risk-table resolved-risk-table">
              <thead>
                <tr>
                  <th>风险描述</th>
                  <th>项目</th>
                  <th>原等级</th>
                  <th>原类别</th>
                  <th>解除信息</th>
                  <th>解除原因</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="item in resolvedRisks?.items ?? []"
                  :key="item.id"
                  tabindex="0"
                  @click="openRisk(item)"
                  @keydown.enter="openRisk(item)"
                >
                  <td data-label="风险描述" class="risk-description-cell">
                    <strong>{{ item.description }}</strong>
                    <small>{{ item.sourceLabel }}</small>
                  </td>
                  <td data-label="项目" class="risk-project-cell">
                    <strong>{{ item.projectName }}</strong>
                    <small>
                      负责人：{{ item.projectOwnerName || "待补充" }}
                    </small>
                  </td>
                  <td data-label="原等级">
                    <span
                      class="risk-level-chip"
                      :class="`level-${item.level.toLowerCase()}`"
                    >
                      {{ levelLabel(item.level) }}
                    </span>
                  </td>
                  <td data-label="原类别">{{ item.category.name }}</td>
                  <td data-label="解除信息">
                    <strong>{{ formatDateTime(item.resolvedAt) }}</strong>
                    <small>解除人：{{ item.resolvedByName }}</small>
                  </td>
                  <td data-label="解除原因" class="resolved-reason-cell">
                    {{ item.resolutionReason }}
                  </td>
                </tr>
              </tbody>
            </table>
            <p
              v-if="
                !resolvedLoading &&
                (resolvedRisks?.items.length ?? 0) === 0
              "
              class="dashboard-empty"
            >
              当前筛选条件下暂无已解除风险。
            </p>
            <p v-if="resolvedLoading" class="dashboard-empty">
              正在加载已解除风险…
            </p>
          </div>

          <footer
            v-if="
              (resolvedRisks?.total ?? 0) >
              (resolvedRisks?.pageSize ?? 20)
            "
            class="risk-pagination"
          >
            <button
              type="button"
              :disabled="
                (resolvedRisks?.page ?? 1) <= 1 || resolvedLoading
              "
              @click="loadResolvedRisks((resolvedRisks?.page ?? 1) - 1)"
            >
              上一页
            </button>
            <span>
              第 {{ resolvedRisks?.page ?? 1 }} /
              {{ resolvedTotalPages }} 页
            </span>
            <button
              type="button"
              :disabled="
                (resolvedRisks?.page ?? 1) >= resolvedTotalPages ||
                resolvedLoading
              "
              @click="loadResolvedRisks((resolvedRisks?.page ?? 1) + 1)"
            >
              下一页
            </button>
          </footer>
        </div>

        <div v-else class="dashboard-phase-placeholder">
          <span aria-hidden="true">◇</span>
          <h2>{{ tabs.find((tab) => tab.id === activeTab)?.label }}</h2>
          <p>功能模块保持在既定范围内，将在后续计划中接入真实数据与交互。</p>
          <button type="button" @click="activeTab = 'risks'">返回风险清单</button>
        </div>
      </section>

      <section class="weekly-report-panel">
        <header>
          <div>
            <p>WEEKLY REPORTS</p>
            <h2>
              本周周报风险汇总
              <small v-if="weeklyReport">
                {{ weekRangeLabel(weeklyReport) }} · 已分析
                {{ summaryCount(weeklyReport.summary, "reportCount") ?? "—" }} 封
                · 风险 {{ summaryCount(weeklyReport.summary, "riskCount") ?? "—" }} 项
              </small>
            </h2>
          </div>
          <RouterLink
            v-if="auth.user?.permissions.includes('mailbox.sync_self')"
            to="/mail-sync-results"
          >
            同步周报
          </RouterLink>
        </header>

        <div v-if="weeklyLoading" class="weekly-report-state is-loading" role="status">
          <span class="weekly-state-icon" aria-hidden="true">↻</span>
          <div>
            <strong>正在加载周报汇总</strong>
            <p>正在整理本周已识别的项目风险。</p>
          </div>
        </div>

        <div
          v-else-if="weeklyError"
          class="weekly-report-state is-error"
          role="alert"
        >
          <span class="weekly-state-icon" aria-hidden="true">!</span>
          <div>
            <strong>周报汇总暂不可用</strong>
            <p>{{ weeklyError }}</p>
            <small>请稍后重试；同步周报操作不受影响。</small>
          </div>
          <button class="admin-outline-button weekly-state-button" type="button" @click="loadWeeklyReport">重试</button>
        </div>

        <div
          v-else-if="weeklyReport && weeklyReport.projects.length"
          class="weekly-report-table-wrap"
        >
          <p v-if="weeklyReport.stale" class="weekly-stale-note">
            {{ staleLabel(weeklyReport.stale) }}
          </p>
          <table>
            <thead>
              <tr>
                <th>项目</th>
                <th>风险数</th>
                <th>等级分布</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="project in weeklyReport.projects"
                :key="project.project.id"
                :tabindex="projectHasRisks(project) ? 0 : -1"
                @click="openWeeklyProject(project)"
                @keydown.enter="openWeeklyProject(project)"
              >
                <td><strong>{{ project.project.name }}</strong></td>
                <td>{{ project.riskCount }}</td>
                <td>{{ levelCountsLabel(project.riskLevelCounts as Record<string, unknown>) }}</td>
                <td>
                  <small v-if="projectHasRisks(project)">点击查看风险明细</small>
                  <small v-else>本周无风险</small>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div v-else class="weekly-report-state is-empty">
          <span class="weekly-state-icon" aria-hidden="true">○</span>
          <div>
            <strong>本周暂无周报风险汇总</strong>
            <p>同步并完成识别后，项目风险会显示在这里。</p>
          </div>
        </div>
      </section>
    </main>

    <aside
      v-if="agentOpen"
      class="agent-drawer"
      :class="{ 'has-no-messages': !agent.state.messages.length && !agent.state.streamingText && agent.state.status === 'idle' }"
      aria-label="Agent智能对话"
    >
      <header>
        <div>
          <p>AGENT ASSISTANT</p>
          <h2>Agent 智能对话</h2>
          <span>仅使用您有权访问的数据</span>
        </div>
        <button type="button" aria-label="关闭" @click="closeAgent">×</button>
      </header>

      <div v-if="agentHelp && agentHelp.tools.length" class="agent-tools">
        <small>可用能力：</small>
        <div class="agent-tool-list">
          <span v-for="tool in agentHelp.tools" :key="tool.name">{{ tool.name }}</span>
        </div>
      </div>

      <div v-if="!agent.state.messages.length && agent.state.status === 'idle'" class="agent-suggestions">
        <button
          v-for="prompt in agentSuggestions"
          :key="prompt"
          type="button"
          @click="sendAgent(prompt)"
        >
          {{ prompt }}
        </button>
      </div>

      <div class="agent-messages">
        <article
          v-for="message in agent.state.messages"
          :key="message.id"
          :class="message.role.toLowerCase()"
        >
          <b>{{ message.role === 'ASSISTANT' ? 'AI' : '我' }}</b>
          <p>{{ message.content }}</p>
          <small v-if="message.dataAsOf">数据截至 {{ formatDateTime(message.dataAsOf) }}</small>
        </article>

        <article v-if="agent.state.streamingText" class="assistant">
          <b>AI</b>
          <p>{{ agent.state.streamingText }}</p>
        </article>

        <p v-if="agent.state.status === 'loading'" class="agent-progress" role="status">
          正在发起对话…
        </p>
        <p
          v-else-if="agent.state.status === 'streaming' && agent.state.progress"
          class="agent-progress"
          role="status"
        >
          {{ agent.state.progress.message || agent.state.progress.stage }}
        </p>
      </div>

      <section
        v-if="agent.state.preview"
        class="agent-preview"
        :class="`preview-${agent.state.preview.status}`"
        aria-label="Agent 预览确认"
      >
        <header>
          <span class="agent-preview-operation">
            {{ operationLabel(agent.state.preview.operation) }}
          </span>
          <small>需确认后生效</small>
        </header>
        <p class="agent-preview-summary">
          {{ previewSummary(agent.state.preview.content) }}
        </p>
        <p v-if="agent.state.preview.content.description" class="agent-preview-desc">
          {{ agent.state.preview.content.description }}
        </p>

        <p v-if="agent.state.preview.status === 'confirmed' && agent.state.preview.result" class="agent-preview-feedback">
          已确认：{{ agent.state.preview.result.resourceType }}
          {{ formatDateTime(agent.state.preview.result.completedAt) }}
        </p>
        <p v-if="agent.state.preview.status === 'failed'" class="agent-preview-feedback" role="alert">
          {{ agent.state.preview.failureMessage }}
        </p>

        <div class="agent-preview-actions">
          <button
            v-if="agent.state.preview.status === 'pending' || agent.state.preview.status === 'failed'"
            type="button"
            @click="agent.confirmPreview()"
          >
            确认执行
          </button>
          <button
            v-if="agent.state.preview.status === 'confirmed'"
            type="button"
            @click="agent.dismissPreview()"
          >
            完成
          </button>
        </div>
      </section>

      <p v-if="agent.state.error" class="agent-error" role="alert">
        {{ agentErrorLabel(agent.state.error.code, agent.state.error.message) }}
        <button v-if="agent.state.error.retryable" type="button" @click="agent.retry()">
          重试
        </button>
      </p>

      <p v-if="agent.state.status === 'disconnected'" class="agent-error" role="status">
        连接已断开，事件流可能未完成。
        <button type="button" @click="agent.reconnect()">重新连接</button>
      </p>

      <form @submit.prevent="sendAgent()">
        <textarea
          v-model="agentInput"
          :disabled="agent.sending.value"
          placeholder="输入关于项目、风险、回款或待办的问题"
        ></textarea>
        <button type="submit" :disabled="agent.sending.value">
          {{ agent.sending.value ? "处理中" : "发送" }}
        </button>
      </form>
    </aside>
    <button
      v-if="agentOpen"
      class="agent-backdrop"
      type="button"
      aria-label="关闭Agent"
      @click="closeAgent"
    ></button>

    <div v-if="detailLoading" class="dashboard-detail-loading" role="status">
      正在加载风险详情…
    </div>
    <div
      v-if="departmentDetailLoading"
      class="dashboard-detail-loading"
      role="status"
    >
      正在加载部门回款明细…
    </div>
    <div v-if="todoDetailLoading" class="dashboard-detail-loading" role="status">
      正在加载待办详情…
    </div>
    <div
      v-if="collectionDetailLoading"
      class="dashboard-detail-loading"
      role="status"
    >
      正在加载项目回款详情…
    </div>
    <div
      v-if="timelineDetailLoading"
      class="dashboard-detail-loading"
      role="status"
    >
      正在加载时间线详情…
    </div>

    <ModalDialog
      v-if="selectedTimeline"
      eyebrow="TIMELINE EVENT DETAIL"
      :title="selectedTimeline.projectName"
      @close="selectedTimeline = null"
    >
      <div class="timeline-detail-meta">
        <span
          class="timeline-event-chip"
          :class="`tone-${selectedTimeline.tone.toLowerCase()}`"
        >
          {{ selectedTimeline.eventLabel }}
        </span>
        <span
          class="risk-level-chip"
          :class="`level-${selectedTimeline.riskLevel.toLowerCase()}`"
        >
          {{ levelLabel(selectedTimeline.riskLevel) }}
        </span>
        <span>{{ selectedTimeline.categoryName }}</span>
        <span>{{ selectedTimeline.sourceLabel }}</span>
      </div>

      <section class="timeline-detail-event">
        <header>
          <div>
            <small>事件时间</small>
            <strong>
              {{ formatDateTime(selectedTimeline.occurredAt) }}
            </strong>
          </div>
          <div>
            <small>操作人</small>
            <strong>{{ selectedTimeline.actorName }}</strong>
          </div>
        </header>
        <h3>{{ selectedTimeline.title }}</h3>
        <p>{{ selectedTimeline.description }}</p>
        <dl
          v-if="
            selectedTimeline.fromValue ||
            selectedTimeline.toValue
          "
        >
          <div>
            <dt>变更前</dt>
            <dd>{{ selectedTimeline.fromValue || "—" }}</dd>
          </div>
          <div>
            <dt>变更后</dt>
            <dd>{{ selectedTimeline.toValue || "—" }}</dd>
          </div>
        </dl>
      </section>

      <section class="timeline-related-risk">
        <header>
          <h3>关联风险</h3>
          <span>
            {{ selectedTimeline.departmentName || "未分配部门" }} ·
            负责人：{{ selectedTimeline.projectOwnerName || "待补充" }}
          </span>
        </header>
        <strong>{{ selectedTimeline.riskTitle }}</strong>
        <p>{{ selectedTimeline.riskDescription }}</p>
        <dl>
          <div>
            <dt>证据 / 信息来源</dt>
            <dd>
              {{
                selectedTimeline.riskEvidence ||
                "暂无独立证据说明"
              }}
            </dd>
          </div>
          <div>
            <dt>建议措施</dt>
            <dd>
              {{
                selectedTimeline.riskSuggestion ||
                "待风险管理员补充建议措施"
              }}
            </dd>
          </div>
          <div>
            <dt>风险发现时间</dt>
            <dd>
              {{ formatDateTime(selectedTimeline.detectedAt) }}
            </dd>
          </div>
          <div v-if="selectedTimeline.resolvedAt">
            <dt>风险解除时间</dt>
            <dd>
              {{ formatDateTime(selectedTimeline.resolvedAt) }}
            </dd>
          </div>
          <div v-if="selectedTimeline.resolutionReason">
            <dt>解除说明</dt>
            <dd>{{ selectedTimeline.resolutionReason }}</dd>
          </div>
        </dl>
      </section>

      <section
        v-if="selectedTimeline.actionItem"
        class="timeline-related-action"
      >
        <header>
          <h3>关联待办</h3>
          <span
            class="todo-status"
            :class="`status-${selectedTimeline.actionItem.status.toLowerCase()}`"
          >
            {{
              actionStatusLabel(
                selectedTimeline.actionItem.status,
              )
            }}
          </span>
        </header>
        <strong>{{ selectedTimeline.actionItem.title }}</strong>
        <dl>
          <div>
            <dt>负责人</dt>
            <dd>{{ selectedTimeline.actionItem.assigneeName }}</dd>
          </div>
          <div>
            <dt>截止日期</dt>
            <dd>
              {{ selectedTimeline.actionItem.dueDate || "待安排" }}
            </dd>
          </div>
          <div>
            <dt>处理说明</dt>
            <dd>
              {{
                selectedTimeline.actionItem.completionNote ||
                "暂无处理说明"
              }}
            </dd>
          </div>
        </dl>
      </section>
    </ModalDialog>

    <ModalDialog
      v-if="metricDetail"
      eyebrow="METRIC DETAIL"
      :title="metricDetail.title"
      @close="selectedMetricKey = null"
    >
      <dl class="metric-detail-summary">
        <div
          v-for="item in metricDetail.summary"
          :key="item[0]"
          :class="{
            'is-remaining': selectedMetricKey === 'remaining' && item[0] === '待回款',
          }"
        >
          <dt>{{ item[0] }}</dt>
          <dd>{{ item[1] }}</dd>
        </div>
      </dl>
      <dl class="metric-detail-rows">
        <div v-for="item in metricDetail.rows" :key="item[0]">
          <dt>{{ item[0] }}</dt>
          <dd>{{ item[1] }}</dd>
        </div>
      </dl>
    </ModalDialog>

    <ModalDialog
      v-if="selectedWeeklyProject"
      eyebrow="WEEKLY REPORT DETAIL"
      :title="`${selectedWeeklyProject.project.name} · 周报风险明细`"
      @close="closeWeeklyProject"
    >
      <section class="weekly-report-summary">
        <p>本周风险等级分布</p>
        <strong>{{
          levelCountsLabel(
            selectedWeeklyProject.riskLevelCounts as Record<string, unknown>,
          )
        }}</strong>
        <small v-if="weeklyReport?.stale">{{ staleLabel(weeklyReport.stale) }}</small>
      </section>

      <div v-if="weeklyDetailLoading" class="dashboard-detail-loading" role="status">
        正在加载周报风险明细…
      </div>

      <section v-else-if="weeklyDetail" class="weekly-risk-list">
        <h3>提取的风险（{{ weeklyDetail.items.length }}项）</h3>
        <article v-for="item in weeklyDetail.items" :key="item.riskId">
          <span
            class="risk-level-chip"
            :class="`level-${item.riskLevel.toLowerCase()}`"
          >
            {{ levelLabel(item.riskLevel) }}
          </span>
          <p>{{ item.summary }}</p>
          <small>
            {{ riskStatusLabel(item.riskStatus) }} · 待办{{ todoStatusLabel(item.todoStatus) }}
            · {{ formatDateTime(item.occurredAt) }}
          </small>
        </article>
      </section>

      <section v-else class="weekly-risk-list">
        <p>周报风险明细加载失败，请关闭后重试。</p>
      </section>
    </ModalDialog>

    <ModalDialog
      v-if="selectedRisk"
      eyebrow="RISK DETAIL"
      :title="selectedRisk.projectName"
      @close="closeRiskDetail"
    >
      <div class="risk-detail-meta">
        <span class="risk-level-chip" :class="`level-${selectedRisk.level.toLowerCase()}`">
          {{ levelLabel(selectedRisk.level) }}
        </span>
        <span>{{ selectedRisk.category.name }}</span>
        <span>{{ selectedRisk.sourceLabel }}</span>
        <span>{{ selectedRisk.weekCode || "未标记周次" }}</span>
        <span
          class="risk-status-chip"
          :class="{
            'is-resolved': selectedRisk.status === 'RESOLVED',
          }"
        >
          {{ selectedRisk.status === "RESOLVED" ? "已解除" : "跟踪中" }}
        </span>
      </div>
      <section class="risk-detail-section">
        <h3>风险描述</h3>
        <p>{{ selectedRisk.description }}</p>
      </section>
      <section class="risk-detail-section risk-evidence">
        <h3>证据 / 信息来源</h3>
        <p>{{ selectedRisk.evidence || "暂无独立证据说明" }}</p>
      </section>
      <section class="risk-detail-section risk-suggestion">
        <h3>建议措施</h3>
        <p>{{ selectedRisk.suggestion || "待风险管理员补充建议措施" }}</p>
      </section>
      <dl class="risk-detail-grid">
        <div>
          <dt>项目负责人</dt>
          <dd>{{ selectedRisk.projectOwnerName || "待补充" }}</dd>
        </div>
        <div>
          <dt>上报人</dt>
          <dd>{{ selectedRisk.reporterName || "系统导入" }}</dd>
        </div>
        <div>
          <dt>已回款</dt>
          <dd>{{ formatWan(selectedRisk.actualCollectedAmountYuan) }}</dd>
        </div>
        <div class="risk-remaining">
          <dt>待回款</dt>
          <dd>{{ formatWan(selectedRisk.remainingAmountYuan) }}</dd>
        </div>
        <div>
          <dt>发现时间</dt>
          <dd>{{ formatDateTime(selectedRisk.detectedAt) }}</dd>
        </div>
        <div>
          <dt>更新时间</dt>
          <dd>{{ formatDateTime(selectedRisk.updatedAt) }}</dd>
        </div>
        <div v-if="selectedRisk.resolvedAt">
          <dt>解除时间</dt>
          <dd>{{ formatDateTime(selectedRisk.resolvedAt) }}</dd>
        </div>
        <div v-if="selectedRisk.resolvedAt">
          <dt>解除人</dt>
          <dd>{{ selectedRisk.resolvedByName || "系统处理" }}</dd>
        </div>
      </dl>
      <section
        v-if="selectedRisk.status === 'RESOLVED'"
        class="risk-resolution-summary"
      >
        <h3>解除原因</h3>
        <p>{{ selectedRisk.resolutionReason || "未记录解除原因" }}</p>
      </section>
      <section v-if="selectedRisk.sameProjectRisks.length" class="same-project-risks">
        <h3>同项目其他风险</h3>
        <button
          v-for="risk in selectedRisk.sameProjectRisks"
          :key="risk.id"
          type="button"
          @click="openRisk({ id: risk.id } as DashboardRiskListItem)"
        >
          <span class="risk-level-chip" :class="`level-${risk.level.toLowerCase()}`">
            {{ levelLabel(risk.level) }}
          </span>
          <strong>{{ risk.categoryName }}：{{ risk.title }}</strong>
        </button>
      </section>
      <section
        v-if="canManageRisks"
        class="risk-lifecycle-actions"
      >
        <div v-if="!lifecycleMode" class="risk-lifecycle-entry">
          <div>
            <h3>
              {{
                selectedRisk.status === "ACTIVE"
                  ? "确认风险已得到处理？"
                  : "需要恢复风险跟踪？"
              }}
            </h3>
            <p>
              {{
                selectedRisk.status === "ACTIVE"
                  ? "解除后将同步完成关联待办，并写入时间线和审计日志。"
                  : "重新打开后将恢复关联待办，并写入时间线和审计日志。"
              }}
            </p>
          </div>
          <button
            type="button"
            :class="{
              'is-reopen': selectedRisk.status === 'RESOLVED',
            }"
            @click="
              beginRiskLifecycle(
                selectedRisk.status === 'ACTIVE' ? 'resolve' : 'reopen',
              )
            "
          >
            {{
              selectedRisk.status === "ACTIVE"
                ? "解除风险"
                : "重新打开风险"
            }}
          </button>
        </div>
        <form
          v-else
          class="risk-lifecycle-form"
          @submit.prevent="saveRiskLifecycle"
        >
          <label>
            <strong>
              {{
                lifecycleMode === "resolve"
                  ? "解除原因"
                  : "重新打开原因"
              }}
              <span aria-hidden="true">*</span>
            </strong>
            <textarea
              v-model="lifecycleReason"
              rows="4"
              maxlength="2000"
              :placeholder="
                lifecycleMode === 'resolve'
                  ? '请说明风险已消除的依据、处理结果或决策结论（至少5个字符）'
                  : '请说明重新进入跟踪的原因或新出现的情况（至少5个字符）'
              "
            ></textarea>
          </label>
          <small>{{ lifecycleReason.trim().length }} / 2000</small>
          <div>
            <button
              type="button"
              :disabled="lifecycleSaving"
              @click="cancelRiskLifecycle"
            >
              取消
            </button>
            <button
              type="submit"
              :disabled="
                lifecycleSaving || lifecycleReason.trim().length < 5
              "
            >
              {{
                lifecycleSaving
                  ? "提交中…"
                  : lifecycleMode === "resolve"
                    ? "确认解除"
                    : "确认重新打开"
              }}
            </button>
          </div>
        </form>
      </section>
    </ModalDialog>

    <ModalDialog
      v-if="selectedDepartment"
      eyebrow="DEPARTMENT COLLECTION DETAIL"
      :title="selectedDepartment.departmentName"
      @close="selectedDepartment = null"
    >
      <dl class="department-detail-summary">
        <div>
          <dt>项目总数</dt>
          <dd>{{ selectedDepartment.summary.projectTotal }} 个</dd>
        </div>
        <div>
          <dt>已统计应收</dt>
          <dd>
            {{ formatWan(selectedDepartment.summary.receivableAmountYuan) }}
          </dd>
        </div>
        <div>
          <dt>累计已收</dt>
          <dd>
            {{ formatWan(selectedDepartment.summary.collectedAmountYuan) }}
          </dd>
        </div>
        <div class="collection-remaining-total">
          <dt>剩余未回</dt>
          <dd>
            {{ formatWan(selectedDepartment.summary.remainingAmountYuan) }}
          </dd>
        </div>
      </dl>
      <section class="department-project-list">
        <header>
          <h3>项目回款明细</h3>
          <span>
            完整
            {{ selectedDepartment.summary.amountCompleteProjectTotal }}
            项 · 缺失
            {{ selectedDepartment.summary.amountMissingProjectTotal }}
            项
          </span>
        </header>
        <article
          v-for="project in selectedDepartment.projects"
          :key="project.projectId"
        >
          <div>
            <strong>{{ project.projectName }}</strong>
            <small>
              {{ project.externalCode || "无项目编码" }} · 负责人：
              {{ project.ownerName || "待补充" }}
            </small>
          </div>
          <span
            class="collection-source-chip"
            :class="`source-${project.amountSource.toLowerCase()}`"
          >
            {{ project.amountSourceLabel }}
          </span>
          <dl>
            <div>
              <dt>应收</dt>
              <dd>{{ formatWan(project.receivableAmountYuan) }}</dd>
            </div>
            <div>
              <dt>已收</dt>
              <dd>{{ formatWan(project.collectedAmountYuan) }}</dd>
            </div>
            <div class="collection-remaining">
              <dt>未回</dt>
              <dd>{{ formatWan(project.remainingAmountYuan) }}</dd>
            </div>
            <div>
              <dt>完成率</dt>
              <dd>{{ collectionRateLabel(project.completionRate) }}</dd>
            </div>
          </dl>
        </article>
      </section>
    </ModalDialog>

    <ModalDialog
      v-if="selectedTodo"
      eyebrow="ACTION ITEM DETAIL"
      :title="selectedTodo.projectName"
      @close="selectedTodo = null"
    >
      <div class="todo-detail-meta">
        <span class="todo-urgency" :class="`urgency-${selectedTodo.urgency.toLowerCase()}`">
          {{ urgencyLabel(selectedTodo.urgency) }}
        </span>
        <span class="todo-status" :class="`status-${selectedTodo.status.toLowerCase()}`">
          {{ todoStatusLabel(selectedTodo.status) }}
        </span>
        <span>{{ selectedTodo.typeLabel }}</span>
        <span>{{ selectedTodo.departmentName || "未分配部门" }}</span>
      </div>

      <section class="todo-detail-section">
        <h3>待办事项</h3>
        <strong>{{ selectedTodo.title }}</strong>
        <p>{{ selectedTodo.description }}</p>
      </section>

      <section v-if="selectedTodo.risk" class="todo-related-risk">
        <header>
          <h3>关联风险</h3>
          <span>{{ selectedTodo.risk.categoryName }} · {{ levelLabel(selectedTodo.risk.level) }}</span>
        </header>
        <strong>{{ selectedTodo.risk.title }}</strong>
        <p>{{ selectedTodo.risk.description }}</p>
        <dl>
          <div>
            <dt>证据 / 来源</dt>
            <dd>{{ selectedTodo.risk.evidence || "暂无独立证据说明" }}</dd>
          </div>
          <div>
            <dt>建议措施</dt>
            <dd>{{ selectedTodo.risk.suggestion || "待补充" }}</dd>
          </div>
        </dl>
      </section>

      <form class="todo-edit-form" @submit.prevent="saveTodo">
        <label>
          <span>负责人</span>
          <input
            v-model="todoForm.assigneeName"
            type="text"
            maxlength="128"
            :disabled="!canManageTodos"
          />
        </label>
        <label>
          <span>处理状态</span>
          <select v-model="todoForm.status" :disabled="!canManageTodos">
            <option value="PENDING">待处理</option>
            <option value="IN_PROGRESS">处理中</option>
            <option value="COMPLETED">已完成</option>
          </select>
        </label>
        <label>
          <span>截止日期</span>
          <input v-model="todoForm.dueDate" type="date" :disabled="!canManageTodos" />
        </label>
        <label class="todo-note-field">
          <span>处理说明</span>
          <textarea
            v-model="todoForm.completionNote"
            rows="4"
            maxlength="2000"
            placeholder="记录处理进展、决策结果或完成说明"
            :disabled="!canManageTodos"
          ></textarea>
        </label>
        <p v-if="!canManageTodos" class="todo-readonly-note">
          当前账号为只读查看，需要“处理与解除项目风险”权限才能更新待办。
        </p>
        <div v-else class="todo-form-actions">
          <button type="button" @click="selectedTodo = null">取消</button>
          <button type="submit" :disabled="todoSaving || !todoForm.assigneeName.trim()">
            {{ todoSaving ? "保存中" : "保存更新" }}
          </button>
        </div>
      </form>
    </ModalDialog>

    <ModalDialog
      v-if="selectedCollection"
      eyebrow="COLLECTION DETAIL"
      :title="selectedCollection.projectName"
      @close="selectedCollection = null"
    >
      <div class="collection-detail-meta">
        <span
          class="risk-level-chip"
          :class="`level-${selectedCollection.riskLevel.toLowerCase()}`"
        >
          {{ levelLabel(selectedCollection.riskLevel) }}
        </span>
        <span>{{ selectedCollection.departmentName || "未分配部门" }}</span>
        <span>{{ selectedCollection.ownerName || "负责人待补充" }}</span>
        <span>{{ selectedCollection.amountSourceLabel }}</span>
      </div>

      <dl class="collection-detail-summary">
        <div>
          <dt>计划金额</dt>
          <dd>{{ formatWan(selectedCollection.receivableAmountYuan) }}</dd>
        </div>
        <div>
          <dt>已回款</dt>
          <dd>{{ formatWan(selectedCollection.collectedAmountYuan) }}</dd>
        </div>
        <div>
          <dt>完成率</dt>
          <dd>{{ collectionRateLabel(selectedCollection.completionRate) }}</dd>
        </div>
      </dl>

      <dl class="collection-detail-rows">
        <div class="collection-detail-remaining">
          <dt>待回款</dt>
          <dd>{{ formatWan(selectedCollection.remainingAmountYuan) }}</dd>
        </div>
        <div>
          <dt>下一笔回款</dt>
          <dd>{{ nextCollectionLabel(selectedCollection) }}</dd>
        </div>
        <div>
          <dt>回款进展</dt>
          <dd>
            {{ selectedCollection.collectionProgress || "待补充回款进展" }}
          </dd>
        </div>
        <div>
          <dt>统计口径</dt>
          <dd>{{ selectedCollection.statisticalScope }}</dd>
        </div>
        <div>
          <dt>更新时间</dt>
          <dd>{{ formatDateTime(selectedCollection.updatedAt) }}</dd>
        </div>
      </dl>

      <section class="collection-monthly-section">
        <header>
          <h3>月度回款计划</h3>
          <span>Excel 原始月份属性与金额</span>
        </header>
        <div class="collection-month-grid">
          <div
            v-for="month in selectedCollection.monthlyCollections"
            :key="month.month"
            :class="{ 'has-amount': month.amountYuan !== null }"
          >
            <span>{{ month.month }} 月</span>
            <strong>{{ formatWan(month.amountYuan) }}</strong>
            <small>{{ month.attribute || "未标记" }}</small>
          </div>
        </div>
      </section>

      <section class="collection-risk-section">
        <header>
          <h3>当前有效风险</h3>
          <span>{{ selectedCollection.activeRiskTotal }} 项</span>
        </header>
        <button
          v-for="risk in selectedCollection.activeRisks"
          :key="risk.id"
          type="button"
          @click="
            selectedCollection = null;
            openRisk({ id: risk.id } as DashboardRiskListItem)
          "
        >
          <span
            class="risk-level-chip"
            :class="`level-${risk.level.toLowerCase()}`"
          >
            {{ levelLabel(risk.level) }}
          </span>
          <span>
            <strong>{{ risk.categoryName }} · {{ risk.title }}</strong>
            <small>{{ risk.description }}</small>
          </span>
          <i aria-hidden="true">›</i>
        </button>
      </section>
    </ModalDialog>
  </div>
</template>
