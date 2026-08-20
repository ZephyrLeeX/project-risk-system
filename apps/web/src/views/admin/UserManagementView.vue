<script setup lang="ts">
import type {
  AdminUserListItem,
  AdminUserSummary,
  DataScopeType,
  DepartmentOption,
  ProjectOption,
  RoleListItem,
  UserAuditRecord,
  UserMutationRequest,
} from "@risk-platform/contracts";
import { computed, onMounted, onUnmounted, reactive, ref, watch } from "vue";

import { adminApi } from "@/api/admin";
import { ApiError } from "@/api/http";
import AdminShell from "@/components/AdminShell.vue";
import ModalDialog from "@/components/ModalDialog.vue";
import { copyTextToClipboard } from "@/utils/clipboard";

const users = ref<AdminUserListItem[]>([]);
const roles = ref<RoleListItem[]>([]);
const departments = ref<DepartmentOption[]>([]);
const projects = ref<ProjectOption[]>([]);
const summary = ref<AdminUserSummary>({
  total: 0,
  active: 0,
  locked: 0,
  disabled: 0,
});
const loading = ref(true);
const saving = ref(false);
const errorMessage = ref("");
const total = ref(0);
const page = ref(1);
const pageSize = 20;
const selectedUserIds = ref<string[]>([]);
const filters = reactive({
  keyword: "",
  roleCode: "",
  status: "",
  departmentId: "",
});

const drawerOpen = ref(false);
const editingUserId = ref<string | null>(null);
const form = reactive<UserMutationRequest>({
  displayName: "",
  username: "",
  email: null,
  mobile: null,
  departmentId: "",
  roleId: "",
  dataScope: "NONE",
  projectIds: [],
  ownedProjectIds: [],
  enabled: true,
});
const ownedProjectKeyword = ref("");
const oneTimePassword = ref("");
const passwordUserName = ref("");
/** Transient copy-success toast; never carries the password itself. */
const copyNotice = ref("");
let copyNoticeTimer: ReturnType<typeof setTimeout> | null = null;
const records = ref<UserAuditRecord[] | null>(null);
const recordsUserName = ref("");
const pendingConfirm = ref<{
  title: string;
  copy: string;
  confirmLabel: string;
  run: () => Promise<void>;
} | null>(null);

