<script setup lang="ts">
import { useRouter } from "vue-router";

const router = useRouter();
</script>

<template>
  <main class="credential-page">
    <section class="credential-card forbidden-card">
      <p class="eyebrow">ACCESS DENIED</p>
      <h1>无权访问此页面</h1>
      <p class="credential-summary">
        当前账号未获得该功能权限。前端入口和后端接口均已执行权限校验。
      </p>
      <button class="primary-button" type="button" @click="router.replace('/')">
        返回首页
      </button>
    </section>
  </main>
</template>
