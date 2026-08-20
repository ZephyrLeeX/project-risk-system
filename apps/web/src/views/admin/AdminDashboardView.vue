<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";

import { adminApi } from "@/api/admin";
import type {
  AttentionItem,
  HealthItem,
  OverviewLink,
  RecentAuditItem,
  UnavailableSection,
} from "@/api/admin";
import { aiProviderApi } from "@/api/ai-providers";
import { projectImportApi } from "@/api/imports";
import AdminShell from "@/components/AdminShell.vue";
import ModalDialog from "@/components/ModalDialog.vue";
import {
  attentionStatusClass,
  attentionStatusLabel,
  auditResultLabel,
  findUnavailable,
  healthGlyph,
  healthStatusLabel,
  overallHealthLabel,
  overallHealthStatus,
  unavailableReasonLabel,
} from "@/utils/admin-overview";

const router = useRouter();
const loading = ref(true);
const metricsLoading = ref(false);
const error = ref("");
const updatedAt = ref(new Date());
const overview = ref<null | Awaited<ReturnType<typeof adminApi.overview>>>(null);
const selectedModule = ref<null | { title: string; description: string; to: string; abilities: string[] }>(null);
const metrics = ref({ users: 0, activeUsers: 0, roles: 0, permissions: 0, imports: 0, aiServices: 0, healthyAiServices: 0, expiringAiServices: 0 });
const recentImports = ref<Array<{ id: string; fileName: string; status: string; totalRows: number; uploadedByName: string; createdAt: string }>>([]);

const modules = [
  { title: "用户管理", description: "维护账号、状态、部门和项目数据范围", to: "/admin/users", meta: "账号与范围", abilities: ["新增与编辑用户", "角色和项目范围分配", "启停、解锁与重置密码"] },
  { title: "角色权限", description: "配置角色能力、菜单权限与数据范围", to: "/admin/roles", meta: "角色与权限", abilities: ["预置与自定义角色", "权限树配置", "角色用户与变更记录"] },
  { title: "项目数据导入", description: "导入项目清单与回款 Excel，校验并发布批次", to: "/admin/imports", meta: "Excel 批次", abilities: ["上传与解析", "项目匹配确认", "发布与批次回滚"] },
  { title: "API Key 管理", description: "维护 AI 服务接入凭据、有效期和默认服务", to: "/admin/api-keys", meta: "AI 服务", abilities: ["密钥加密与掩码", "连接测试和轮换", "调用策略与日志"] },
  { title: "系统配置", description: "配置风险等级、分类字典与通知规则", to: "/admin/system-config", meta: "规则字典", abilities: ["风险规则", "邮箱识别规则", "安全与通知策略"] },
  { title: "审计日志", description: "追踪登录、配置、权限和数据变更记录", to: "/admin/audit-logs", meta: "全链路审计", abilities: ["多条件检索", "脱敏变更详情", "受控导出"] },
];
const userModule = modules[0]!;
const roleModule = modules[1]!;
const importModule = modules[2]!;
const aiModule = modules[3]!;

const latestImport = computed(() => recentImports.value[0]);

const healthItems = computed<HealthItem[] | null>(() => overview.value?.health ?? null);
const attentionItems = computed<AttentionItem[] | null>(() => overview.value?.attention ?? null);
const recentAuditItems = computed<RecentAuditItem[] | null>(() => overview.value?.recentAudit ?? null);
const unavailableSections = computed<UnavailableSection[]>(() => overview.value?.unavailableSections ?? []);

const healthUnavailable = computed(() => findUnavailable(unavailableSections.value, "health"));
const attentionUnavailable = computed(() => findUnavailable(unavailableSections.value, "attention"));
const auditUnavailable = computed(() => findUnavailable(unavailableSections.value, "recentAudit"));

const overallHealth = computed(() => overallHealthStatus(healthItems.value));
const overallHealthClass = computed(() => {
  switch (overallHealth.value) {
    case "ALL_HEALTHY":
      return "status-ok";
    case "DEGRADED":
      return "status-warn";
    case "UNAVAILABLE":
      return "status-bad";
    default:
      return "status-neutral";
  }
});

