<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from "vue";
import type {
  MailboxConfigInput,
  MailboxConnectionTestResult,
  MailboxOverview,
  MailSyncBatchItem,
} from "@risk-platform/contracts";

import { mailboxApi } from "@/api/mailbox";
import { ApiError } from "@/api/http";
import BusinessHeader from "@/components/BusinessHeader.vue";
import ModalDialog from "@/components/ModalDialog.vue";

const loading = ref(true);
const saving = ref(false);
const testing = ref(false);
const syncing = ref(false);
const toggling = ref(false);
const dirty = ref(false);
const hydrating = ref(true);
const toast = ref("");
const errorMessage = ref("");
const keywordInput = ref("");
const authCodeVisible = ref(false);
const guideOpen = ref(false);
const disableOpen = ref(false);
const overview = ref<MailboxOverview | null>(null);
const connectionResult = ref<MailboxConnectionTestResult | null>(null);
const syncBatch = ref<MailSyncBatchItem | null>(null);
const originalSnapshot = ref("");
const lastTestFingerprint = ref("");

const form = reactive<MailboxConfigInput>({
  provider: "QQ",
  email: "",
  authCode: "",
  imapHost: "imap.qq.com",
  imapPort: 993,
  encryption: "SSL",
  folder: "INBOX",
  subjectKeywords: ["项目周报", "工作周报", "风险周报"],
  senderRule: "",
  initialSyncWeeks: 4,
  readAttachments: true,
  aiExtractionEnabled: true,
});

const configured = computed(() => Boolean(overview.value?.configured));
const enabled = computed(() => Boolean(overview.value?.enabled));
const statusLabel = computed(() => {
  if (!configured.value) return "尚未配置";
  if (!enabled.value) return "邮箱已停用";
  return overview.value?.connectionStatus === "HEALTHY"
    ? "连接正常"
    : overview.value?.connectionStatus === "FAILED"
      ? "连接异常"
      : "等待连接测试";
});
const currentConfigLabel = computed(() =>
  configured.value ? overview.value?.maskedEmail ?? "尚未配置" : "尚未配置",
);
const connectionClass = computed(() => ({
  "is-failed": overview.value?.connectionStatus === "FAILED",
  "is-muted": !configured.value || !enabled.value || overview.value?.connectionStatus === "UNTESTED",
}));
const syncStatusLabel = computed(() => {
  if (!overview.value?.lastSyncAt) return "尚未同步";
  return formatDateTime(overview.value.lastSyncAt);
});
const syncSummaryLabel = computed(() => {
  if (!overview.value?.lastSyncAt) return "保存并测试后可开始同步";
  return `新增${overview.value.lastSyncNewCount}封 · 提取${overview.value.lastSyncRiskCandidateCount}项风险线索`;
});
const scheduleLabel = computed(() =>
  enabled.value && overview.value?.autoSyncEnabled
    ? `已开启 · 每${overview.value.autoSyncIntervalMinutes}分钟`
    : "已停用",
);
const nextSyncLabel = computed(() =>
  overview.value?.nextSyncAt
    ? `下次执行：${formatDateTime(overview.value.nextSyncAt)}`
    : "自动同步已暂停",
);

watch(
  form,
  () => {
    if (!hydrating.value) {
      dirty.value = snapshot() !== originalSnapshot.value;
      connectionResult.value = null;
    }
  },
  { deep: true },
);

onMounted(load);

async function load(): Promise<void> {
  loading.value = true;
  errorMessage.value = "";
  try {
    applyOverview(await mailboxApi.overview());
  } catch (error) {
    handleError(error, "个人邮箱配置加载失败");
  } finally {
    loading.value = false;
  }
}

