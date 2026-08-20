<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import { ApiError } from "@/api/http";
import { useAuthStore } from "@/stores/auth";

const REMEMBERED_ACCOUNT_KEY = "project-risk-remembered-account";
const route = useRoute();
const router = useRouter();
const auth = useAuthStore();

const username = ref("");
const password = ref("");
const remember = ref(false);
const showPassword = ref(false);
const submitting = ref(false);
const errorMessage = ref("");
const forgotOpen = ref(false);
const wechatErrorMessages: Record<string, string> = {
  WECHAT_TOKEN_INVALID: "微信登录凭证无效，请从小程序重新进入",
  WECHAT_USER_NOT_BOUND: "微信手机号尚未绑定系统账号，请联系管理员",
  WECHAT_USER_INFO_UNAVAILABLE: "微信用户信息服务暂不可用，请稍后重试",
  ACCOUNT_DISABLED: "账号已停用，请联系管理员",
  ACCOUNT_LOCKED: "账号已锁定，请稍后重试",
};

const redirectPath = computed(() => {
  const redirect = route.query.redirect;
  return typeof redirect === "string" && redirect.startsWith("/")
    ? redirect
    : "/";
});

onMounted(() => {
  const code = route.query.wechatError;
  if (typeof code === "string") errorMessage.value = wechatErrorMessages[code] ?? "微信登录失败，请稍后重试";
  const remembered = window.localStorage.getItem(REMEMBERED_ACCOUNT_KEY);
  if (remembered) {
    username.value = remembered;
    remember.value = true;
  }
});

async function submitLogin(): Promise<void> {
  errorMessage.value = "";
  if (!username.value.trim() || !password.value) {
    errorMessage.value = "请输入账号和密码";
    return;
  }

  submitting.value = true;
  try {
    const user = await auth.login({
      username: username.value.trim(),
      password: password.value,
    });
    if (remember.value) {
      window.localStorage.setItem(
        REMEMBERED_ACCOUNT_KEY,
        username.value.trim(),
      );
    } else {
      window.localStorage.removeItem(REMEMBERED_ACCOUNT_KEY);
    }
    await router.replace(
      user.mustChangePassword ? "/change-password" : redirectPath.value,
    );
  } catch (error) {
    errorMessage.value =
      error instanceof ApiError ? error.message : "登录失败，请稍后重试";
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <main class="login-page">
    <div class="ambient ambient-top" aria-hidden="true"></div>
    <div class="ambient ambient-bottom" aria-hidden="true"></div>

    <div class="login-layout">
      <section class="login-intro" aria-labelledby="login-page-title">
        <p class="product-kicker">
          <span class="kicker-dot" aria-hidden="true"></span>
          PROJECT RISK INTELLIGENCE
        </p>
        <h1 id="login-page-title">
          <span class="gradient-title">AI驱动的</span>
          <span>项目风险协同平台</span>
        </h1>
        <p class="intro-copy">
          汇聚项目、回款与周报线索，让风险识别更早一步，
          让每一次协同都有依据。
        </p>
        <ul class="capability-list" aria-label="平台核心能力">
          <li>风险看板</li>
          <li>智能对话</li>
          <li>周报洞察</li>
        </ul>
      </section>

      <section class="login-panel" aria-label="账号登录">
        <div class="login-card">
          <div class="card-accent" aria-hidden="true"></div>
          <header class="login-card-header">
            <p class="login-kicker">WELCOME BACK</p>
            <h2>登录平台</h2>
            <p>请输入系统管理员分配的账号信息</p>
          </header>

          <form class="login-form" novalidate @submit.prevent="submitLogin">
            <label class="form-field">
              <span>账号</span>
              <span class="input-shell">
                <span class="field-icon" aria-hidden="true">人</span>
                <input
                  v-model="username"
                  name="username"
                  type="text"
                  autocomplete="username"
                  maxlength="64"
                  placeholder="请输入账号"
                />
              </span>
            </label>

            <label class="form-field">
              <span>密码</span>
              <span class="input-shell">
                <span class="field-icon" aria-hidden="true">锁</span>
                <input
                  v-model="password"
                  name="password"
                  :type="showPassword ? 'text' : 'password'"
                  autocomplete="current-password"
                  maxlength="255"
                  placeholder="请输入密码"
                />
                <button
                  class="password-toggle"
                  type="button"
                  :aria-label="showPassword ? '隐藏密码' : '显示密码'"
                  @click="showPassword = !showPassword"
                >
                  {{ showPassword ? "隐藏" : "显示" }}
                </button>
              </span>
            </label>

            <div class="form-options">
              <label class="remember-option">
                <input v-model="remember" type="checkbox" />
                <span>记住账号</span>
              </label>
              <button
                class="text-button"
                type="button"
                @click="forgotOpen = true"
              >
                忘记密码
              </button>
            </div>

            <p v-if="errorMessage" class="form-error" role="alert">
              {{ errorMessage }}
            </p>

            <button
              class="primary-button"
              type="submit"
              :disabled="submitting"
            >
              {{ submitting ? "正在登录…" : "登 录" }}
            </button>

            <div class="security-note">
              <span aria-hidden="true">i</span>
              <p>为保障账号安全，首次登录后需修改初始密码。</p>
            </div>
          </form>
          <p class="login-footer">项目风险管理平台 · 安全访问</p>
        </div>
      </section>
    </div>

    <div
      v-if="forgotOpen"
      class="dialog-backdrop"
      role="presentation"
      @click.self="forgotOpen = false"
    >
      <section class="message-dialog" role="dialog" aria-modal="true">
        <span class="dialog-icon" aria-hidden="true">?</span>
        <h2>忘记密码</h2>
        <p>
          当前版本由系统管理员统一管理账号。请联系系统管理员重置密码，
          重置后首次登录需要重新设置新密码。
        </p>
        <button
          class="secondary-button"
          type="button"
          @click="forgotOpen = false"
        >
          我知道了
        </button>
      </section>
    </div>
  </main>
</template>
