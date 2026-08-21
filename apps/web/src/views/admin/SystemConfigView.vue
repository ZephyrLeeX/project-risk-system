<script setup lang="ts">
import { computed, onMounted, ref, toRaw } from "vue";

import type {
  ConfigRiskLevel,
  ProjectOption,
  SystemConfigModule,
  SystemConfigOverview,
  SystemConfigReleaseDetail,
  SystemConfigReleaseItem,
  SystemConfigSnapshot,
  SystemProjectAlias,
  SystemRiskCategory,
} from "@risk-platform/contracts";

import { systemConfigApi } from "@/api/system-config";
import { cloneConfigSnapshot } from "@/api/system-config-contract";
import AdminShell from "@/components/AdminShell.vue";
import AgentScopeRulesPanel from "@/components/admin/AgentScopeRulesPanel.vue";
import ModalDialog from "@/components/ModalDialog.vue";
import { useAuthStore } from "@/stores/auth";

type SectionKey = "overview" | "risk" | "mail" | "alias" | "agentScope" | "security" | "notification" | "history";

const auth = useAuthStore();

const activeSection = ref<SectionKey>("overview");
const overview = ref<SystemConfigOverview | null>(null);
const draft = ref<SystemConfigSnapshot | null>(null);
const original = ref<SystemConfigSnapshot | null>(null);
const releases = ref<SystemConfigReleaseItem[]>([]);
const projectOptions = ref<ProjectOption[]>([]);
const changedModules = ref(new Set<SystemConfigModule>());
const loading = ref(true);
const saving = ref(false);
const error = ref("");
const toast = ref("");
const publishOpen = ref(false);
const discardOpen = ref(false);
const categoryOpen = ref(false);
const aliasOpen = ref(false);
const selectedRelease = ref<SystemConfigReleaseDetail | null>(null);
const historyFilter = ref<SystemConfigModule | "all">("all");
const categorySearch = ref("");
const aliasSearch = ref("");
const keywordInput = ref("");
const riskKeywordInput = ref("");
const changeSummary = ref("");
const editingCategoryIndex = ref<number | null>(null);
const categoryForm = ref<SystemRiskCategory>(emptyCategory());
const editingAliasId = ref<string | null>(null);
const aliasForm = ref({ projectId: "", aliases: "", note: "" });

const directory = computed(() => [
  { key: "overview" as const, name: "配置概览", copy: "状态与生效版本" },
  { key: "risk" as const, name: "风险规则", copy: "类别、等级与关键词", count: String((draft.value?.categories.length ?? 0) + (draft.value?.levels.length ?? 0)) },
  { key: "mail" as const, name: "周报与邮箱同步", copy: "周期与识别关键词", count: "3" },
  { key: "alias" as const, name: "项目别名", copy: "简称与标准项目映射", count: String(draft.value?.aliases.filter((item) => item.isActive).length ?? 0) },
  // Agent 范围规则不是 SystemConfig 快照的一部分：独立 CRUD、保存即生效，
  // 不参与发布流程。目录项仅对持有 agent.scope.manage 的管理员可见；
  // 后端仍是权限权威（无权限调用一律 403）。
  ...(auth.user?.permissions.includes("agent.scope.manage")
    ? [{ key: "agentScope" as const, name: "Agent 范围规则", copy: "会话数据范围拦截" }]
    : []),
  { key: "security" as const, name: "会话与登录", copy: "超时和账号保护", count: "4" },
  { key: "notification" as const, name: "后台提醒", copy: "异常与到期提醒", count: "4" },
  { key: "history" as const, name: "变更记录", copy: "版本与审计摘要" },
]);
const sectionTitle = computed(() => directory.value.find((item) => item.key === activeSection.value)?.name ?? "配置概览");
const changed = computed(() => changedModules.value.size > 0);
const changeCount = computed(() => changedModules.value.size);
const filteredCategories = computed(() => {
  const keyword = categorySearch.value.trim().toLowerCase();
  return (draft.value?.categories ?? []).filter((item) => !keyword || `${item.name}${item.code}${item.keywords.join(" ")}`.toLowerCase().includes(keyword));
});
const filteredAliases = computed(() => {
  const keyword = aliasSearch.value.trim().toLowerCase();
  return (draft.value?.aliases ?? []).filter((item) => !keyword || `${item.projectName}${item.projectCode ?? ""}${item.alias}`.toLowerCase().includes(keyword));
});
const filteredReleases = computed(() => releases.value.filter((item) => historyFilter.value === "all" || item.module === historyFilter.value || item.module === "ALL"));

