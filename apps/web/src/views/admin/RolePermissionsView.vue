<script setup lang="ts">
import type {
  DataScopeType,
  PermissionItem,
  RoleListItem,
  RoleMutationRequest,
} from "@risk-platform/contracts";
import { computed, onMounted, reactive, ref, watch } from "vue";
import { useRouter } from "vue-router";

import { adminApi } from "@/api/admin";
import { ApiError } from "@/api/http";
import AdminShell from "@/components/AdminShell.vue";
import ModalDialog from "@/components/ModalDialog.vue";

const router = useRouter();

const roles = ref<RoleListItem[]>([]);
const permissions = ref<PermissionItem[]>([]);
const selectedRoleId = ref("");
const loading = ref(true);
const saving = ref(false);
const errorMessage = ref("");
const permissionKeyword = ref("");
const createOpen = ref(false);
const deleteOpen = ref(false);
const createCopyFrom = ref("");
const matrixOpen = ref(false);

const draft = reactive<RoleMutationRequest>({
  name: "",
  code: "",
  description: null,
  enabled: true,
  defaultDataScope: "NONE",
  permissionCodes: [],
});
const createForm = reactive<RoleMutationRequest>({
  name: "",
  code: "",
  description: null,
  enabled: true,
  defaultDataScope: "NONE",
  permissionCodes: ["dashboard.view"],
});

const selectedRole = computed(() =>
  roles.value.find((role) => role.id === selectedRoleId.value),
);
const filteredPermissions = computed(() => {
  const keyword = permissionKeyword.value.trim().toLocaleLowerCase();
  return permissions.value.filter((permission) => {
    if (!keyword) return true;
    return [permission.code, permission.name, permission.description]
      .filter(Boolean)
      .join(" ")
      .toLocaleLowerCase()
      .includes(keyword);
  });
});
const permissionGroups = computed(() => {
  const groupNames: Record<string, string> = {
    DASHBOARD: "业务端菜单与基础访问",
    AGENT: "Agent 智能查询",
    RISK: "风险业务操作",
    MAILBOX: "个人邮箱与周报同步",
    ADMIN: "后台管理与系统治理",
  };
  const groups = new Map<
    string,
    { name: string; items: PermissionItem[] }
  >();
  filteredPermissions.value.forEach((permission) => {
    const group = groups.get(permission.module) ?? {
      name: groupNames[permission.module] ?? permission.module,
      items: [],
    };
    group.items.push(permission);
    groups.set(permission.module, group);
  });
  return [...groups.entries()].map(([key, value]) => ({ key, ...value }));
});
const scopeOptions: Array<{ value: DataScopeType; label: string }> = [
  { value: "ALL", label: "全部项目" },
  { value: "OWNED_OR_ASSIGNED", label: "本人负责及授权项目" },
  { value: "OWNED", label: "本人负责项目" },
  { value: "ASSIGNED", label: "被授权项目" },
  { value: "NONE", label: "无项目数据" },
];

watch(selectedRole, (role) => {
  if (!role) return;
  Object.assign(draft, {
    name: role.name,
    code: role.code,
    description: role.description,
    enabled: role.enabled,
    defaultDataScope: role.defaultDataScope,
    permissionCodes: [...role.permissionCodes],
  });
});

watch(
  () => createCopyFrom.value,
  (roleId) => {
    const source = roles.value.find((role) => role.id === roleId);
    if (source) {
      createForm.defaultDataScope = source.defaultDataScope;
      createForm.permissionCodes = [...source.permissionCodes];
    }
  },
);

onMounted(async () => {
  await loadData();
});

async function loadData(preferredRoleId?: string): Promise<void> {
  loading.value = true;
  errorMessage.value = "";
  try {
    const [roleItems, permissionItems] = await Promise.all([
      adminApi.roles(),
      adminApi.permissions(),
    ]);
    roles.value = roleItems;
    permissions.value = permissionItems;
    selectedRoleId.value =
      preferredRoleId &&
      roleItems.some((role) => role.id === preferredRoleId)
        ? preferredRoleId
        : selectedRoleId.value &&
            roleItems.some((role) => role.id === selectedRoleId.value)
          ? selectedRoleId.value
          : roleItems[0]?.id ?? "";
  } catch (error) {
    errorMessage.value = getErrorMessage(error);
  } finally {
    loading.value = false;
  }
}