function formatTime(value: Date | string): string {
  return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(value)).replace(/\//g, "-");
}

function navigateLink(link: OverviewLink | null): void {
  if (!link) return;
  void router.push({ path: link.path, query: { ...link.query } });
}

async function loadOverview(): Promise<void> {
  loading.value = true;
  error.value = "";
  try {
    overview.value = await adminApi.overview();
    updatedAt.value = new Date(overview.value.generatedAt);
  } catch (requestError) {
    overview.value = null;
    error.value = requestError instanceof Error ? requestError.message : "管理概览加载失败";
  } finally {
    loading.value = false;
  }
  // Supplementary admin metrics degrade gracefully; the overview contract
  // (health/attention/recentAudit) remains the authoritative panel state.
  await loadMetrics();
}

async function loadMetrics(): Promise<void> {
  metricsLoading.value = true;
  try {
    const [summary, roles, permissions, batches, accounts] = await Promise.all([
      adminApi.userSummary(), adminApi.roles(), adminApi.permissions(), projectImportApi.batches(1, 3), aiProviderApi.accounts(),
    ]);
    metrics.value = { users: summary.total, activeUsers: summary.active, roles: roles.length, permissions: permissions.length, imports: batches.total, aiServices: accounts.length, healthyAiServices: accounts.filter((account) => account.health === "AVAILABLE").length, expiringAiServices: 0 };
    recentImports.value = batches.items;
  } catch {
    // Leave the previous/zero metric state; overview panels stay authoritative.
  } finally {
    metricsLoading.value = false;
  }
}

function openModule(item: (typeof modules)[number]): void {
  selectedModule.value = item;
}

onMounted(() => void loadOverview());
</script>