function emptyCategory(): SystemRiskCategory {
  return { id: null, code: "", name: "", keywords: [], colorToken: "#4C8FE8", description: null, defaultLevel: null, sortOrder: 0, isActive: true, riskCount: 0 };
}

function formatTime(value: string): string { return new Date(value).toLocaleString("zh-CN", { hour12: false }); }
function showToast(message: string): void { toast.value = message; window.setTimeout(() => { if (toast.value === message) toast.value = ""; }, 2600); }
function markChanged(module: SystemConfigModule): void { changedModules.value = new Set([...changedModules.value, module]); }
function levelName(level: ConfigRiskLevel | null): string { return level === "HIGH" ? "高风险" : level === "MEDIUM" ? "中风险" : level === "LOW" ? "低风险" : "未指定"; }

async function load(): Promise<void> {
  loading.value = true;
  error.value = "";
  try {
    const [data, history, projects] = await Promise.all([
      systemConfigApi.overview(),
      systemConfigApi.releases(),
      systemConfigApi.projectOptions(),
    ]);
    overview.value = data;
    draft.value = cloneConfigSnapshot(data.snapshot);
    original.value = cloneConfigSnapshot(data.snapshot);
    releases.value = history;
    projectOptions.value = projects;
    changedModules.value = new Set();
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "系统配置加载失败";
  } finally { loading.value = false; }
}

function discardChanges(): void {
  if (original.value) draft.value = cloneConfigSnapshot(original.value);
  changedModules.value = new Set();
  discardOpen.value = false;
  showToast(`草稿已恢复为已发布的 ${overview.value?.version ?? "当前"} 配置。`);
}

function openCategory(index?: number): void {
  editingCategoryIndex.value = index ?? null;
  const existing = index === undefined ? undefined : draft.value?.categories[index];
  // ``existing`` is a deep reactive Proxy (``draft`` is a ``ref``), which
  // ``structuredClone`` rejects with ``DataCloneError`` — unwrap it first.
  categoryForm.value = existing ? structuredClone(toRaw(existing)) : emptyCategory();
  categoryOpen.value = true;
}

function saveCategory(): void {
  if (!draft.value) return;
  // ``categoryForm.value`` is a deep reactive Proxy (``ref``), so unwrap
  // before cloning into the draft — same ``structuredClone`` Proxy guard.
  const form = toRaw(categoryForm.value);
  form.code = form.code.trim().toUpperCase().replace(/[\s-]+/g, "_");
  form.name = form.name.trim();
  form.keywords = [...new Set(form.keywords.map((item) => item.trim()).filter(Boolean))];
  if (!form.name || !/^[A-Z][A-Z0-9_]{1,63}$/.test(form.code)) { error.value = "请填写类别名称，并使用大写字母、数字和下划线作为类别编码"; return; }
  if (editingCategoryIndex.value === null) {
    form.sortOrder = (draft.value.categories[draft.value.categories.length - 1]?.sortOrder ?? 0) + 10;
    draft.value.categories.push(structuredClone(form));
  } else draft.value.categories[editingCategoryIndex.value] = structuredClone(form);
  categoryOpen.value = false;
  markChanged("RISK");
}

function moveCategory(category: SystemRiskCategory, offset: -1 | 1): void {
  if (!draft.value) return;
  const index = draft.value.categories.indexOf(category);
  const next = index + offset;
  if (next < 0 || next >= draft.value.categories.length) return;
  const currentItem = draft.value.categories[index];
  const nextItem = draft.value.categories[next];
  if (!currentItem || !nextItem) return;
  draft.value.categories[index] = nextItem;
  draft.value.categories[next] = currentItem;
  draft.value.categories.forEach((item, position) => { item.sortOrder = (position + 1) * 10; });
  markChanged("RISK");
}