function applyOverview(value: MailboxOverview): void {
  overview.value = value;
  hydrating.value = true;
  Object.assign(form, {
    provider: value.provider,
    email: value.email,
    authCode: "",
    imapHost: value.imapHost,
    imapPort: value.imapPort,
    encryption: value.encryption,
    folder: value.folder,
    subjectKeywords: [...value.subjectKeywords],
    senderRule: value.senderRule ?? "",
    initialSyncWeeks: value.initialSyncWeeks,
    readAttachments: value.readAttachments,
    aiExtractionEnabled: value.aiExtractionEnabled,
  });
  void nextTick(() => {
    originalSnapshot.value = snapshot();
    dirty.value = false;
    hydrating.value = false;
  });
}

function selectProvider(provider: "QQ" | "IMAP"): void {
  form.provider = provider;
  if (provider === "QQ") {
    form.imapHost = "imap.qq.com";
    form.imapPort = 993;
    form.encryption = "SSL";
  } else if (form.imapHost === "imap.qq.com") {
    form.imapHost = "";
  }
}

function addKeyword(): void {
  const value = keywordInput.value.trim();
  if (!value) return;
  if (form.subjectKeywords.includes(value)) {
    notify("该关键词已存在");
    return;
  }
  if (form.subjectKeywords.length >= 8) {
    notify("最多可配置8个主题关键词");
    return;
  }
  form.subjectKeywords.push(value);
  keywordInput.value = "";
}

function removeKeyword(index: number): void {
  if (form.subjectKeywords.length <= 1) {
    notify("至少保留一个主题关键词");
    return;
  }
  form.subjectKeywords.splice(index, 1);
}

function validate(): boolean {
  if (!/^\S+@\S+\.\S+$/.test(form.email.trim())) return fail("请输入有效的邮箱地址");
  if (!form.imapHost.trim() || !form.imapHost.includes(".")) return fail("请输入有效的IMAP服务器地址");
  if (!Number.isInteger(Number(form.imapPort)) || form.imapPort < 1 || form.imapPort > 65535) return fail("端口范围应为1至65535");
  if (!form.subjectKeywords.length) return fail("请至少配置一个主题关键词");
  if (!overview.value?.hasAuthCode && !form.authCode?.trim()) return fail("首次配置邮箱时必须填写邮箱授权码");
  return true;
}

async function testConnection(): Promise<void> {
  if (!validate()) return;
  testing.value = true;
  errorMessage.value = "";
  connectionResult.value = null;
  try {
    const result = await mailboxApi.test(payload());
    connectionResult.value = result;
    if (result.success) {
      lastTestFingerprint.value = connectionFingerprint();
      notify("邮箱连接测试通过");
    } else {
      notify(result.errorSummary || "邮箱连接测试失败");
    }
  } catch (error) {
    handleError(error, "邮箱连接测试失败");
  } finally {
    testing.value = false;
  }
}

async function save(): Promise<void> {
  if (!validate()) return;
  saving.value = true;
  errorMessage.value = "";
  const shouldRetest = lastTestFingerprint.value === connectionFingerprint();
  try {
    let saved = await mailboxApi.save(payload());
    form.authCode = "";
    authCodeVisible.value = false;
    if (shouldRetest) {
      const retest = await mailboxApi.test({ ...payload(), authCode: undefined });
      connectionResult.value = retest;
      saved = await mailboxApi.overview();
    }
    applyOverview(saved);
    notify("个人邮箱配置已安全保存");
  } catch (error) {
    handleError(error, "个人邮箱配置保存失败");
  } finally {
    saving.value = false;
  }
}

function discard(): void {
  if (!dirty.value) {
    notify("当前没有未保存的修改");
    return;
  }
  if (overview.value) applyOverview(overview.value);
  connectionResult.value = null;
  notify("已撤销本次修改");
}

async function syncNow(): Promise<void> {
  if (dirty.value) return notify("请先保存当前配置后再同步");
  syncing.value = true;
  errorMessage.value = "";
  try {
    syncBatch.value = await mailboxApi.sync();
    notify("同步任务已进入队列，可在同步结果页查看进度");
  } catch (error) {
    handleError(error, "邮箱同步任务创建失败");
  } finally {
    syncing.value = false;
  }
}