const scopeOptions: Array<{ value: DataScopeType; label: string }> = [
  { value: "ALL", label: "全部项目" },
  { value: "OWNED_OR_ASSIGNED", label: "本人负责及授权项目" },
  { value: "OWNED", label: "本人负责项目" },
  { value: "ASSIGNED", label: "被授权项目" },
  { value: "NONE", label: "无项目数据" },
];
const allowedScopeValuesByRole: Partial<Record<string, DataScopeType[]>> = {
  SYSTEM_ADMIN: ["ALL"],
  RISK_ADMIN: ["ALL", "ASSIGNED"],
  PROJECT_MANAGER: ["OWNED_OR_ASSIGNED", "OWNED", "ASSIGNED", "NONE"],
  VIEWER_AUDITOR: ["ASSIGNED", "NONE"],
};
const selectedRole = computed(() =>
  roles.value.find((role) => role.id === form.roleId),
);
const availableScopeOptions = computed(() => {
  const roleCode = selectedRole.value?.code;
  if (!roleCode) return scopeOptions;
  const allowed = allowedScopeValuesByRole[roleCode];
  if (!allowed) return scopeOptions;
  return scopeOptions.filter((option) => allowed.includes(option.value));
});
const selectedProjectsVisible = computed(() =>
  ["ASSIGNED", "OWNED_OR_ASSIGNED"].includes(form.dataScope),
);
/** The "本人负责项目" selector only appears for PROJECT_MANAGER role accounts. */
const ownedProjectsVisible = computed(() => {
  if (!selectedRole.value) return false;
  return (
    selectedRole.value.code === "PROJECT_MANAGER" &&
    ["OWNED", "OWNED_OR_ASSIGNED"].includes(form.dataScope)
  );
});
/** Normalize display names the same way on both sides of the Excel-name match. */
function normalizeName(value: string): string {
  return value.replace(/\s+/g, "").trim().toLowerCase();
}
/** Projects whose Excel deliveryOwnerName matches the current form name. */
const recommendedProjects = computed(() => {
  const name = normalizeName(form.displayName);
  if (!name) return [];
  return projects.value.filter(
    (project) =>
      project.deliveryOwnerName &&
      normalizeName(project.deliveryOwnerName) === name,
  );
});
function ownedProjectBlocked(project: ProjectOption): boolean {
  return Boolean(project.managerId) && project.managerId !== editingUserId.value;
}
function ownedProjectStatus(project: ProjectOption): string {
  if (!project.managerId) return "尚未绑定系统账号";
  if (project.managerId === editingUserId.value) return "当前已负责";
  return `已由 ${project.managerName ?? "其他用户"} 负责`;
}
const selectableRecommended = computed(() =>
  recommendedProjects.value.filter((project) => !ownedProjectBlocked(project)),
);
const allRecommendedSelected = computed(
  () =>
    selectableRecommended.value.length > 0 &&
    selectableRecommended.value.every((project) =>
      form.ownedProjectIds.includes(project.id),
    ),
);
const ownedSearchProjects = computed(() => {
  const keyword = ownedProjectKeyword.value.trim().toLowerCase();
  const recommendedIds = new Set(
    recommendedProjects.value.map((project) => project.id),
  );
  const all = projects.value.filter(
    (project) => !recommendedIds.has(project.id),
  );
  if (!keyword) return all;
  return all.filter(
    (project) =>
      project.name.toLowerCase().includes(keyword) ||
      (project.externalCode ?? "").toLowerCase().includes(keyword),
  );
});
function toggleAllRecommended(): void {
  if (allRecommendedSelected.value) {
    cancelAllRecommended();
    return;
  }
  const selected = new Set(form.ownedProjectIds);
  selectableRecommended.value.forEach((project) => selected.add(project.id));
  form.ownedProjectIds = [...selected];
}
function cancelAllRecommended(): void {
  const recommendedIds = new Set(
    recommendedProjects.value.map((project) => project.id),
  );
  form.ownedProjectIds = form.ownedProjectIds.filter(
    (id) => !recommendedIds.has(id),
  );
}
const totalPages = computed(() =>
  Math.max(1, Math.ceil(total.value / pageSize)),
);
const allCurrentSelected = computed(() =>
  users.value.length > 0 &&
  users.value.every((user) => selectedUserIds.value.includes(user.id)),
);

watch(
  () => form.roleId,
  () => {
    if (!selectedRole.value) return;
    const currentScopeAllowed = availableScopeOptions.value.some(
      (option) => option.value === form.dataScope,
    );
    if (!editingUserId.value || !currentScopeAllowed) {
      form.dataScope = selectedRole.value.defaultDataScope;
    }
    if (selectedRole.value.code !== "PROJECT_MANAGER") {
      form.ownedProjectIds = [];
    }
  },
);
watch(
  () => form.dataScope,
  (scope) => {
    if (!["ASSIGNED", "OWNED_OR_ASSIGNED"].includes(scope)) {
      form.projectIds = [];
    }
  },
);

onMounted(async () => {
  await loadInitial();
});

async function loadInitial(): Promise<void> {
  loading.value = true;
  errorMessage.value = "";
  try {
    const [roleItems, departmentItems, projectItems, summaryData] =
      await Promise.all([
        adminApi.roles(),
        adminApi.departments(),
        adminApi.projects(),
        adminApi.userSummary(),
      ]);
    roles.value = roleItems;
    departments.value = departmentItems;
    projects.value = projectItems;
    summary.value = summaryData;
    await loadUsers();
  } catch (error) {
    errorMessage.value = getErrorMessage(error);
  } finally {
    loading.value = false;
  }
}

async function loadUsers(): Promise<void> {
  loading.value = true;
  try {
    const result = await adminApi.users({
      page: page.value,
      pageSize,
      keyword: filters.keyword.trim(),
      roleCode: filters.roleCode,
      status: filters.status,
      departmentId: filters.departmentId,
    });
    users.value = result.items;
    total.value = result.total;
    selectedUserIds.value = selectedUserIds.value.filter((id) =>
      result.items.some((user) => user.id === id),
    );
  } catch (error) {
    errorMessage.value = getErrorMessage(error);
  } finally {
    loading.value = false;
  }
}

function toggleAllCurrent(): void {
  selectedUserIds.value = allCurrentSelected.value
    ? []
    : users.value.map((user) => user.id);
}