function addCategoryKeyword(value: string): void {
  const items = value.split(/[，,]/).map((item) => item.trim()).filter(Boolean);
  categoryForm.value.keywords = [...new Set([...categoryForm.value.keywords, ...items])];
}

function addKeyword(target: "subject" | "risk"): void {
  if (!draft.value) return;
  const input = target === "subject" ? keywordInput : riskKeywordInput;
  const value = input.value.trim();
  if (!value) return;
  const list = target === "subject" ? draft.value.mail.subjectKeywords : draft.value.mail.riskKeywords;
  if (!list.includes(value)) list.push(value);
  input.value = "";
  markChanged("MAIL");
}

function openAlias(item?: SystemProjectAlias): void {
  editingAliasId.value = item?.id ?? null;
  aliasForm.value = item ? { projectId: item.projectId, aliases: item.alias, note: item.note ?? "" } : { projectId: "", aliases: "", note: "" };
  aliasOpen.value = true;
}

function saveAlias(): void {
  if (!draft.value) return;
  const project = projectOptions.value.find((item) => item.id === aliasForm.value.projectId);
  const names = [...new Set(aliasForm.value.aliases.split(/[，,]/).map((item) => item.trim()).filter(Boolean))];
  if (!project || names.length === 0) { error.value = "请选择标准项目并填写至少一个项目别名"; return; }
  if (editingAliasId.value) {
    const existing = draft.value.aliases.find((item) => item.id === editingAliasId.value);
    if (existing) Object.assign(existing, { projectId: project.id, projectName: project.name, projectCode: project.externalCode, alias: names[0], note: aliasForm.value.note || null });
    names.slice(1).forEach((name) => draft.value!.aliases.push(aliasItem(project, name)));
  } else names.forEach((name) => draft.value!.aliases.push(aliasItem(project, name)));
  aliasOpen.value = false;
  markChanged("ALIAS");
}

function aliasItem(project: ProjectOption, alias: string): SystemProjectAlias {
  return { id: null, projectId: project.id, projectName: project.name, projectCode: project.externalCode, projectOwnerName: null, alias, source: "系统管理员", note: aliasForm.value.note || null, isActive: true, hitCount: 0, lastHitAt: null };
}

async function publish(): Promise<void> {
  if (!draft.value || !changeSummary.value.trim()) { error.value = "请填写本次配置变更摘要"; return; }
  saving.value = true;
  error.value = "";
  try {
    const module: SystemConfigModule = changedModules.value.size === 1
      ? ([...changedModules.value][0] ?? "ALL")
      : "ALL";
    const result = await systemConfigApi.publish({ ...cloneConfigSnapshot(draft.value), changeCount: changeCount.value, changeSummary: changeSummary.value.trim(), module });
    overview.value = result;
    draft.value = cloneConfigSnapshot(result.snapshot);
    original.value = cloneConfigSnapshot(result.snapshot);
    releases.value = await systemConfigApi.releases();
    changedModules.value = new Set();
    publishOpen.value = false;
    changeSummary.value = "";
    showToast(`系统配置已发布为 ${result.version}`);
  } catch (reason) { error.value = reason instanceof Error ? reason.message : "系统配置发布失败"; }
  finally { saving.value = false; }
}

async function openRelease(item: SystemConfigReleaseItem): Promise<void> {
  try { selectedRelease.value = await systemConfigApi.releaseDetail(item.id); }
  catch (reason) { error.value = reason instanceof Error ? reason.message : "配置详情加载失败"; }
}

onMounted(load);
</script>

