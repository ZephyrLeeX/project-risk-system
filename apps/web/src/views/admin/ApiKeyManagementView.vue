<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";

import { aiProviderApi, type ModelConfig, type ProviderAccount } from "@/api/ai-providers";
import AdminShell from "@/components/AdminShell.vue";

const accounts = ref<ProviderAccount[]>([]);
const models = ref<Record<string, ModelConfig[]>>({});
const loading = ref(true);
const saving = ref(false);
const notice = ref("");
const accountForm = reactive({ name: "", apiKey: "" });
const modelForm = reactive({ accountId: "", modelName: "", priority: 100, timeoutSeconds: 60, isDefault: false });

function message(error: unknown): string {
  return error instanceof Error ? error.message : "操作失败，请稍后重试";
}

async function load(): Promise<void> {
  loading.value = true;
  try {
    accounts.value = await aiProviderApi.accounts();
    await Promise.all(accounts.value.map(async (account) => { models.value[account.id] = await aiProviderApi.models(account.id); }));
  } catch (error) { notice.value = message(error); } finally { loading.value = false; }
}

async function createAccount(): Promise<void> {
  if (accountForm.name.trim().length < 2 || accountForm.apiKey.trim().length < 8) { notice.value = "请填写名称和有效的 DeepSeek API Key"; return; }
  saving.value = true;
  try { await aiProviderApi.createAccount({ name: accountForm.name.trim(), providerType: "DEEPSEEK_OFFICIAL", apiKey: accountForm.apiKey.trim(), enabled: true }); accountForm.name = ""; accountForm.apiKey = ""; notice.value = "Provider Account 已创建"; await load(); }
  catch (error) { notice.value = message(error); } finally { saving.value = false; }
}

async function rotate(account: ProviderAccount): Promise<void> {
  const apiKey = window.prompt("输入新的 DeepSeek API Key（仅写入，不会回显）", "");
  if (!apiKey) return;
  try { await aiProviderApi.rotateKey(account.id, { apiKey }); notice.value = "Key rotation 已完成"; await load(); } catch (error) { notice.value = message(error); }
}

async function toggleAccount(account: ProviderAccount): Promise<void> {
  try { await aiProviderApi.setAccountStatus(account.id, !account.enabled); await load(); } catch (error) { notice.value = message(error); }
}

async function testAccount(account: ProviderAccount): Promise<void> {
  try { const result = await aiProviderApi.testAccount(account.id); notice.value = result.success ? "Provider health: AVAILABLE" : `Provider health 检查失败：${result.errorClassification ?? "UNKNOWN"}`; await load(); } catch (error) { notice.value = message(error); }
}

async function discover(account: ProviderAccount): Promise<void> {
  try { const found = await aiProviderApi.discoverModels(account.id); notice.value = found.length ? `已发现 ${found.length} 个模型，可复制模型 ID 添加配置` : "Provider 未返回可用模型"; } catch (error) { notice.value = message(error); }
}

async function addModel(): Promise<void> {
  if (!modelForm.accountId || !modelForm.modelName.trim()) { notice.value = "请选择 Account 并填写模型 ID"; return; }
  try { await aiProviderApi.createModel(modelForm.accountId, { modelName: modelForm.modelName.trim(), enabled: true, isDefault: modelForm.isDefault, priority: Number(modelForm.priority), timeoutSeconds: Number(modelForm.timeoutSeconds) }); modelForm.modelName = ""; notice.value = "Model Config 已保存"; await load(); } catch (error) { notice.value = message(error); }
}

async function editModel(accountId: string, model: ModelConfig): Promise<void> {
  const priority = window.prompt("Priority", String(model.priority));
  const timeoutSeconds = window.prompt("Timeout seconds", String(model.timeoutSeconds));
  if (priority === null || timeoutSeconds === null) return;
  const parsedPriority = Number(priority);
  const parsedTimeout = Number(timeoutSeconds);
  if (!Number.isInteger(parsedPriority) || parsedPriority < 0 || !Number.isInteger(parsedTimeout) || parsedTimeout < 1) {
    notice.value = "Priority 必须为非负整数，Timeout 必须为正整数";
    return;
  }
  try {
    await aiProviderApi.updateModel(accountId, model.id, {
      modelName: model.modelName,
      enabled: model.enabled,
      isDefault: model.isDefault,
      priority: parsedPriority,
      timeoutSeconds: parsedTimeout,
    });
    notice.value = "Model Config 已更新";
    await load();
  } catch (error) { notice.value = message(error); }
}

async function toggleModel(accountId: string, model: ModelConfig): Promise<void> {
  try { await aiProviderApi.setModelStatus(accountId, model.id, !model.enabled); await load(); } catch (error) { notice.value = message(error); }
}

