<script setup lang="ts">
import { ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useAuthStore } from "@/stores/auth";

defineEmits<{ agent: [] }>();
const auth = useAuthStore();
const route = useRoute();
const router = useRouter();
const menuOpen = ref(false);
const canMailbox = (permission: string) => auth.user?.permissions.includes(permission) ?? false;
async function logout(): Promise<void> { await auth.logout(); await router.replace("/login"); }
</script>
<template><header class="business-header"><RouterLink class="business-brand" to="/"><span class="brand-mark"><i></i><i></i><i></i></span><span><strong>项目风险管理</strong><small>PROJECT RISK INTELLIGENCE</small></span></RouterLink><nav><RouterLink :class="{active:route.path==='/' }" to="/">▣ Web 风险看板</RouterLink><button type="button" @click="$emit('agent')">◌ Agent 智能对话</button></nav><div class="business-tools"><span class="week-chip">2026年第27周</span><button class="notice-tool" type="button">♧<b>3</b></button><button class="profile-tool" type="button" @click="menuOpen=!menuOpen"><span>{{ auth.user?.displayName.slice(0,1) }}</span><i><strong>{{ auth.user?.displayName }}</strong><small>全项目数据</small></i>⌄</button><div v-if="menuOpen" class="business-profile-menu"><RouterLink v-if="canMailbox('mailbox.manage_self')" to="/mailbox-settings" @click="menuOpen=false">个人邮箱配置</RouterLink><RouterLink v-if="canMailbox('mailbox.sync_self')" to="/mail-sync-results" @click="menuOpen=false">邮箱同步结果</RouterLink><RouterLink to="/change-password" @click="menuOpen=false">修改密码</RouterLink><button type="button" @click="logout">退出登录</button></div></div></header></template>