<template>
  <AdminShell>
    <section class="admin-page-heading prototype-heading"><div><p class="admin-eyebrow">SYSTEM RULES &amp; BUSINESS DICTIONARY</p><h1>系统配置</h1><p>维护现有业务运行所需的识别规则与平台参数。配置变更发布后生效，并保留版本和审计记录。</p></div><div class="prototype-heading-actions"><button class="admin-outline-button" type="button" @click="activeSection='history'">变更记录</button><button class="admin-primary-button" type="button" :disabled="!changed || saving" @click="publishOpen=true">保存并发布</button></div></section>
    <p v-if="error" class="prototype-error">{{ error }}</p>
    <section v-if="overview" class="prototype-metric-grid"><button class="prototype-metric tone-blue" type="button" @click="activeSection='overview'"><span class="metric-glyph">配</span><small>生效配置</small><strong>{{ overview.activeConfigCount }}<em>项</em></strong><p>版本 {{ overview.version }}</p></button><button class="prototype-metric tone-green" type="button" @click="activeSection='risk'"><span class="metric-glyph">类</span><small>风险类别</small><strong>{{ overview.activeCategoryCount }}<em>类</em></strong><p>当前启用</p></button><button class="prototype-metric tone-orange" type="button" @click="activeSection='risk'"><span class="metric-glyph">级</span><small>风险等级</small><strong>{{ overview.activeLevelCount }}<em>级</em></strong><p>高 · 中 · 低</p></button><button class="prototype-metric tone-violet" type="button" @click="activeSection='history'"><span class="metric-glyph">改</span><small>本月变更</small><strong>{{ overview.monthlyChangeCount }}<em>次</em></strong><p>最近：{{ formatTime(overview.publishedAt) }}</p></button></section>

    <div v-if="loading" class="prototype-panel loading-panel">正在加载系统配置…</div>
    <div v-else-if="overview && draft" class="config-workspace prototype-panel">
      <aside class="config-directory"><header><p>CONFIGURATION</p><h2>配置目录</h2></header><nav><button v-for="item in directory" :key="item.key" type="button" :class="{active:activeSection===item.key}" @click="activeSection=item.key"><span class="directory-glyph">{{ item.name.slice(0,1) }}</span><span><strong>{{ item.name }}</strong><small>{{ item.copy }}</small></span><b v-if="item.count">{{ item.count }}</b></button></nav><footer><span><i></i>当前配置运行正常</span><small>{{ overview.version }} · {{ formatTime(overview.publishedAt) }}发布</small></footer></aside>
      <section class="config-editor"><header class="prototype-panel-heading"><div><p>CONFIGURATION EDITOR</p><h2>{{ sectionTitle }}</h2><span>当前生效版本 {{ overview.version }}</span></div><span class="status-ok"><i></i>配置运行正常</span></header>

        <div v-if="activeSection==='overview'" class="config-content"><div class="config-health-grid"><article><span class="health-glyph">✓</span><div><strong>风险规则</strong><p>{{ overview.activeCategoryCount }}类风险、{{ overview.activeLevelCount }}级等级，关键词规则正常</p></div><b>正常</b></article><article><span class="health-glyph">✓</span><div><strong>周报识别</strong><p>每{{ draft.mail.syncIntervalMinutes }}分钟同步，{{ draft.mail.subjectKeywords.length }}个主题关键词</p></div><b>正常</b></article><article><span class="health-glyph">✓</span><div><strong>项目别名</strong><p>{{ draft.aliases.filter(item=>item.isActive).length }}个别名映射</p></div><b>正常</b></article><article><span class="health-glyph">✓</span><div><strong>会话与登录</strong><p>会话{{ draft.security.sessionHours }}小时，连续{{ draft.security.loginMaxAttempts }}次失败锁定</p></div><b>正常</b></article></div><div class="config-card-grid"><article class="config-card"><p>RULE IMPACT</p><h3>规则作用范围</h3><ul><li>周报邮件识别 <b>同步任务</b></li><li>AI风险提取 <b>AI服务</b></li><li>Web风险看板 <b>业务端</b></li><li>Agent智能对话 <b>业务端</b></li></ul></article><article class="config-card"><p>CURRENT VERSION</p><h3>当前配置版本 <em>{{ overview.version }}</em></h3><dl><div><dt>发布时间</dt><dd>{{ formatTime(overview.publishedAt) }}</dd></div><div><dt>发布人</dt><dd>{{ overview.publishedBy }}</dd></div><div><dt>变更内容</dt><dd>{{ overview.changeSummary }}</dd></div></dl></article></div></div>

        <div v-else-if="activeSection==='risk'" class="config-content"><section class="config-card"><header><div><p>RISK CATEGORY</p><h3>风险类别</h3></div><button type="button" @click="openCategory()">＋ 新增类别</button></header><input v-model="categorySearch" class="config-search" placeholder="搜索类别、编码或关键词"><div class="rule-table category-rule-table"><div v-for="category in filteredCategories" :key="category.id ?? category.code"><span class="risk-dot" :style="{background:category.colorToken}"></span><span><strong>{{ category.name }}</strong><small>{{ category.code }} · {{ category.riskCount }}项关联风险</small></span><select v-model="category.isActive" @change="markChanged('RISK')"><option :value="true">启用</option><option :value="false">停用</option></select><span class="row-actions"><button type="button" @click="openCategory(draft.categories.indexOf(category))">编辑</button><button type="button" @click="moveCategory(category,-1)">↑</button><button type="button" @click="moveCategory(category,1)">↓</button></span></div></div></section><section class="config-card"><p>LEVEL DEFINITION</p><h3>等级判定口径</h3><div class="level-rule-grid"><label v-for="level in draft.levels" :key="level.level"><span><b :style="{color:level.colorToken}">{{ level.displayName }}</b> · {{ levelName(level.level) }}</span><textarea v-model="level.criteria" @input="markChanged('RISK')"></textarea><input v-model="level.colorToken" type="color" @input="markChanged('RISK')"></label></div></section></div>

        <div v-else-if="activeSection==='mail'" class="config-content"><section class="config-card"><p>WEEKLY REPORT RECOGNITION</p><h3>周报与邮箱同步</h3><p>全局规则只控制同步任务；个人邮箱仍由风险管理员本人配置。</p><div class="prototype-form"><label><span>自动同步周期</span><select v-model.number="draft.mail.syncIntervalMinutes" @change="markChanged('MAIL')"><option :value="15">每15分钟</option><option :value="30">每30分钟</option><option :value="60">每1小时</option><option :value="120">每2小时</option></select></label><label><span>首次同步范围</span><select v-model.number="draft.mail.initialSyncDays" @change="markChanged('MAIL')"><option :value="30">近30天</option><option :value="90">近90天</option><option :value="180">近180天</option></select></label></div><h4>周报主题关键词</h4><div class="keyword-editor"><span v-for="(keyword,index) in draft.mail.subjectKeywords" :key="keyword" class="keyword-chip">{{ keyword }}<button type="button" :aria-label="`删除关键词“${keyword}”`" @click="draft.mail.subjectKeywords.splice(index,1);markChanged('MAIL')">×</button></span><label class="keyword-input"><input v-model="keywordInput" placeholder="输入后按回车" @keyup.enter.prevent="addKeyword('subject')"><button type="button" @click="addKeyword('subject')">添加</button></label></div><h4>正文风险显式标记</h4><div class="keyword-editor warning-keywords"><span v-for="(keyword,index) in draft.mail.riskKeywords" :key="keyword" class="keyword-chip">{{ keyword }}<button type="button" :aria-label="`删除风险关键词“${keyword}”`" @click="draft.mail.riskKeywords.splice(index,1);markChanged('MAIL')">×</button></span><label class="keyword-input"><input v-model="riskKeywordInput" placeholder="输入后按回车" @keyup.enter.prevent="addKeyword('risk')"><button type="button" @click="addKeyword('risk')">添加</button></label></div></section></div>

        <div v-else-if="activeSection==='alias'" class="config-content"><section class="config-card"><header><div><p>PROJECT ALIAS MAPPING</p><h3>项目名称映射</h3></div><button type="button" @click="openAlias()">＋ 新增项目别名</button></header><p>项目别名只用于匹配，不会修改Excel导入的标准项目名称。</p><input v-model="aliasSearch" class="config-search" placeholder="搜索标准项目或别名"><div class="admin-table-scroll"><table class="admin-table"><thead><tr><th>标准项目名称</th><th>项目编码</th><th>别名</th><th>来源</th><th>最近命中</th><th>操作</th></tr></thead><tbody><tr v-for="item in filteredAliases" :key="item.id ?? `${item.projectId}-${item.alias}`"><td><strong>{{ item.projectName }}</strong><small>负责人：{{ item.projectOwnerName ?? '未提供' }}</small></td><td>{{ item.projectCode ?? '未提供' }}</td><td><span class="alias-chip">{{ item.alias }}</span></td><td>{{ item.source }}</td><td>{{ item.lastHitAt ? formatTime(item.lastHitAt) : '尚未命中' }} · {{ item.hitCount }}次</td><td><button type="button" @click="openAlias(item)">编辑</button><button type="button" @click="item.isActive=!item.isActive;markChanged('ALIAS')">{{ item.isActive?'停用':'启用' }}</button></td></tr></tbody></table></div></section></div>

        <div v-else-if="activeSection==='agentScope' && auth.user?.permissions.includes('agent.scope.manage')" class="config-content"><AgentScopeRulesPanel /></div>

        <div v-else-if="activeSection==='security'" class="config-content"><section class="config-card"><p>SESSION &amp; LOGIN PROTECTION</p><h3>会话参数</h3><p>修改后仅影响新建会话；现有会话按原到期时间结束。</p><div class="prototype-form"><label><span>登录会话有效期（小时）</span><input v-model.number="draft.security.sessionHours" type="number" min="1" max="24" @input="markChanged('SECURITY')"></label><label><span>无操作自动退出（分钟）</span><input v-model.number="draft.security.idleTimeoutMinutes" type="number" min="10" max="240" @input="markChanged('SECURITY')"></label><label><span>连续失败锁定阈值（次）</span><input v-model.number="draft.security.loginMaxAttempts" type="number" min="3" max="10" @input="markChanged('SECURITY')"></label><label><span>账号锁定时间（分钟）</span><input v-model.number="draft.security.loginLockMinutes" type="number" min="5" max="120" @input="markChanged('SECURITY')"></label><label class="full"><span>密码最小长度</span><input v-model.number="draft.security.passwordMinLength" type="number" min="8" max="128" @input="markChanged('SECURITY')"></label></div></section><section class="config-card"><p>SECURITY POLICY</p><h3>安全策略说明</h3><ul><li>前端不保存服务端会话明文 <b>HttpOnly · SameSite</b></li><li>首次登录必须修改初始密码 <b>其他会话注销</b></li><li>登录、邮箱测试和AI测试执行限流 <b>脱敏日志</b></li></ul></section></div>

        <div v-else-if="activeSection==='notification'" class="config-content"><section class="config-card"><p>ADMIN NOTIFICATION POLICY</p><h3>系统异常与到期提醒</h3><div class="switch-list"><label><span><strong>邮箱同步失败</strong><small>同步任务连续失败或授权失效时提醒系统管理员</small></span><input v-model="draft.notifications.mailboxSyncFailure" type="checkbox" @change="markChanged('NOTIFICATION')"></label><label><span><strong>API Key即将到期</strong><small>到期前{{ draft.notifications.apiKeyExpiryDays }}天提醒，并在剩余7天时升级提示</small></span><input v-model="draft.notifications.apiKeyExpiry" type="checkbox" @change="markChanged('NOTIFICATION')"></label><label><span><strong>Excel导入失败</strong><small>批次解析失败、发布冲突或回滚失败时提醒</small></span><input v-model="draft.notifications.importFailure" type="checkbox" @change="markChanged('NOTIFICATION')"></label><label><span><strong>账号异常登录</strong><small>连续登录失败、账号锁定或权限越界时提醒</small></span><input v-model="draft.notifications.abnormalLogin" type="checkbox" @change="markChanged('NOTIFICATION')"></label></div></section></div>

        <div v-else class="config-content"><section class="config-card"><header><div><p>CONFIGURATION CHANGE HISTORY</p><h3>配置变更记录</h3></div><span class="history-filters"><button v-for="item in [{k:'all',n:'全部'},{k:'RISK',n:'风险规则'},{k:'MAIL',n:'周报同步'},{k:'SECURITY',n:'安全参数'}]" :key="item.k" type="button" :class="{active:historyFilter===item.k}" @click="historyFilter=item.k as typeof historyFilter">{{ item.n }}</button></span></header><div class="history-list"><article v-for="item in filteredReleases" :key="item.id"><b>{{ item.version }}</b><span><strong>{{ item.changeSummary }}</strong><small>{{ item.publishedBy }} · {{ formatTime(item.publishedAt) }} · {{ item.changeCount }}项</small></span><button type="button" @click="openRelease(item)">查看详情</button></article><p v-if="filteredReleases.length===0">没有符合条件的配置记录。</p></div></section></div>
      </section>
    </div>

    <div v-if="changed" class="config-unsaved-bar"><span><strong>有 {{ changeCount }} 个模块的配置尚未发布</strong><small>离开页面前请保存或放弃更改。</small></span><button type="button" @click="discardOpen=true">放弃更改</button><button class="admin-primary-button" type="button" @click="publishOpen=true">保存并发布</button></div>

    <ModalDialog v-if="categoryOpen" :eyebrow="editingCategoryIndex===null?'NEW RISK CATEGORY':'EDIT RISK CATEGORY'" :title="editingCategoryIndex===null?'新增风险类别':'编辑风险类别'" @close="categoryOpen=false"><form class="prototype-form" @submit.prevent="saveCategory"><label><span>类别名称 *</span><input v-model="categoryForm.name" required maxlength="128"></label><label><span>类别编码 *</span><input v-model="categoryForm.code" :disabled="editingCategoryIndex!==null" required maxlength="64"></label><label><span>标识颜色</span><input v-model="categoryForm.colorToken" type="color"></label><label><span>默认等级</span><select v-model="categoryForm.defaultLevel"><option :value="null">未指定</option><option value="HIGH">高风险</option><option value="MEDIUM">中风险</option><option value="LOW">低风险</option></select></label><label class="full"><span>识别关键词</span><input :value="categoryForm.keywords.join('，')" @change="addCategoryKeyword(($event.target as HTMLInputElement).value)"></label><label class="full"><span>类别说明</span><textarea v-model="categoryForm.description"></textarea></label></form><p class="modal-copy">类别编码创建后不可修改；停用已有类别不会覆盖或删除历史风险记录。</p><template #footer><button type="button" @click="categoryOpen=false">取消</button><button class="admin-primary-button" type="button" @click="saveCategory">保存到草稿</button></template></ModalDialog>

    <ModalDialog v-if="aliasOpen" :eyebrow="editingAliasId?'EDIT PROJECT ALIAS':'NEW PROJECT ALIAS'" :title="editingAliasId?'编辑项目别名':'新增项目别名'" @close="aliasOpen=false"><form class="prototype-form" @submit.prevent="saveAlias"><label class="full"><span>标准项目名称 *</span><select v-model="aliasForm.projectId" required><option value="">请选择现有标准项目</option><option v-for="project in projectOptions" :key="project.id" :value="project.id">{{ project.name }}{{ project.externalCode?` · ${project.externalCode}`:'' }}</option></select></label><label class="full"><span>项目别名 *</span><input v-model="aliasForm.aliases" required placeholder="多个别名使用中文逗号分隔"></label><label class="full"><span>备注</span><textarea v-model="aliasForm.note"></textarea></label></form><p class="modal-copy">项目别名只映射到已有项目，不会创建新项目；歧义名称仍需风险管理员确认。</p><template #footer><button type="button" @click="aliasOpen=false">取消</button><button class="admin-primary-button" type="button" @click="saveAlias">保存到草稿</button></template></ModalDialog>

    <ModalDialog v-if="publishOpen" eyebrow="PUBLISH SYSTEM CONFIGURATION" title="确认发布系统配置" @close="publishOpen=false"><p class="modal-copy">本次将发布 {{ changeCount }} 个模块的配置变更，并生成新版本。发布后看板筛选、AI风险提取和项目匹配将使用新规则。</p><label class="publish-summary"><span>变更摘要 *</span><textarea v-model="changeSummary" maxlength="500" placeholder="请说明本次调整内容"></textarea></label><dl class="prototype-detail-list"><div><dt>生效方式</dt><dd>保存后立即生效</dd></div><div><dt>历史数据</dt><dd>保留原类别与等级事件</dd></div><div><dt>任务影响</dt><dd>下一次同步任务读取新规则</dd></div><div><dt>审计记录</dt><dd>保存变更前后脱敏摘要</dd></div></dl><template #footer><button type="button" @click="publishOpen=false">取消</button><button class="admin-primary-button" type="button" :disabled="saving" @click="publish">{{ saving?'发布中…':'确认发布' }}</button></template></ModalDialog>

    <ModalDialog v-if="discardOpen" eyebrow="DISCARD DRAFT" title="放弃未发布更改" @close="discardOpen=false"><p class="modal-copy">当前所有草稿修改将恢复为已发布的 {{ overview?.version }} 配置。本操作不会影响线上已生效规则。</p><template #footer><button type="button" @click="discardOpen=false">取消</button><button class="admin-primary-button" type="button" @click="discardChanges">确认放弃</button></template></ModalDialog>

    <ModalDialog v-if="selectedRelease" eyebrow="CHANGE DETAIL" title="配置变更详情" @close="selectedRelease=null"><dl class="prototype-detail-list"><div><dt>版本</dt><dd>{{ selectedRelease.version }}</dd></div><div><dt>发布时间</dt><dd>{{ formatTime(selectedRelease.publishedAt) }}</dd></div><div><dt>发布人</dt><dd>{{ selectedRelease.publishedBy }}</dd></div><div><dt>配置模块</dt><dd>{{ selectedRelease.module }}</dd></div><div><dt>变更摘要</dt><dd>{{ selectedRelease.changeSummary }}</dd></div><div><dt>影响范围</dt><dd>{{ selectedRelease.impactScope.join('、') }}</dd></div><div><dt>审计 Trace ID</dt><dd>{{ selectedRelease.traceId }}</dd></div></dl></ModalDialog>
    <p v-if="toast" class="prototype-toast">{{ toast }}</p>
  </AdminShell>