<template>
  <AdminShell>
    <section class="admin-page-heading prototype-heading">
      <div><p class="admin-eyebrow">ADMINISTRATION OVERVIEW</p><h1>管理概览</h1><p>集中管理系统账号、权限边界、基础数据和 AI 服务配置。</p></div>
      <div class="prototype-heading-actions"><span class="prototype-update"><i></i><span><strong>系统状态已更新</strong><small>{{ formatTime(updatedAt) }}</small></span></span><button class="admin-outline-button" type="button" :disabled="loading" @click="loadOverview">↻ 刷新状态</button></div>
    </section>

    <p v-if="error" class="overview-error" role="alert">{{ error }}<button type="button" :disabled="loading" @click="loadOverview">重试</button></p>

    <section class="prototype-metric-grid" aria-label="后台关键指标">
      <button class="prototype-metric tone-blue" type="button" @click="openModule(userModule)"><span class="metric-glyph">人</span><small>系统用户</small><strong>{{ metrics.users }}<em>人</em></strong><p>{{ metrics.activeUsers }} 人当前启用</p></button>
      <button class="prototype-metric tone-violet" type="button" @click="openModule(roleModule)"><span class="metric-glyph">权</span><small>角色与权限</small><strong>{{ metrics.roles }}<em>个角色</em></strong><p>覆盖 {{ metrics.permissions }} 个权限点</p></button>
      <button class="prototype-metric tone-green" type="button" @click="openModule(importModule)"><span class="metric-glyph">表</span><small>数据导入批次</small><strong>{{ metrics.imports }}<em>批</em></strong><p>{{ latestImport ? `最新 ${formatTime(latestImport.createdAt)}` : "暂无导入批次" }}</p></button>
      <button class="prototype-metric tone-orange" type="button" @click="openModule(aiModule)"><span class="metric-glyph">钥</span><small>AI 服务</small><strong>{{ metrics.aiServices }}<em>项</em></strong><p>{{ metrics.expiringAiServices }} 个 Key 即将到期</p></button>
    </section>

    <section class="prototype-panel health-panel"><header class="prototype-panel-heading"><div><p>SYSTEM HEALTH</p><h2>系统运行状态</h2></div><span :class="overallHealthClass"><i></i>{{ overallHealthLabel(overallHealth) }}</span></header>
      <div class="health-grid">
        <p v-if="!overview" class="prototype-empty">{{ loading ? "正在加载系统健康状态…" : "系统健康状态不可用" }}</p>
        <p v-else-if="healthUnavailable" class="prototype-empty overview-unavailable">{{ unavailableReasonLabel(healthUnavailable.reason) }}</p>
        <p v-else-if="!healthItems?.length" class="prototype-empty">暂无健康检查数据</p>
        <button v-for="item in healthItems" v-else :key="item.key" type="button" :class="`health-${item.status.toLowerCase()}`" @click="navigateLink(item.link)"><span class="health-glyph">{{ healthGlyph(item.status) }}</span><span><strong>{{ item.label }}</strong><small>{{ item.summary }}</small></span><em>{{ healthStatusLabel(item.status) }}</em></button>
      </div>
    </section>

    <div class="prototype-two-columns">
      <section class="prototype-panel"><header class="prototype-panel-heading"><div><p>MANAGEMENT CENTER</p><h2>管理中心</h2></div><span>按后台职责集中配置</span></header><div class="management-grid"><button v-for="item in modules" :key="item.to" type="button" @click="openModule(item)"><span class="management-icon">{{ item.title.slice(0,1) }}</span><span><strong>{{ item.title }}</strong><small>{{ item.description }}</small></span><em>{{ item.meta }}</em><b>›</b></button></div></section>
      <section class="prototype-panel attention-panel"><header class="prototype-panel-heading"><div><p>REQUIRES ATTENTION</p><h2>待处理事项</h2></div><b>{{ attentionItems?.length ?? 0 }}</b></header>
        <div class="attention-list">
          <p v-if="!overview" class="prototype-empty">{{ loading ? "正在加载待处理事项…" : "待处理事项不可用" }}</p>
          <p v-else-if="attentionUnavailable" class="prototype-empty overview-unavailable">{{ unavailableReasonLabel(attentionUnavailable.reason) }}</p>
          <p v-else-if="!attentionItems?.length" class="prototype-empty">暂无待处理事项</p>
          <button v-for="item in attentionItems" v-else :key="item.id" type="button" @click="navigateLink(item.link)"><em :class="attentionStatusClass(item.status)">{{ attentionStatusLabel(item.status) }}</em><span><strong>{{ item.title }}</strong><small>{{ item.summary }}</small></span><time>{{ formatTime(item.occurredAt) }}</time></button>
        </div>
        <button class="view-all-button" type="button" @click="router.push('/admin/audit-logs')">查看全部审计动态</button>
      </section>
    </div>

    <div class="prototype-two-columns bottom-grid">
      <section class="prototype-panel"><header class="prototype-panel-heading"><div><p>DATA IMPORTS</p><h2>最近导入批次</h2></div><button class="text-action" type="button" @click="router.push('/admin/imports')">进入数据导入</button></header><div class="admin-table-scroll"><table class="admin-table"><thead><tr><th>导入文件</th><th>批次号</th><th>数据结果</th><th>操作人</th><th>完成时间</th><th>状态</th></tr></thead><tbody><tr v-for="batch in recentImports" :key="batch.id"><td><strong>{{ batch.fileName }}</strong></td><td><code>{{ batch.id.slice(0,13).toUpperCase() }}</code></td><td>{{ batch.totalRows }} 条</td><td>{{ batch.uploadedByName }}</td><td>{{ formatTime(batch.createdAt) }}</td><td><span class="status-pill">{{ batch.status }}</span></td></tr><tr v-if="!recentImports.length"><td colspan="6" class="prototype-empty">{{ metricsLoading ? "正在加载导入批次…" : "暂无导入批次" }}</td></tr></tbody></table></div></section>
      <section class="prototype-panel"><header class="prototype-panel-heading"><div><p>AUDIT ACTIVITY</p><h2>最近审计动态</h2></div><button class="text-action" type="button" @click="router.push('/admin/audit-logs')">全部日志</button></header>
        <ol v-if="overview && recentAuditItems?.length" class="audit-activity"><li v-for="item in recentAuditItems" :key="item.id" :style="{ '--tone': item.result === 'SUCCESS' ? '#1caf87' : '#ec575c' }"><i></i><span><strong>{{ item.actorName }}</strong><p>{{ item.summary }}<small class="audit-result-tag" :class="item.result === 'SUCCESS' ? 'tag-success' : 'tag-failure'">{{ auditResultLabel(item.result) }}</small></p><time>{{ formatTime(item.occurredAt) }}</time></span></li></ol>
        <p v-else-if="!overview" class="prototype-empty audit-empty">{{ loading ? "正在加载审计动态…" : "审计动态不可用" }}</p>
        <p v-else-if="auditUnavailable" class="prototype-empty overview-unavailable audit-empty">{{ unavailableReasonLabel(auditUnavailable.reason) }}</p>
        <p v-else class="prototype-empty audit-empty">暂无审计动态</p>
      </section>
    </div>

    <footer class="prototype-page-footer"><span>项目风险管理平台 V2.0</span><span>后台权限范围：全部项目 · 用户、角色、数据与系统配置</span></footer>

    <ModalDialog v-if="selectedModule" eyebrow="MODULE OVERVIEW" :title="selectedModule.title" @close="selectedModule=null"><p class="modal-copy">{{ selectedModule.description }}</p><section class="prototype-modal-section"><h3>主要能力</h3><ul><li v-for="ability in selectedModule.abilities" :key="ability">{{ ability }}</li></ul></section><template #footer><button type="button" @click="selectedModule=null">关闭</button><button class="admin-primary-button" type="button" @click="router.push(selectedModule.to); selectedModule=null">进入管理</button></template></ModalDialog>
  </AdminShell>