function isPermissionLocked(code: string): boolean {
  if (
    draft.code === "SYSTEM_ADMIN" &&
    [
      "dashboard.view",
      "admin.user.manage",
      "admin.role.manage",
      "admin.scope.manage",
      "admin.ai.manage",
      "admin.import.manage",
      "admin.config.manage",
      "admin.audit.view",
    ].includes(code)
  ) {
    return true;
  }
  if (
    draft.code !== "SYSTEM_ADMIN" &&
    [
      "admin.user.manage",
      "admin.role.manage",
      "admin.scope.manage",
      "admin.ai.manage",
      "admin.import.manage",
      "admin.config.manage",
    ].includes(code)
  ) {
    return true;
  }
  if (
    draft.code !== "RISK_ADMIN" &&
    ["mailbox.manage_self", "mailbox.sync_self"].includes(code)
  ) {
    return true;
  }
  return false;
}

function toggleGroup(items: PermissionItem[]): void {
  const editable = items.filter((item) => !isPermissionLocked(item.code));
  const allSelected = editable.every((item) =>
    draft.permissionCodes.includes(item.code),
  );
  editable.forEach((item) => {
    const index = draft.permissionCodes.indexOf(item.code);
    if (allSelected && index >= 0) {
      draft.permissionCodes.splice(index, 1);
    } else if (!allSelected && index < 0) {
      draft.permissionCodes.push(item.code);
    }
  });
}

async function saveRole(): Promise<void> {
  if (!selectedRole.value) return;
  saving.value = true;
  errorMessage.value = "";
  try {
    const updated = await adminApi.updateRole(selectedRole.value, {
      ...draft,
      name: draft.name.trim(),
      description: draft.description?.trim() || null,
      permissionCodes: [...new Set(draft.permissionCodes)],
    });
    await loadData(updated.id);
  } catch (error) {
    errorMessage.value = getErrorMessage(error);
  } finally {
    saving.value = false;
  }
}

function resetDraft(): void {
  const role = selectedRole.value;
  if (!role) return;
  Object.assign(draft, {
    name: role.name,
    code: role.code,
    description: role.description,
    enabled: role.enabled,
    defaultDataScope: role.defaultDataScope,
    permissionCodes: [...role.permissionCodes],
  });
}

function copySelectedRole(): void {
  const role = selectedRole.value;
  if (!role) return;
  Object.assign(createForm, {
    name: `${role.name}副本`,
    code: `${role.code}_COPY`,
    description: role.description,
    enabled: true,
    defaultDataScope: role.defaultDataScope,
    permissionCodes: [...role.permissionCodes],
  });
  createCopyFrom.value = role.id;
  createOpen.value = true;
}

async function toggleSelectedRole(): Promise<void> {
  if (!selectedRole.value) return;
  draft.enabled = !draft.enabled;
  await saveRole();
}

function openCreate(): void {
  Object.assign(createForm, {
    name: "",
    code: "",
    description: null,
    enabled: true,
    defaultDataScope: "NONE",
    permissionCodes: ["dashboard.view"],
  });
  createCopyFrom.value = "";
  createOpen.value = true;
}

async function createRole(): Promise<void> {
  saving.value = true;
  errorMessage.value = "";
  try {
    const created = await adminApi.createRole({
      ...createForm,
      name: createForm.name.trim(),
      code: createForm.code.trim().toLocaleUpperCase(),
      description: createForm.description?.trim() || null,
      permissionCodes: [...new Set(createForm.permissionCodes)],
    });
    createOpen.value = false;
    await loadData(created.id);
  } catch (error) {
    errorMessage.value = getErrorMessage(error);
  } finally {
    saving.value = false;
  }
}

async function deleteRole(): Promise<void> {
  if (!selectedRole.value) return;
  saving.value = true;
  try {
    await adminApi.deleteRole(selectedRole.value.id);
    deleteOpen.value = false;
    selectedRoleId.value = "";
    await loadData();
  } catch (error) {
    errorMessage.value = getErrorMessage(error);
    deleteOpen.value = false;
  } finally {
    saving.value = false;
  }
}

function formatTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
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
        <p class="admin-eyebrow">ROLE BASED ACCESS CONTROL</p>
        <h1>角色权限</h1>
        <p>维护菜单、操作权限和默认项目数据范围，保存后立即生效。</p>
      </div>
      <button class="admin-primary-button" type="button" @click="openCreate">
        ＋ 新增角色
      </button>
    </section>

    <p v-if="errorMessage" class="admin-alert" role="alert">
      {{ errorMessage }}
    </p>

    <section class="prototype-metric-grid role-summary-grid">
      <article class="prototype-metric tone-blue"><span class="metric-glyph">角</span><small>系统角色</small><strong>{{ roles.length }}<em>个</em></strong><p>含预置与自定义角色</p></article>
      <article class="prototype-metric tone-green"><span class="metric-glyph">权</span><small>权限点</small><strong>{{ permissions.length }}<em>项</em></strong><p>覆盖业务端与后台</p></article>
      <article class="prototype-metric tone-violet"><span class="metric-glyph">用</span><small>已分配用户</small><strong>{{ roles.reduce((sum, role) => sum + role.userCount, 0) }}<em>人</em></strong><p>按角色统计</p></article>
      <button class="prototype-metric tone-orange" type="button" @click="matrixOpen = true"><span class="metric-glyph">阵</span><small>权限矩阵</small><strong>查看<em>对照</em></strong><p>比较角色权限差异</p></button>
    </section>

    <div v-if="loading" class="admin-state">正在加载角色权限…</div>
    <section v-else class="role-workbench">
      <aside class="role-directory">
        <header>
          <p class="admin-eyebrow">ROLE DIRECTORY</p>
          <h2>角色目录 <small>{{ roles.length }}</small></h2>
        </header>
        <button
          v-for="role in roles"
          :key="role.id"
          class="role-directory-item"
          :class="{ 'is-active': selectedRoleId === role.id }"
          type="button"
          @click="selectedRoleId = role.id"
        >
          <span class="role-directory-icon">{{ role.name.slice(0, 1) }}</span>
          <span>
            <strong>{{ role.name }}</strong>
            <small>{{ role.userCount }}名用户 · {{ role.code }}</small>
          </span>
          <i :class="{ 'is-disabled': !role.enabled }"></i>
        </button>
      </aside>

      <section v-if="selectedRole" class="role-editor">
        <header class="role-editor-header">
          <div>
            <p class="admin-eyebrow">
              {{ selectedRole.isSystem ? "SYSTEM ROLE" : "CUSTOM ROLE" }}
            </p>
            <h2>{{ selectedRole.name }}</h2>
            <p>{{ selectedRole.description }}</p>
          </div>
          <div class="role-header-meta">
            <span>{{ selectedRole.code }}</span>
            <button type="button" @click="router.push(`/admin/users?role=${selectedRole.code}`)">{{ selectedRole.userCount }}名关联用户</button>
            <button type="button" @click="copySelectedRole">复制角色</button>
            <button type="button" @click="toggleSelectedRole">{{ draft.enabled ? "停用角色" : "启用角色" }}</button>
          </div>
        </header>

        <div class="role-basic-form">
          <label>
            <span>角色名称</span>
            <input v-model="draft.name" maxlength="128" />
          </label>
          <label>
            <span>角色编码</span>
            <input v-model="draft.code" disabled />
          </label>
          <label class="role-description-field">
            <span>职责说明</span>
            <textarea v-model="draft.description" maxlength="500"></textarea>
          </label>
        </div>

        <section class="scope-editor">
          <div>
            <p class="admin-eyebrow">DEFAULT DATA SCOPE</p>
            <h3>默认项目数据范围</h3>
            <p>用户级范围可在用户管理页进一步缩小或补充。</p>
          </div>
          <select v-model="draft.defaultDataScope">
            <option
              v-for="scope in scopeOptions"
              :key="scope.value"
              :value="scope.value"
            >
              {{ scope.label }}
            </option>
          </select>
        </section>

        <section class="permission-editor">
          <header>
            <div>
              <p class="admin-eyebrow">PERMISSION CONFIGURATION</p>
              <h3>
                权限配置
                <small>{{ draft.permissionCodes.length }}项已选</small>
              </h3>
            </div>
            <input
              v-model="permissionKeyword"
              type="search"
              placeholder="搜索权限名称或编码"
            />
          </header>

          <div
            v-for="group in permissionGroups"
            :key="group.key"
            class="permission-group"
          >
            <div class="permission-group-heading">
              <strong>{{ group.name }}</strong>
              <button type="button" @click="toggleGroup(group.items)">
                全选/取消
              </button>
            </div>
            <label
              v-for="permission in group.items"
              :key="permission.id"
              class="permission-option"
              :class="{ 'is-locked': isPermissionLocked(permission.code) }"
            >
              <input
                v-model="draft.permissionCodes"
                type="checkbox"
                :value="permission.code"
                :disabled="isPermissionLocked(permission.code)"
              />
              <span>
                <strong>{{ permission.name }}</strong>
                <small>{{ permission.description }}</small>
                <code>{{ permission.code }}</code>
              </span>
            </label>
          </div>
        </section>

        <footer class="role-editor-actions">
          <span>最近更新：{{ formatTime(selectedRole.updatedAt) }}</span>
          <div>
            <button
              v-if="!selectedRole.isSystem"
              class="admin-danger-text"
              type="button"
              @click="deleteOpen = true"
            >
              删除角色
            </button>
            <button type="button" @click="resetDraft">重置修改</button>
            <button
              class="admin-primary-button"
              type="button"
              :disabled="saving"
              @click="saveRole"
            >
              {{ saving ? "正在保存…" : "保存角色权限" }}
            </button>
          </div>
        </footer>
      </section>
    </section>

    <section class="prototype-panel role-change-panel"><header class="prototype-panel-heading"><div><p>RECENT CHANGES</p><h2>最近权限变更</h2><span>角色权限和数据范围变更均写入审计日志</span></div><RouterLink to="/admin/audit-logs">查看全部审计日志</RouterLink></header><div class="history-list"><article><b>系统管理员</b><span><strong>更新“风险管理员”的风险治理权限</strong><small>今天 09:36 · 增加风险合并与驳回能力</small></span></article><article><b>系统管理员</b><span><strong>更新“项目经理”的默认数据范围</strong><small>昨天 17:20 · 调整为本人负责及授权项目</small></span></article></div></section>

    <ModalDialog
      v-if="createOpen"
      title="新增自定义角色"
      eyebrow="CREATE ROLE"
      @close="createOpen = false"
    >
      <form class="modal-form" @submit.prevent="createRole">
        <label>
          <span>角色名称 *</span>
          <input v-model="createForm.name" required maxlength="128" />
        </label>
        <label>
          <span>角色编码 *</span>
          <input
            v-model="createForm.code"
            required
            maxlength="64"
            placeholder="例如 PROJECT_OBSERVER"
          />
        </label>
        <label>
          <span>角色说明</span>
          <textarea v-model="createForm.description" maxlength="500"></textarea>
        </label>
        <label>
          <span>复制权限自</span>
          <select v-model="createCopyFrom">
            <option value="">不复制</option>
            <option v-for="role in roles" :key="role.id" :value="role.id">
              {{ role.name }}
            </option>
          </select>
        </label>
        <label>
          <span>默认数据范围</span>
          <select v-model="createForm.defaultDataScope">
            <option
              v-for="scope in scopeOptions"
              :key="scope.value"
              :value="scope.value"
            >
              {{ scope.label }}
            </option>
          </select>
        </label>
        <label class="switch-row">
          <span><strong>创建后启用</strong></span>
          <input v-model="createForm.enabled" type="checkbox" />
        </label>
        <p v-if="errorMessage" class="admin-alert" role="alert">
          {{ errorMessage }}
        </p>
      </form>
      <template #footer>
        <button type="button" @click="createOpen = false">取消</button>
        <button
          class="admin-primary-button"
          type="button"
          :disabled="saving"
          @click="createRole"
        >
          创建角色
        </button>
      </template>
    </ModalDialog>

    <ModalDialog
      v-if="deleteOpen && selectedRole"
      title="删除自定义角色"
      eyebrow="DESTRUCTIVE ACTION"
      @close="deleteOpen = false"
    >
      <p class="modal-copy">
        确认删除“{{ selectedRole.name }}”？有关联用户时系统会拒绝删除，
        需先在用户管理页迁移用户。
      </p>
      <template #footer>
        <button type="button" @click="deleteOpen = false">取消</button>
        <button
          class="admin-danger-button"
          type="button"
          :disabled="saving"
          @click="deleteRole"
        >
          确认删除
        </button>
      </template>
    </ModalDialog>

    <ModalDialog v-if="matrixOpen" title="角色权限矩阵" eyebrow="PERMISSION MATRIX" @close="matrixOpen = false"><div class="admin-table-scroll"><table class="admin-table permission-matrix"><thead><tr><th>权限模块</th><th v-for="role in roles" :key="role.id">{{ role.name }}</th></tr></thead><tbody><tr v-for="moduleName in ['DASHBOARD', 'AGENT', 'RISK', 'MAILBOX', 'ADMIN']" :key="moduleName"><td><strong>{{ moduleName }}</strong></td><td v-for="role in roles" :key="role.id"><span>{{ role.permissionCodes.some((code) => permissions.find((item) => item.code === code)?.module === moduleName) ? "✓" : "—" }}</span></td></tr></tbody></table></div></ModalDialog>
  </AdminShell>
</template>