</template>

<style scoped>
.loading-panel{padding:36px;text-align:center;color:#6d8798}.prototype-error{padding:13px 16px;border:1px solid #f0c5c8;border-radius:12px;color:#b9363f;background:#fff0f1}.config-search{width:100%;min-height:42px;margin-top:14px;padding:0 13px;border:1px solid #d3e2ec;border-radius:10px}.category-rule-table>div{grid-template-columns:22px minmax(220px,1fr) 100px 190px}.category-rule-table small,.admin-table td small{display:block;margin-top:4px;color:#8aa0ae}.row-actions{display:flex;gap:5px}.row-actions button{min-width:38px}.alias-chip{display:inline-flex;padding:6px 9px;border-radius:8px;color:#176fc8;background:#eaf5ff}.warning-keywords>span{color:#9a6506;background:#fff3dc}.history-filters{display:flex;gap:6px}.history-filters button{padding:7px 9px;border:1px solid #d7e4ec;border-radius:8px;color:#557287;background:#fff}.history-filters button.active{color:#fff;background:#1975da}.config-unsaved-bar{position:sticky;z-index:20;bottom:14px;display:flex;margin:16px auto 0;padding:14px 18px;border:1px solid #cfe0eb;border-radius:14px;align-items:center;gap:10px;background:#fff;box-shadow:0 14px 38px #173f5930}.config-unsaved-bar>span{display:grid;flex:1;gap:3px}.config-unsaved-bar small{color:#849aa8}.config-unsaved-bar button{min-height:40px;padding:0 14px;border:1px solid #d4e1e9;border-radius:9px}.publish-summary{display:grid;margin:16px 0;gap:7px;color:#45657a;font-weight:700}.publish-summary textarea{min-height:92px;padding:11px;border:1px solid #d3e2ec;border-radius:10px}.admin-table td button{margin-right:8px;border:0;color:#176fc8;background:transparent}@media(max-width:760px){.category-rule-table>div{grid-template-columns:18px minmax(150px,1fr) 88px}.category-rule-table .row-actions{grid-column:2/-1}.config-unsaved-bar{align-items:stretch;flex-wrap:wrap}.config-unsaved-bar>span{width:100%;flex-basis:100%}}
</style>