function bulkSetStatus(status: "ACTIVE" | "DISABLED"): void {
  const count = selectedUserIds.value.length;
  if (!count) return;
  confirmAction(
    status === "ACTIVE" ? "批量启用用户" : "批量停用用户",
    `确认${status === "ACTIVE" ? "启用" : "停用"}已选择的 ${count} 个账号？`,
    status === "ACTIVE" ? "确认启用" : "确认停用",
    async () => {
      await Promise.all(
        selectedUserIds.value.map((id) => adminApi.setUserStatus(id, status)),
      );
      selectedUserIds.value = [];
    },
  );
}

async function refreshAfterMutation(): Promise<void> {
  [summary.value] = await Promise.all([
    adminApi.userSummary(),
    loadUsers(),
  ]);
}

function resetFilters(): void {
  filters.keyword = "";
  filters.roleCode = "";
  filters.status = "";
  filters.departmentId = "";
  page.value = 1;
  void loadUsers();
}

function openCreate(): void {
  editingUserId.value = null;
  Object.assign(form, {
    displayName: "",
    username: "",
    email: null,
    mobile: null,
    departmentId: departments.value[0]?.id ?? "",
    roleId: roles.value.find((role) => role.enabled)?.id ?? "",
    dataScope:
      roles.value.find((role) => role.enabled)?.defaultDataScope ?? "NONE",
    projectIds: [],
    ownedProjectIds: [],
    enabled: true,
  });
  ownedProjectKeyword.value = "";
  errorMessage.value = "";
  drawerOpen.value = true;
}

function openEdit(user: AdminUserListItem): void {
  editingUserId.value = user.id;
  Object.assign(form, {
    displayName: user.displayName,
    username: user.username,
    email: user.email,
    mobile: user.mobile,
    departmentId: user.department?.id ?? "",
    roleId: user.role?.id ?? "",
    dataScope: user.dataScope,
    projectIds: [...user.assignedProjectIds],
    ownedProjectIds: [...user.ownedProjectIds],
    enabled: user.status !== "DISABLED",
  });
  ownedProjectKeyword.value = "";
  errorMessage.value = "";
  drawerOpen.value = true;
}

async function saveUser(): Promise<void> {
  saving.value = true;
  errorMessage.value = "";
  try {
    const payload = {
      ...form,
      displayName: form.displayName.trim(),
      username: form.username.trim(),
      email: form.email?.trim() || null,
      mobile: form.mobile?.trim() || null,
      projectIds: [...form.projectIds],
      ownedProjectIds: [...form.ownedProjectIds],
    };
    const result = editingUserId.value
      ? await adminApi.updateUser(editingUserId.value, payload)
      : await adminApi.createUser(payload);
    drawerOpen.value = false;
    if (result.initialPassword) {
      oneTimePassword.value = result.initialPassword;
      passwordUserName.value = result.user.displayName;
    }
    await refreshAfterMutation();
  } catch (error) {
    errorMessage.value = getErrorMessage(error);
  } finally {
    saving.value = false;
  }
}

function confirmAction(
  title: string,
  copy: string,
  confirmLabel: string,
  run: () => Promise<void>,
): void {
  pendingConfirm.value = { title, copy, confirmLabel, run };
}

async function executeConfirmed(): Promise<void> {
  if (!pendingConfirm.value) return;
  saving.value = true;
  errorMessage.value = "";
  try {
    await pendingConfirm.value.run();
    pendingConfirm.value = null;
    await refreshAfterMutation();
  } catch (error) {
    errorMessage.value = getErrorMessage(error);
    pendingConfirm.value = null;
  } finally {
    saving.value = false;
  }
}

function toggleStatus(user: AdminUserListItem): void {
  const enabling = user.status === "DISABLED";
  confirmAction(
    enabling ? "启用用户" : "停用用户",
    enabling
      ? `确认启用“${user.displayName}”的账号？`
      : `停用后“${user.displayName}”的全部登录会话将立即失效。`,
    enabling ? "确认启用" : "确认停用",
    async () => {
      await adminApi.setUserStatus(
        user.id,
        enabling ? "ACTIVE" : "DISABLED",
      );
    },
  );
}

function unlock(user: AdminUserListItem): void {
  confirmAction(
    "解除账号锁定",
    `确认清除“${user.displayName}”的连续失败次数并恢复登录？`,
    "确认解锁",
    async () => {
      await adminApi.unlockUser(user.id);
    },
  );
}