</template>

<style scoped>
.overview-error {
  display: flex;
  margin: 0 0 16px;
  padding: 13px 16px;
  border: 1px solid #f3b9bd;
  border-radius: 12px;
  color: #bc3038;
  background: #fff0f1;
  font-weight: 700;
  align-items: center;
  gap: 16px;
}
.overview-error button {
  margin-left: auto;
  padding: 7px 14px;
  border: 1px solid #e3a3a9;
  border-radius: 9px;
  color: #bc3038;
  background: #fff;
  font-weight: 700;
  cursor: pointer;
}

.status-warn {
  display: inline-flex;
  align-items: center;
  gap: 9px;
  color: #c97800;
  font-weight: 700;
}
.status-warn i {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: #ee9208;
  box-shadow: 0 0 0 6px rgba(238, 146, 8, 0.1);
}
.status-bad {
  display: inline-flex;
  align-items: center;
  gap: 9px;
  color: #d64149;
  font-weight: 700;
}
.status-bad i {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: #ec575c;
  box-shadow: 0 0 0 6px rgba(236, 87, 92, 0.1);
}
.status-neutral {
  display: inline-flex;
  align-items: center;
  gap: 9px;
  color: #8ba1b1;
  font-weight: 700;
}
.status-neutral i {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: #b7c6d0;
  box-shadow: 0 0 0 6px rgba(183, 198, 208, 0.1);
}

.health-grid p.prototype-empty,
.attention-list p.prototype-empty {
  margin: 18px;
}

.health-unavailable .health-glyph,
.health-degraded .health-glyph {
  color: #c97800;
  background: #fff2dc;
}
.health-unavailable .health-glyph {
  color: #d64149;
  background: #ffeded;
}
.health-unavailable em,
.health-degraded em {
  color: #c97800;
}
.health-unavailable em {
  color: #d64149;
}

.overview-unavailable {
  color: #b07d00 !important;
  background: #fff8e8;
  border: 1px solid #f3e2b8;
  border-radius: 12px;
  padding: 14px 16px;
  text-align: left;
}

.audit-empty {
  padding: 28px 24px;
}
.audit-result-tag {
  display: inline-block;
  margin-left: 8px;
  padding: 1px 7px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 700;
}
.tag-success {
  color: #159276;
  background: #e6f7f1;
}
.tag-failure {
  color: #d64149;
  background: #ffeded;
}
</style>