async function setDefault(accountId: string, model: ModelConfig): Promise<void> {
  try { await aiProviderApi.setDefaultModel(accountId, model.id); await load(); } catch (error) { notice.value = message(error); }
}

async function testModel(accountId: string, model: ModelConfig): Promise<void> {
  try { const result = await aiProviderApi.testModel(accountId, model.id); notice.value = result.success ? "Model health: AVAILABLE" : `Model health 检查失败：${result.errorClassification ?? "UNKNOWN"}`; await load(); } catch (error) { notice.value = message(error); }
}

onMounted(() => void load());
</script>

<template>
  <AdminShell>
    <section class="admin-page-heading prototype-heading"><div><p class="admin-eyebrow">AI PROVIDER V2</p><h1>Provider Account &amp; Model Config</h1><p>仅支持 DeepSeek Official；官方 endpoint 固定为服务端安全边界，页面不可编辑。</p></div><button class="admin-outline-button" type="button" @click="load">刷新</button></section>
    <p v-if="notice" class="prototype-toast" role="status">{{ notice }}</p>
    <section class="prototype-security-banner"><span>锁</span><div><strong>DeepSeek Official only</strong><p>官方连接地址由服务端固定，密钥仅在提交时使用并以掩码状态展示。</p></div></section>
    <section class="prototype-panel"><header class="prototype-panel-heading"><div><p>PROVIDER ACCOUNT</p><h2>新增 Provider Account</h2></div></header><form class="prototype-form" @submit.prevent="createAccount"><label><span>Account 名称</span><input v-model="accountForm.name" required minlength="2" autocomplete="off"></label><label><span>DeepSeek API Key</span><input v-model="accountForm.apiKey" required minlength="8" type="password" autocomplete="new-password"></label><div><button class="admin-primary-button" :disabled="saving" type="submit">保存 Account</button></div></form></section>
    <section class="prototype-panel"><header class="prototype-panel-heading"><div><p>ACCOUNTS</p><h2>Provider Accounts <small>{{ accounts.length }} 个</small></h2></div></header><p v-if="loading">正在加载…</p><article v-for="account in accounts" :key="account.id" class="provider-card"><header><span class="provider-avatar">DS</span><span><strong>{{ account.name }}</strong><small>DEEPSEEK_OFFICIAL · endpoint fixed by backend</small></span><i :class="{off: !account.enabled}">{{ account.enabled ? '已启用' : '已停用' }}</i></header><dl><div><dt>Key</dt><dd><code>{{ account.maskedKey }}</code></dd></div><div><dt>Account health</dt><dd>{{ account.health }}<small v-if="account.lastHealthErrorCode"> · {{ account.lastHealthErrorCode }}</small></dd></div><div><dt>Models</dt><dd>{{ account.modelCount }}</dd></div></dl><footer><button type="button" @click="testAccount(account)">Health test</button><button type="button" @click="discover(account)">Discover models</button><button type="button" @click="rotate(account)">Key rotation</button><button type="button" @click="toggleAccount(account)">{{ account.enabled ? '停用' : '启用' }}</button></footer><div class="prototype-panel"><header class="prototype-panel-heading"><div><p>MODEL CONFIG</p><h3>模型配置</h3></div></header><form class="prototype-form" @submit.prevent="modelForm.accountId = account.id; void addModel()"><label><span>Model ID</span><input v-model="modelForm.modelName" required autocomplete="off" placeholder="DeepSeek model ID"></label><label><span>Priority</span><input v-model.number="modelForm.priority" type="number" min="0"></label><label><span>Timeout (seconds)</span><input v-model.number="modelForm.timeoutSeconds" type="number" min="1" max="300"></label><label class="switch-row"><span>Default model</span><input v-model="modelForm.isDefault" type="checkbox"></label><button class="admin-primary-button" type="submit">添加 Model Config</button></form><p v-if="!models[account.id]?.length">暂无模型配置。</p><div v-for="model in models[account.id]" :key="model.id" class="provider-card"><header><span><strong>{{ model.modelName }}</strong><small>priority {{ model.priority }} · timeout {{ model.timeoutSeconds }}s</small></span><em v-if="model.isDefault">默认</em><i :class="{off: !model.enabled}">{{ model.health }}</i></header><footer><button type="button" @click="editModel(account.id, model)">编辑 priority/timeout</button><button type="button" @click="testModel(account.id, model)">Health test</button><button type="button" :disabled="model.isDefault" @click="setDefault(account.id, model)">{{ model.isDefault ? '当前默认' : '设为默认' }}</button><button type="button" @click="toggleModel(account.id, model)">{{ model.enabled ? '停用' : '启用' }}</button></footer></div></div></article><p v-if="!loading && !accounts.length" class="prototype-empty">尚未配置 DeepSeek Official Account。</p></section>
  </AdminShell>
</template>