function resetPassword(user: AdminUserListItem): void {
  confirmAction(
    "重置用户密码",
    `重置后“${user.displayName}”的全部登录会话将失效，新密码仅展示一次。`,
    "确认重置",
    async () => {
      const result = await adminApi.resetPassword(user.id);
      oneTimePassword.value = result.initialPassword;
      passwordUserName.value = user.displayName;
    },
  );
}

async function viewRecords(user: AdminUserListItem): Promise<void> {
  errorMessage.value = "";
  try {
    records.value = await adminApi.userRecords(user.id);
    recordsUserName.value = user.displayName;
  } catch (error) {
    errorMessage.value = getErrorMessage(error);
  }
}

async function copyPassword(): Promise<void> {
  const copied = await copyTextToClipboard(oneTimePassword.value);
  if (copied) {
    showCopyNotice("一次性密码已复制");
  } else {
    errorMessage.value = "浏览器未允许自动复制，请手动选择密码复制";
  }
}

/** Show the transient copy-success toast for ~2.5 s (no password content). */
function showCopyNotice(message: string): void {
  if (copyNoticeTimer !== null) clearTimeout(copyNoticeTimer);
  copyNotice.value = message;
  copyNoticeTimer = setTimeout(() => {
    copyNotice.value = "";
    copyNoticeTimer = null;
  }, 2500);
}

onUnmounted(() => {
  if (copyNoticeTimer !== null) clearTimeout(copyNoticeTimer);
});

function scopeName(scope: DataScopeType): string {
  return (
    scopeOptions.find((item) => item.value === scope)?.label ?? scope
  );
}

function statusName(status: AdminUserListItem["status"]): string {
  return {
    ACTIVE: "启用",
    DISABLED: "停用",
    LOCKED: "已锁定",
  }[status];
}

