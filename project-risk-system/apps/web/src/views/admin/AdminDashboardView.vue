<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";

import { adminApi } from "@/api/admin";
import { aiProviderApi } from "@/api/ai-providers";
import { projectImportApi } from "@/api/imports";
import AdminShell from "@/components/AdminShell.vue";
import ModalDialog from "@/components/ModalDialog.vue";

const router = useRouter();
const loading = ref(true);
const updatedAt = ref(new Date());
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

function formatTime(value: Date | string): string {
  return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(value)).replace(/\//g, "-");
}

async function loadOverview(): Promise<void> {
  loading.value = true;
  try {
    const [summary, roles, permissions, batches, aiSummary] = await Promise.all([
      adminApi.userSummary(), adminApi.roles(), adminApi.permissions(), projectImportApi.batches(1, 3), aiProviderApi.summary(),
    ]);
    metrics.value = { users: summary.total, activeUsers: summary.active, roles: roles.length, permissions: permissions.length, imports: batches.total, aiServices: aiSummary.total, healthyAiServices: aiSummary.healthy, expiringAiServices: aiSummary.expiring };
    recentImports.value = batches.items;
    updatedAt.value = new Date();
  } finally {
    loading.value = false;
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

    <section class="prototype-metric-grid" aria-label="后台关键指标">
      <button class="prototype-metric tone-blue" type="button" @click="openModule(userModule)"><span class="metric-glyph">人</span><small>系统用户</small><strong>{{ metrics.users }}<em>人</em></strong><p>{{ metrics.activeUsers }} 人当前启用</p></button>
      <button class="prototype-metric tone-violet" type="button" @click="openModule(roleModule)"><span class="metric-glyph">权</span><small>角色与权限</small><strong>{{ metrics.roles }}<em>个角色</em></strong><p>覆盖 {{ metrics.permissions }} 个权限点</p></button>
      <button class="prototype-metric tone-green" type="button" @click="openModule(importModule)"><span class="metric-glyph">表</span><small>数据导入批次</small><strong>{{ metrics.imports }}<em>批</em></strong><p>{{ latestImport ? `最新 ${formatTime(latestImport.createdAt)}` : "暂无导入批次" }}</p></button>
      <button class="prototype-metric tone-orange" type="button" @click="openModule(aiModule)"><span class="metric-glyph">钥</span><small>AI 服务</small><strong>{{ metrics.aiServices }}<em>项</em></strong><p>{{ metrics.expiringAiServices }} 个 Key 即将到期</p></button>
    </section>

    <section class="prototype-panel health-panel"><header class="prototype-panel-heading"><div><p>SYSTEM HEALTH</p><h2>系统运行状态</h2></div><span class="status-ok"><i></i>全部核心服务正常</span></header><div class="health-grid">
      <button v-for="item in [{name:'身份与权限服务',copy:'用户登录、角色鉴权正常'},{name:'项目数据服务',copy:latestImport ? '最新批次已完成' : '等待首次导入'},{name:'AI 分析服务',copy:metrics.healthyAiServices ? `${metrics.healthyAiServices}项服务最近连接成功` : '等待配置并完成连接测试'},{name:'审计记录服务',copy:'日志链路完整可追溯'}]" :key="item.name" type="button"><span class="health-glyph">✓</span><span><strong>{{ item.name }}</strong><small>{{ item.copy }}</small></span><em>正常</em></button>
    </div></section>

    <div class="prototype-two-columns">
      <section class="prototype-panel"><header class="prototype-panel-heading"><div><p>MANAGEMENT CENTER</p><h2>管理中心</h2></div><span>按后台职责集中配置</span></header><div class="management-grid"><button v-for="item in modules" :key="item.to" type="button" @click="openModule(item)"><span class="management-icon">{{ item.title.slice(0,1) }}</span><span><strong>{{ item.title }}</strong><small>{{ item.description }}</small></span><em>{{ item.meta }}</em><b>›</b></button></div></section>
      <section class="prototype-panel attention-panel"><header class="prototype-panel-heading"><div><p>REQUIRES ATTENTION</p><h2>待处理事项</h2></div><b>3</b></header><div class="attention-list"><button type="button" @click="router.push('/admin/imports')"><em class="danger">紧急</em><span><strong>项目导入存在待确认匹配</strong><small>需确认项目负责人后再发布数据</small></span><time>09:12</time></button><button type="button" @click="router.push('/admin/api-keys')"><em class="warning">提醒</em><span><strong>1个 API Key 将在30天内到期</strong><small>建议提前更新并完成连通性测试</small></span><time>08:45</time></button><button type="button" @click="router.push('/admin/users')"><em>关注</em><span><strong>复核长期未登录账号</strong><small>确认账号是否仍需保留</small></span><time>昨日</time></button></div><button class="view-all-button" type="button" @click="router.push('/admin/audit-logs')">查看全部待处理事项</button></section>
    </div>

    <div class="prototype-two-columns bottom-grid">
      <section class="prototype-panel"><header class="prototype-panel-heading"><div><p>DATA IMPORTS</p><h2>最近导入批次</h2></div><button class="text-action" type="button" @click="router.push('/admin/imports')">进入数据导入</button></header><div class="admin-table-scroll"><table class="admin-table"><thead><tr><th>导入文件</th><th>批次号</th><th>数据结果</th><th>操作人</th><th>完成时间</th><th>状态</th></tr></thead><tbody><tr v-for="batch in recentImports" :key="batch.id"><td><strong>{{ batch.fileName }}</strong></td><td><code>{{ batch.id.slice(0,13).toUpperCase() }}</code></td><td>{{ batch.totalRows }} 条</td><td>{{ batch.uploadedByName }}</td><td>{{ formatTime(batch.createdAt) }}</td><td><span class="status-pill">{{ batch.status }}</span></td></tr><tr v-if="!recentImports.length"><td colspan="6">暂无导入批次</td></tr></tbody></table></div></section>
      <section class="prototype-panel"><header class="prototype-panel-heading"><div><p>AUDIT ACTIVITY</p><h2>最近审计动态</h2></div><button class="text-action" type="button" @click="router.push('/admin/audit-logs')">全部日志</button></header><ol class="audit-activity"><li><i class="tone-blue"></i><span><strong>系统管理员</strong><p>查看后台管理概览</p><time>刚刚</time></span></li><li><i class="tone-green"></i><span><strong>系统管理员</strong><p>完成项目清单导入</p><time>今天</time></span></li><li><i class="tone-violet"></i><span><strong>系统管理员</strong><p>更新用户项目数据范围</p><time>昨天</time></span></li></ol></section>
    </div>

    <footer class="prototype-page-footer"><span>项目风险管理平台 V2.0</span><span>后台权限范围：全部项目 · 用户、角色、数据与系统配置</span></footer>

    <ModalDialog v-if="selectedModule" eyebrow="MODULE OVERVIEW" :title="selectedModule.title" @close="selectedModule=null"><p class="modal-copy">{{ selectedModule.description }}</p><section class="prototype-modal-section"><h3>主要能力</h3><ul><li v-for="ability in selectedModule.abilities" :key="ability">{{ ability }}</li></ul></section><template #footer><button type="button" @click="selectedModule=null">关闭</button><button class="admin-primary-button" type="button" @click="router.push(selectedModule.to); selectedModule=null">进入管理</button></template></ModalDialog>
  </AdminShell>
</template>