async function setEnabled(next: boolean): Promise<void> {
  toggling.value = true;
  errorMessage.value = "";
  try {
    applyOverview(await mailboxApi.setEnabled(next));
    disableOpen.value = false;
    notify(next ? "邮箱已恢复，自动同步将继续运行" : "邮箱已停用，历史同步记录仍会保留");
  } catch (error) {
    handleError(error, next ? "恢复邮箱失败" : "停用邮箱失败");
  } finally {
    toggling.value = false;
  }
}

function payload(): MailboxConfigInput {
  return {
    provider: form.provider,
    email: form.email.trim(),
    authCode: form.authCode?.trim() || undefined,
    imapHost: form.imapHost.trim(),
    imapPort: Number(form.imapPort),
    encryption: form.encryption,
    folder: form.folder.trim(),
    subjectKeywords: form.subjectKeywords.map((item) => item.trim()).filter(Boolean),
    senderRule: form.senderRule?.trim() || undefined,
    initialSyncWeeks: form.initialSyncWeeks,
    readAttachments: form.readAttachments,
    aiExtractionEnabled: form.aiExtractionEnabled,
  };
}

function snapshot(): string {
  return JSON.stringify(payload());
}

function connectionFingerprint(): string {
  return JSON.stringify({
    provider: form.provider,
    email: form.email.trim().toLocaleLowerCase(),
    authCode: form.authCode?.trim() || "saved",
    imapHost: form.imapHost.trim().toLocaleLowerCase(),
    imapPort: Number(form.imapPort),
    encryption: form.encryption,
    folder: form.folder.trim(),
  });
}

function formatDateTime(value: string | null): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(new Date(value));
}

function fail(message: string): false {
  errorMessage.value = message;
  notify(message);
  return false;
}

function handleError(error: unknown, fallback: string): void {
  const message = error instanceof ApiError || error instanceof Error ? error.message : fallback;
  errorMessage.value = message;
  notify(message);
}

function notify(message: string): void {
  toast.value = message;
  window.setTimeout(() => {
    if (toast.value === message) toast.value = "";
  }, 2600);
}
</script>