function formatTime(value: string | null): string {
  if (!value) return "从未登录";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function getErrorMessage(error: unknown): string {
  return error instanceof ApiError
    ? error.message
    : "操作失败，请稍后重试";
}
</script>

<template>
  <AdminShell>
    <section class="admin-page-heading">
      <div>
        <p class="admin-eyebrow">ACCOUNT & ACCESS MANAGEMENT</p>
        <h1>用户管理</h1>
        <p>维护账号、角色、部门、项目数据范围和登录安全。</p>
      </div>
      <button class="admin-primary-button" type="button" @click="openCreate">
        ＋ 新增用户
      </button>
    </section>

    <p v-if="errorMessage" class="admin-alert" role="alert">
      {{ errorMessage }}
    </p>

    <section class="admin-summary-grid" aria-label="用户统计">
      <button type="button" @click="filters.status = ''; loadUsers()">
        <span class="summary-icon summary-blue">人</span>
        <span><small>系统用户</small><strong>{{ summary.total }}</strong></span>
      </button>
      <button type="button" @click="filters.status = 'ACTIVE'; loadUsers()">
        <span class="summary-icon summary-green">✓</span>
        <span><small>正常启用</small><strong>{{ summary.active }}</strong></span>
      </button>
      <button type="button" @click="filters.status = 'LOCKED'; loadUsers()">
        <span class="summary-icon summary-orange">!</span>
        <span><small>登录锁定</small><strong>{{ summary.locked }}</strong></span>
      </button>
      <button type="button" @click="filters.status = 'DISABLED'; loadUsers()">
        <span class="summary-icon summary-gray">—</span>
        <span><small>已停用</small><strong>{{ summary.disabled }}</strong></span>
      </button>
    </section>

    <section class="admin-content-card">
      <header class="content-card-header">
        <div>
          <p class="admin-eyebrow">USER DIRECTORY</p>
          <h2>用户列表 <small>共{{ total }}名用户</small></h2>
        </div>
      </header>

      <form class="admin-filter-bar" @submit.prevent="page = 1; loadUsers()">
        <input
          v-model="filters.keyword"
          type="search"
          placeholder="搜索姓名、账号或部门"
        />
        <select v-model="filters.roleCode" aria-label="角色筛选">
          <option value="">全部角色</option>
          <option v-for="role in roles" :key="role.id" :value="role.code">
            {{ role.name }}
          </option>
        </select>
        <select v-model="filters.status" aria-label="状态筛选">
          <option value="">全部状态</option>
          <option value="ACTIVE">启用</option>
          <option value="LOCKED">锁定</option>
          <option value="DISABLED">停用</option>
        </select>
        <select v-model="filters.departmentId" aria-label="部门筛选">
          <option value="">全部部门</option>
          <option
            v-for="department in departments"
            :key="department.id"
            :value="department.id"
          >
            {{ department.name }}
          </option>
        </select>
        <button class="filter-search-button" type="submit">查询</button>
        <button class="filter-reset-button" type="button" @click="resetFilters">
          重置
        </button>
      </form>

      <div v-if="selectedUserIds.length" class="bulk-action-bar">
        <strong>已选择 {{ selectedUserIds.length }} 名用户</strong>
        <button type="button" @click="bulkSetStatus('ACTIVE')">批量启用</button>
        <button type="button" @click="bulkSetStatus('DISABLED')">批量停用</button>
        <button type="button" @click="selectedUserIds = []">取消选择</button>
      </div>

      <div v-if="loading" class="admin-state">正在加载用户数据…</div>
      <div v-else-if="users.length === 0" class="admin-state">
        暂无符合条件的用户
      </div>
      <div v-else class="admin-table-scroll">
        <table class="admin-table users-live-table">
          <thead>
            <tr>
              <th>
                <input
                  type="checkbox"
                  :checked="allCurrentSelected"
                  aria-label="选择当前页全部用户"
                  @change="toggleAllCurrent"
                />
              </th>
              <th>用户</th>
              <th>所属部门</th>
              <th>角色</th>
              <th>项目数据范围</th>
              <th>账号状态</th>
              <th>最近登录</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="user in users" :key="user.id">
              <td>
                <input
                  v-model="selectedUserIds"
                  type="checkbox"
                  :value="user.id"
                  :aria-label="`选择用户${user.displayName}`"
                />
              </td>
              <td>
                <button class="table-user" type="button" @click="openEdit(user)">
                  <span>{{ user.displayName.slice(0, 1) }}</span>
                  <span>
                    <strong>{{ user.displayName }}</strong>
                    <small>{{ user.username }}</small>
                  </span>
                </button>
              </td>
              <td>{{ user.department?.name ?? "未分配" }}</td>
              <td>
                <span class="role-pill">{{ user.role?.name ?? "无角色" }}</span>
              </td>
              <td>
                <span class="scope-table-cell">
                  <strong>{{ scopeName(user.dataScope) }}</strong>
                  <small v-if="user.ownedProjectCount">
                    {{ user.ownedProjectCount }}个负责项目
                  </small>
                  <small v-if="user.assignedProjectCount">
                    {{ user.assignedProjectCount }}个授权项目
                  </small>
                </span>
              </td>
              <td>
                <span
                  class="status-pill"
                  :class="`status-${user.status.toLowerCase()}`"
                >
                  {{ statusName(user.status) }}
                </span>
              </td>
              <td>{{ formatTime(user.lastLoginAt) }}</td>
              <td>
                <div class="table-actions">
                  <button type="button" @click="openEdit(user)">编辑</button>
                  <button
                    v-if="user.status === 'LOCKED'"
                    type="button"
                    @click="unlock(user)"
                  >
                    解锁
                  </button>
                  <button type="button" @click="resetPassword(user)">重置密码</button>
                  <button type="button" @click="toggleStatus(user)">
                    {{ user.status === "DISABLED" ? "启用" : "停用" }}
                  </button>
                  <button type="button" @click="viewRecords(user)">记录</button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <footer class="admin-pagination">
        <span>第 {{ page }} / {{ totalPages }} 页</span>
        <div>
          <button
            type="button"
            :disabled="page <= 1"
            @click="page--; loadUsers()"
          >
            上一页
          </button>
          <button
            type="button"
            :disabled="page >= totalPages"
            @click="page++; loadUsers()"
          >
            下一页
          </button>
        </div>
      </footer>
    </section>

    <div
      v-if="drawerOpen"
      class="drawer-backdrop"
      role="presentation"
      @click.self="drawerOpen = false"
    >
      <section class="admin-drawer" role="dialog" aria-modal="true">
        <header class="admin-drawer-header">
          <div>
            <p class="admin-eyebrow">
              {{ editingUserId ? "EDIT USER" : "CREATE USER" }}
            </p>
            <h2>{{ editingUserId ? "编辑用户" : "新增用户" }}</h2>
          </div>
          <button type="button" aria-label="关闭" @click="drawerOpen = false">
            ×
          </button>
        </header>

        <form class="admin-drawer-form" @submit.prevent="saveUser">
          <section class="form-section">
            <div class="form-section-title">
              <span>01</span>
              <div><h3>账号信息</h3><p>用于平台登录和人员识别</p></div>
            </div>
            <div class="account-form-grid">
              <label>
                <span>姓名 *</span>
                <input
                  v-model="form.displayName"
                  required
                  maxlength="128"
                  placeholder="请输入用户姓名"
                />
              </label>
              <label>
                <span>登录账号 *</span>
                <input
                  v-model="form.username"
                  required
                  maxlength="64"
                  placeholder="建议使用姓名拼音"
                />
              </label>
            </div>
            <label class="full-form-field">
              <span>所属部门 *</span>
              <select v-model="form.departmentId" required>
                <option value="" disabled>请选择所属部门</option>
                <option
                  v-for="department in departments"
                  :key="department.id"
                  :value="department.id"
                >
                  {{ department.name }}
                </option>
              </select>
            </label>
            <label class="full-form-field">
              <span>联系邮箱</span>
              <input
                v-model="form.email"
                type="email"
                maxlength="255"
                placeholder="选填，仅用于账号通知"
              />
            </label>
            <label class="full-form-field">
              <span>手机号（微信登录绑定）</span>
              <input
                v-model="form.mobile"
                type="tel"
                maxlength="11"
                pattern="1[3-9][0-9]{9}"
                placeholder="选填，例如 13800138000"
              />
            </label>
          </section>

          <section class="form-section">
            <div class="form-section-title">
              <span>02</span>
              <div><h3>角色分配</h3><p>角色决定可用模块和操作权限</p></div>
            </div>
            <div class="role-choice-grid">
              <label v-for="role in roles.filter((item) => item.enabled)" :key="role.id">
                <input v-model="form.roleId" type="radio" :value="role.id" />
                <span>
                  <strong>{{ role.name }}</strong>
                  <small>{{ role.description }}</small>
                </span>
              </label>
            </div>
          </section>

          <section class="form-section">
            <div class="form-section-title">
              <span>03</span>
              <div><h3>项目数据范围</h3><p>所有项目查询均在服务端应用该范围</p></div>
            </div>
            <div class="scope-choice-grid">
              <label v-for="scope in availableScopeOptions" :key="scope.value">
                <input
                  v-model="form.dataScope"
                  type="radio"
                  :value="scope.value"
                />
                <span>{{ scope.label }}</span>
              </label>
            </div>
            <div v-if="selectedProjectsVisible" class="project-selector">
              <strong>指定授权项目</strong>
              <p v-if="projects.length === 0">
                尚未导入项目数据。选择“被授权项目”前需先完成项目清单导入。
              </p>
              <label v-for="project in projects" :key="project.id">
                <input
                  v-model="form.projectIds"
                  type="checkbox"
                  :value="project.id"
                />
                <span>
                  <strong>{{ project.name }}</strong>
                  <small>{{ project.departmentName ?? "未分配部门" }}</small>
                </span>
              </label>
            </div>
            <div v-if="ownedProjectsVisible" class="owned-project-selector">
              <div class="owned-selector-head">
                <strong>本人负责项目</strong>
                <div class="owned-selector-actions">
                  <button
                    type="button"
                    :disabled="!selectableRecommended.length"
                    @click="toggleAllRecommended"
                  >
                    {{ allRecommendedSelected ? "取消全选推荐" : "全选推荐项目" }}
                  </button>
                  <button
                    type="button"
                    :disabled="!recommendedProjects.length"
                    @click="cancelAllRecommended"
                  >
                    取消全选
                  </button>
                </div>
              </div>
              <p v-if="recommendedProjects.length" class="owned-recommend-note">
                根据 Excel 项目负责人“{{ form.displayName }}”匹配到
                {{ recommendedProjects.length }} 个项目
              </p>
              <p v-else class="owned-recommend-empty">
                未匹配到与当前姓名同名的 Excel 项目负责人，可在下方搜索后手动勾选。
              </p>
              <label
                v-for="project in recommendedProjects"
                :key="project.id"
                class="owned-project-row"
                :class="{ 'is-conflict': ownedProjectBlocked(project) }"
              >
                <input
                  v-model="form.ownedProjectIds"
                  type="checkbox"
                  :value="project.id"
                  :disabled="ownedProjectBlocked(project)"
                />
                <span>
                  <strong>{{ project.name }}</strong>
                  <small>
                    {{
                      [project.externalCode, project.departmentName]
                        .filter(Boolean)
                        .join(" · ") || "未分配部门"
                    }}
                  </small>
                  <small class="owned-project-status">
                    {{ ownedProjectStatus(project) }}
                  </small>
                </span>
              </label>
              <div class="owned-search-head">
                <strong>全部项目 / 搜索项目</strong>
                <input
                  v-model="ownedProjectKeyword"
                  type="search"
                  placeholder="按项目名或编码搜索"
                />
              </div>
              <p v-if="ownedSearchProjects.length === 0" class="owned-recommend-empty">
                未找到匹配的项目。
              </p>
              <label
                v-for="project in ownedSearchProjects"
                :key="project.id"
                class="owned-project-row"
                :class="{ 'is-conflict': ownedProjectBlocked(project) }"
              >
                <input
                  v-model="form.ownedProjectIds"
                  type="checkbox"
                  :value="project.id"
                  :disabled="ownedProjectBlocked(project)"
                />
                <span>
                  <strong>{{ project.name }}</strong>
                  <small>
                    {{
                      [project.externalCode, project.departmentName]
                        .filter(Boolean)
                        .join(" · ") || "未分配部门"
                    }}
                  </small>
                  <small class="owned-project-status">
                    {{ ownedProjectStatus(project) }}
                  </small>
                </span>
              </label>
            </div>
          </section>

          <section class="form-section">
            <div class="form-section-title">
              <span>04</span>
              <div><h3>账号安全</h3><p>创建时生成一次性初始密码</p></div>
            </div>
            <label class="switch-row">
              <span><strong>创建或保存后启用</strong><small>停用会立即撤销全部会话</small></span>
              <input v-model="form.enabled" type="checkbox" />
            </label>
          </section>

          <p v-if="errorMessage" class="admin-alert" role="alert">
            {{ errorMessage }}
          </p>
          <footer class="drawer-form-actions">
            <button type="button" @click="drawerOpen = false">取消</button>
            <button class="admin-primary-button" type="submit" :disabled="saving">
              {{ saving ? "正在保存…" : editingUserId ? "保存修改" : "创建用户" }}
            </button>
          </footer>
        </form>
      </section>
    </div>

    <ModalDialog
      v-if="pendingConfirm"
      :title="pendingConfirm.title"
      eyebrow="SECURITY CONFIRMATION"
      @close="pendingConfirm = null"
    >
      <p class="modal-copy">{{ pendingConfirm.copy }}</p>
      <template #footer>
        <button type="button" @click="pendingConfirm = null">取消</button>
        <button
          class="admin-danger-button"
          type="button"
          :disabled="saving"
          @click="executeConfirmed"
        >
          {{ pendingConfirm.confirmLabel }}
        </button>
      </template>
    </ModalDialog>

    <ModalDialog
      v-if="oneTimePassword"
      title="一次性初始密码"
      eyebrow="PASSWORD GENERATED"
      @close="oneTimePassword = ''"
    >
      <p class="modal-copy">
        请通过安全渠道转交给 {{ passwordUserName }}。该密码关闭后不再显示，
        用户首次登录必须修改。
      </p>
      <div class="one-time-password">{{ oneTimePassword }}</div>
      <template #footer>
        <button type="button" @click="oneTimePassword = ''">关闭</button>
        <button class="admin-primary-button" type="button" @click="copyPassword">
          复制密码
        </button>
      </template>
    </ModalDialog>

    <ModalDialog
      v-if="records"
      :title="`${recordsUserName} 的操作记录`"
      eyebrow="USER AUDIT TRAIL"
      @close="records = null"
    >
      <div v-if="records.length" class="audit-record-list">
        <article v-for="record in records" :key="record.id">
          <span>{{ record.result === "SUCCESS" ? "成功" : "失败" }}</span>
          <div>
            <strong>{{ record.summary }}</strong>
            <small>
              {{ formatTime(record.createdAt) }} ·
              {{ record.actorName ?? "系统" }}
            </small>
          </div>
        </article>
      </div>
      <p v-else class="modal-copy">暂无操作记录。</p>
    </ModalDialog>

    <p v-if="copyNotice" class="prototype-toast" role="status">
      {{ copyNotice }}
    </p>
  </AdminShell>
</template>