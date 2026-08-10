<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";

import { ApiError } from "@/api/http";
import { useAuthStore } from "@/stores/auth";

const router = useRouter();
const auth = useAuthStore();
const currentPassword = ref("");
const newPassword = ref("");
const confirmPassword = ref("");
const submitting = ref(false);
const errorMessage = ref("");

async function submit(): Promise<void> {
  errorMessage.value = "";
  if (newPassword.value !== confirmPassword.value) {
    errorMessage.value = "两次输入的新密码不一致";
    return;
  }
  submitting.value = true;
  try {
    await auth.changePassword({
      currentPassword: currentPassword.value,
      newPassword: newPassword.value,
      confirmPassword: confirmPassword.value,
    });
    await router.replace({
      path: "/login",
      query: { passwordChanged: "1" },
    });
  } catch (error) {
    errorMessage.value =
      error instanceof ApiError ? error.message : "密码修改失败，请稍后重试";
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <main class="credential-page">
    <section class="credential-card">
      <p class="eyebrow">FIRST LOGIN SECURITY</p>
      <h1>设置新密码</h1>
      <p class="credential-summary">
        当前账号使用的是初始密码。完成修改后，所有已有会话将失效，
        请使用新密码重新登录。
      </p>

      <form class="credential-form" @submit.prevent="submit">
        <label>
          <span>当前密码</span>
          <input
            v-model="currentPassword"
            type="password"
            autocomplete="current-password"
            required
          />
        </label>
        <label>
          <span>新密码</span>
          <input
            v-model="newPassword"
            type="password"
            autocomplete="new-password"
            required
          />
        </label>
        <label>
          <span>确认新密码</span>
          <input
            v-model="confirmPassword"
            type="password"
            autocomplete="new-password"
            required
          />
        </label>
        <p class="password-hint">
          至少 12 位，需同时包含大写字母、小写字母、数字和特殊字符，
          且不能包含登录账号。
        </p>
        <p v-if="errorMessage" class="form-error" role="alert">
          {{ errorMessage }}
        </p>
        <button class="primary-button" :disabled="submitting" type="submit">
          {{ submitting ? "正在保存…" : "保存并重新登录" }}
        </button>
      </form>
    </section>
  </main>
</template>