<template>
  <div class="business-page mailbox-page">
    <BusinessHeader @agent="notify('Agent 智能对话可返回风险看板后使用')" />
    <main class="mailbox-main">
      <nav class="page-breadcrumb"><RouterLink to="/">Web 风险看板</RouterLink><span>›</span><strong>个人邮箱配置</strong></nav>

      <section class="mailbox-heading">
        <div class="heading-copy">
          <p>PERSONAL MAILBOX</p><h1>个人邮箱配置</h1>
          <span>连接本人用于接收项目周报的邮箱，为风险看板和 Agent 提供经过授权的周报数据。</span>
          <div class="role-boundary"><b>风</b><p><strong>仅风险管理员可配置本人邮箱</strong><small>系统管理员只能查看连接状态，不能代配邮箱或读取授权码。</small></p></div>
        </div>
        <div class="heading-actions"><RouterLink class="admin-outline-button" to="/mail-sync-results">查看同步结果</RouterLink><RouterLink class="admin-outline-button" to="/">返回风险看板</RouterLink></div>
      </section>

      <p v-if="errorMessage" class="mailbox-error" role="alert">{{ errorMessage }}</p>
      <section v-if="loading" class="mailbox-loading">正在读取本人邮箱配置…</section>
      <template v-else>
        <section class="mailbox-overview" aria-label="邮箱运行概览">
          <article class="overview-card" :class="connectionClass"><span class="overview-icon">✓</span><div><p>连接状态</p><strong><i></i>{{ statusLabel }}</strong><small>最近测试：{{ formatDateTime(overview?.lastTestAt ?? null) }}</small></div></article>
          <article class="overview-card"><span class="overview-icon sync-icon">↻</span><div><p>最近同步</p><strong>{{ syncStatusLabel }}</strong><small>{{ syncSummaryLabel }}</small></div></article>
          <article class="overview-card"><span class="overview-icon schedule-icon">◷</span><div><p>自动同步</p><strong>{{ scheduleLabel }}</strong><small>{{ nextSyncLabel }}</small></div></article>
          <article class="overview-card protection-card"><span class="overview-icon shield-icon">锁</span><div><p>凭据保护</p><strong>AES-256-GCM 加密</strong><small>授权码不回显、不进入日志</small></div></article>
        </section>

        <div class="mailbox-layout">
          <form class="mailbox-form-card" novalidate @submit.prevent="save">
            <header class="card-header"><div><p>MAILBOX AUTHORIZATION</p><h2>邮箱授权与连接</h2><span>当前配置：{{ currentConfigLabel }}</span></div><em :class="{dirty}"><i></i>{{ dirty ? '存在未保存修改' : configured ? '配置已保存' : '尚未保存配置' }}</em></header>

            <section class="form-section">
              <div class="section-title"><span>01</span><div><h2>选择邮箱类型</h2><p>QQ邮箱自动带入推荐参数，其他企业邮箱请选择通用 IMAP。</p></div></div>
              <div class="provider-options" role="radiogroup" aria-label="邮箱类型">
                <label :class="{selected:form.provider==='QQ'}"><input :checked="form.provider==='QQ'" type="radio" name="provider" value="QQ" @change="selectProvider('QQ')"><b>Q</b><span><strong>QQ 邮箱</strong><small>推荐 · 自动配置 IMAP 参数</small></span></label>
                <label :class="{selected:form.provider==='IMAP'}"><input :checked="form.provider==='IMAP'" type="radio" name="provider" value="IMAP" @change="selectProvider('IMAP')"><b>IM</b><span><strong>通用 IMAP</strong><small>企业邮箱及其他支持 IMAP 的邮箱</small></span></label>
              </div>
            </section>

            <section class="form-section">
              <div class="section-title"><span>02</span><div><h2>账号与服务器</h2><p>平台使用邮箱授权码建立安全连接，不保存邮箱登录密码。</p></div></div>
              <div class="prototype-form mailbox-grid">
                <label><span>邮箱地址 <em>*</em></span><input v-model="form.email" type="email" autocomplete="email" placeholder="请输入用于接收周报的邮箱"></label>
                <label><span>邮箱授权码 <em>*</em></span><span class="password-shell"><input v-model="form.authCode" :type="authCodeVisible?'text':'password'" autocomplete="new-password" :placeholder="overview?.hasAuthCode?'已保存，如需修改请重新输入':'请输入邮箱授权码'"><button type="button" :aria-label="authCodeVisible?'隐藏授权码':'显示授权码'" @click="authCodeVisible=!authCodeVisible">{{ authCodeVisible?'隐藏':'显示' }}</button></span><small>已保存的授权码不会在页面中回显</small></label>
                <label><span>IMAP 服务器 <em>*</em></span><input v-model="form.imapHost" :readonly="form.provider==='QQ'" placeholder="例如：imap.example.com"></label>
                <label><span>端口 <em>*</em></span><input v-model.number="form.imapPort" :readonly="form.provider==='QQ'" type="number" min="1" max="65535"></label>
                <label><span>加密方式 <em>*</em></span><select v-model="form.encryption" :disabled="form.provider==='QQ'"><option value="SSL">SSL / TLS</option><option value="STARTTLS">STARTTLS</option></select><small>建议使用 SSL / TLS 加密连接</small></label>
                <label><span>邮件文件夹 <em>*</em></span><select v-model="form.folder"><option value="INBOX">收件箱（INBOX）</option><option value="REPORTS">周报文件夹（REPORTS）</option><option value="OTHER">其他文件夹</option></select><small>只读取所选文件夹内符合规则的邮件</small></label>
              </div>
              <div v-if="form.provider==='QQ'" class="provider-guide"><span>i</span><p><strong>QQ邮箱需先开启 IMAP/SMTP 服务</strong><small>请在QQ邮箱设置中生成授权码。这里填写授权码，不要填写QQ密码。</small></p><button type="button" @click="guideOpen=true">查看开启说明</button></div>
              <div v-if="connectionResult" class="connection-result" :class="{failed:!connectionResult.success}" role="status"><span>{{ connectionResult.success?'✓':'!' }}</span><p><strong>{{ connectionResult.success?'连接测试通过':'连接测试失败' }}</strong><small>{{ connectionResult.success?'登录验证、文件夹访问和加密连接均正常。':connectionResult.errorSummary }}</small></p><time>{{ connectionResult.latencyMs }}ms</time></div>
            </section>

            <section class="form-section">
              <div class="section-title"><span>03</span><div><h2>周报识别规则</h2><p>系统仅同步符合关键词、发件人和时间范围的项目周报。</p></div></div>
              <div class="rule-block"><div><strong>主题关键词 <em>*</em></strong><small>邮件主题包含任一关键词时进入周报分析</small></div><div class="keyword-editor"><div class="keyword-list"><span v-for="(item,index) in form.subjectKeywords" :key="item" class="keyword-chip">{{ item }}<button type="button" :aria-label="`删除关键词“${item}”`" @click="removeKeyword(index)">×</button></span></div><label class="keyword-input"><input v-model="keywordInput" maxlength="20" placeholder="输入关键词后按回车" @keyup.enter.prevent="addKeyword"><button type="button" @click="addKeyword">添加</button></label></div></div>
              <div class="prototype-form mailbox-grid rule-grid"><label><span>首次同步范围</span><select v-model.number="form.initialSyncWeeks"><option :value="1">最近1周</option><option :value="4">最近4周</option><option :value="8">最近8周</option><option :value="12">最近12周</option></select><small>后续同步将使用UID游标，只读取新增邮件</small></label><label><span>发件人范围</span><input v-model="form.senderRule" placeholder="选填，例如：@example.com"><small>留空表示不限制发件人</small></label></div>
              <div class="switch-list"><label><span><strong>读取正文及常见附件</strong><small>支持 .txt、.docx、.pdf、.xlsx；不会执行宏、脚本或外部链接。</small></span><input v-model="form.readAttachments" type="checkbox"></label><label><span><strong>同步后进行 AI 风险提取</strong><small>识别显式及隐含风险，低置信结果进入风险管理员确认，不直接发布。</small></span><input v-model="form.aiExtractionEnabled" type="checkbox"></label></div>
            </section>
            <footer class="mail-form-actions"><button type="button" class="secondary-action" @click="discard">取消修改</button><button class="admin-outline-button" type="button" :disabled="testing" @click="testConnection">{{ testing?'正在测试…':'测试连接' }}</button><button class="admin-primary-button" type="submit" :disabled="saving">{{ saving?'正在保存…':'保存配置' }}</button></footer>
          </form>

          <aside class="mailbox-side">
            <section class="prototype-panel sync-control-card"><header><div><p>SYNC CONTROL</p><h2>同步控制</h2></div><span :class="enabled?'status-ok':'status-off'"><i></i>{{ enabled?'运行中':'已停用' }}</span></header><div class="sync-illustration"><span>✉</span><i></i><b>风</b></div><p class="sync-description">手动同步与定时同步使用同一套安全规则，每次只运行一个同步任务。</p><button class="admin-primary-button" type="button" :disabled="syncing||!enabled" @click="syncNow">{{ syncing?'正在创建任务…':'立即同步最新周报' }}</button><div v-if="syncBatch" class="queued-sync" role="status"><strong>同步任务已进入队列</strong><small>批次 {{ syncBatch.id.slice(0,8) }} · 可前往同步结果页查看进度</small><RouterLink to="/mail-sync-results">查看完整同步结果</RouterLink></div><button v-if="configured" class="danger-outline" type="button" :disabled="toggling" @click="enabled?disableOpen=true:setEnabled(true)">{{ enabled?'停用此邮箱':'恢复此邮箱' }}</button></section>
            <section class="prototype-panel sync-flow-card"><p>DATA FLOW</p><h2>数据如何进入看板</h2><ol class="sync-flow-list"><li><span><strong>读取授权周报</strong><small>仅处理符合识别规则的新邮件</small></span></li><li><span><strong>项目名称匹配</strong><small>与已导入的标准项目清单关联</small></span></li><li><span><strong>AI 提取风险线索</strong><small>生成类别、等级和处置建议</small></span></li><li><span><strong>风险管理员确认</strong><small>低置信或歧义结果需要人工确认</small></span></li><li><span><strong>更新风险看板</strong><small>确认后进入周报汇总和风险清单</small></span></li></ol></section>
            <section class="prototype-security-banner mailbox-security"><span>锁</span><div><strong>安全与权限说明</strong><ul><li>授权码加密保存，页面、接口和日志均不回显。</li><li>仅本人可以修改、测试、同步或停用此邮箱。</li><li>系统只读取授权文件夹内符合规则的项目周报。</li></ul></div></section>
          </aside>
        </div>
      </template>
    </main>

    <ModalDialog v-if="guideOpen" eyebrow="QQ MAIL GUIDE" title="开启QQ邮箱 IMAP 服务" @close="guideOpen=false"><ol class="guide-steps"><li><b>1</b><span><strong>进入QQ邮箱设置</strong><small>登录QQ邮箱网页版，打开“设置 → 账号”。</small></span></li><li><b>2</b><span><strong>开启 IMAP/SMTP 服务</strong><small>在账号安全区域开启服务，并按邮箱要求完成身份验证。</small></span></li><li><b>3</b><span><strong>生成并复制授权码</strong><small>授权码只显示一次，请复制后填写到本页，不要填写QQ密码。</small></span></li><li><b>4</b><span><strong>测试并保存</strong><small>点击“测试连接”，通过后再保存配置。</small></span></li></ol><div class="dialog-security-note"><strong>请妥善保管授权码</strong><small>平台不会在保存后再次显示完整授权码。</small></div></ModalDialog>
    <ModalDialog v-if="disableOpen" eyebrow="PAUSE MAILBOX" title="确认停用此邮箱？" @close="disableOpen=false"><p class="modal-copy">停用后系统将停止自动同步，风险看板不会再从此邮箱获取新周报。历史同步记录和已确认风险不会删除。</p><template #footer><button type="button" @click="disableOpen=false">取消</button><button class="danger-button" type="button" :disabled="toggling" @click="setEnabled(false)">{{ toggling?'正在停用…':'确认停用' }}</button></template></ModalDialog>
    <p v-if="toast" class="prototype-toast" role="status">{{ toast }}</p>
  </div>
</template>

<style scoped>
.mailbox-heading{display:flex;margin-bottom:22px;align-items:flex-end;justify-content:space-between;gap:20px}.heading-copy>p{margin:0 0 7px;color:#1475d2;font-size:12px;font-weight:800;letter-spacing:.14em}.heading-copy h1{margin:0;color:#173f59;font-size:34px}.heading-copy>span{display:block;margin-top:9px;color:#718a9c;line-height:1.7}.role-boundary{display:flex;width:fit-content;margin-top:14px;padding:10px 13px;border-radius:12px;align-items:center;gap:10px;background:#eaf5ff}.role-boundary>b{display:grid;width:34px;height:34px;border-radius:10px;place-items:center;color:#fff;background:#168ed2}.role-boundary p{display:grid;margin:0;gap:3px}.role-boundary small{color:#7892a4}.heading-actions{display:flex;gap:10px}.mailbox-error,.mailbox-loading{padding:14px 18px;border:1px solid #ffd0d0;border-radius:14px;color:#bd3038;background:#fff1f1;font-size:14px}.mailbox-loading{border-color:#cfe4ed;color:#517187;background:#fff}.mailbox-overview{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.overview-card{display:flex;min-height:112px;padding:18px;border:1px solid #d7e7ef;border-radius:18px;align-items:center;gap:13px;background:#fff;box-shadow:0 9px 25px rgba(38,85,113,.06)}.overview-icon{display:grid;width:45px;height:45px;flex:0 0 auto;border-radius:14px;place-items:center;color:#fff;background:#22aa83;font-size:20px}.sync-icon{background:#1d83dd}.schedule-icon{background:#7b6ce6}.shield-icon{background:#1996a0}.overview-card>div{display:grid;gap:4px}.overview-card p{margin:0;color:#8298a8;font-size:12px}.overview-card strong{color:#204b66;font-size:17px}.overview-card small{color:#8ca0ae}.overview-card.is-failed .overview-icon{background:#e85558}.overview-card.is-failed strong{color:#c33c42}.overview-card.is-muted .overview-icon{background:#9babb6}.mailbox-layout{grid-template-columns:minmax(0,1fr) 360px}.mailbox-form-card{border:1px solid #d9e7f0;border-radius:20px;background:#fff;overflow:hidden}.card-header{display:flex;padding:22px 24px;border-bottom:1px solid #e1ebf1;align-items:center;justify-content:space-between}.card-header>div{display:grid;gap:4px}.card-header p{margin:0;color:#1475d2;font-size:12px;font-weight:800;letter-spacing:.12em}.card-header h2{margin:0;font-size:22px}.card-header span{color:#8198a7}.card-header em{display:flex;align-items:center;gap:7px;color:#168c70;font-style:normal;font-weight:700}.card-header em.dirty{color:#c47a00}.card-header em i{width:8px;height:8px;border-radius:50%;background:currentColor}.provider-options{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.provider-options label{position:relative;display:flex;padding:16px;border:1px solid #d6e4ed;border-radius:14px;align-items:center;gap:11px;cursor:pointer}.provider-options label.selected{border-color:#1a7ce1;background:#eef7ff;box-shadow:0 0 0 3px rgba(26,124,225,.08)}.provider-options input{position:absolute;opacity:0}.provider-options b{display:grid;width:39px;height:39px;border-radius:11px;place-items:center;color:#fff;background:#1a8dd1}.provider-options label>span{display:grid;gap:4px}.provider-options small{color:#8ba0ae}.mailbox-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.password-shell{display:flex}.password-shell input{min-width:0;flex:1;border-radius:10px 0 0 10px}.password-shell button{padding:0 13px;border:1px solid #cfdee8;border-left:0;border-radius:0 10px 10px 0;color:#176fc9;background:#f7fbfe}.connection-result{display:flex;margin-top:14px;padding:13px;border-left:4px solid #22a86e;border-radius:10px;align-items:center;gap:10px;background:#eaf8f0}.connection-result>span{font-size:20px}.connection-result p{display:grid;flex:1;margin:0;gap:3px}.connection-result.failed{border-color:#e95358;background:#fff0f0}.rule-grid{margin-top:16px}.switch-list{display:grid;margin-top:16px;border:1px solid #dce8ef;border-radius:13px}.switch-list label{display:flex;padding:14px 16px;align-items:center;justify-content:space-between;gap:15px}.switch-list label+label{border-top:1px solid #e1ebf1}.switch-list label>span{display:grid;gap:4px}.switch-list small{color:#8298a7}.switch-list input{width:20px;height:20px}.secondary-action{margin-right:auto;border:0;color:#698497;background:transparent}.mailbox-side{display:grid;align-content:start;gap:16px}.sync-control-card>header{display:flex;align-items:start;justify-content:space-between}.sync-control-card header>div{display:grid;gap:4px}.sync-control-card header p{margin:0;color:#1475d2;font-size:12px;font-weight:800;letter-spacing:.12em}.status-off{color:#9a6470}.sync-illustration{display:flex;margin:18px 0;padding:16px;border-radius:14px;align-items:center;justify-content:center;gap:10px;background:#edf7fb}.sync-illustration span,.sync-illustration b{display:grid;width:42px;height:42px;border-radius:13px;place-items:center;color:#fff;background:#1785d8}.sync-illustration b{background:#19a796}.sync-illustration i{width:68px;border-top:3px dotted #6db9d8}.sync-description{color:#70899a;line-height:1.65}.queued-sync{display:grid;margin-top:12px;padding:12px;border-radius:11px;gap:4px;background:#ebf8f2}.queued-sync small{color:#718a9b}.queued-sync a{margin-top:5px;color:#1772ce}.mailbox-security{margin-top:0!important}.mailbox-security ul{display:grid;margin:8px 0 0;padding-left:18px;gap:7px}.guide-steps{display:grid;padding:0;list-style:none;gap:12px}.guide-steps li{display:flex;padding:12px;border-radius:12px;gap:10px;background:#f4f8fb}.guide-steps b{display:grid;width:30px;height:30px;border-radius:9px;place-items:center;color:#1771ca;background:#e1f1ff}.guide-steps span{display:grid;gap:4px}.dialog-security-note{display:grid;margin-top:14px;padding:13px;border-radius:11px;gap:4px;background:#fff5dd}.dialog-security-note small{color:#8a7553}
.mailbox-side > .prototype-panel{min-width:0;padding:20px}.sync-control-card>header{gap:12px}.sync-control-card>header>div{min-width:0}.sync-control-card>header>span{flex:0 0 auto;white-space:nowrap}.sync-control-card .admin-primary-button,.sync-control-card .danger-outline{width:100%;margin-top:12px}.keyword-editor{min-width:0}.keyword-list{display:flex;min-width:0;flex-wrap:wrap;gap:8px}.keyword-chip{display:inline-flex;max-width:100%;padding:5px 7px 5px 10px;border-radius:999px;align-items:center;gap:5px;color:#176fc8;background:#eaf5ff;font-weight:700;line-height:1.35}.keyword-chip button{display:grid;width:22px;height:22px;padding:0;border:0;border-radius:50%;place-items:center;color:inherit;background:transparent;font-size:17px;line-height:1;cursor:pointer}.keyword-chip button:hover{background:#cfe8fb}.keyword-chip button:focus-visible{outline:2px solid #176fc8;outline-offset:2px}.keyword-input{display:flex;min-width:0;flex:1 1 210px}.keyword-input input{min-width:0;flex:1}.keyword-input button{flex:0 0 auto}.sync-flow-card>p,.sync-flow-card>h2{margin-left:0}.sync-flow-list{display:grid;margin:18px 0 0;padding:0;list-style:none;counter-reset:flow-step;gap:12px}.sync-flow-list li{display:flex;min-width:0;align-items:flex-start;gap:10px;counter-increment:flow-step}.sync-flow-list li::before{display:grid;width:28px;height:28px;flex:0 0 auto;border-radius:9px;place-items:center;color:#1771ca;background:#e8f4ff;content:counter(flow-step)}.sync-flow-list li>span{display:grid;min-width:0;gap:4px}.sync-flow-list strong{line-height:1.45}.sync-flow-list small{color:#7892a4;line-height:1.55}
@media(max-width:1050px){.mailbox-overview{grid-template-columns:repeat(2,minmax(0,1fr))}.mailbox-layout{grid-template-columns:1fr}.mailbox-side{grid-template-columns:repeat(2,minmax(0,1fr))}.mailbox-security{grid-column:1/-1}}
@media(max-width:720px){.mailbox-heading{align-items:stretch;flex-direction:column}.heading-actions{display:grid;grid-template-columns:1fr 1fr}.heading-copy h1{font-size:29px}.mailbox-overview{grid-template-columns:1fr}.mailbox-grid,.provider-options{grid-template-columns:1fr}.mailbox-side{grid-template-columns:1fr}.mailbox-security{grid-column:auto}.rule-block{grid-template-columns:1fr}.card-header{align-items:flex-start;gap:12px;flex-direction:column}.mail-form-actions{display:grid;grid-template-columns:1fr 1fr}.secondary-action{grid-column:1/-1;margin:0}.heading-actions a{justify-content:center;text-align:center}}
</style>
