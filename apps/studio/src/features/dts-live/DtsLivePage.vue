<script setup lang="ts">
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  Clock3,
  ExternalLink,
  LoaderCircle,
  Play,
  RefreshCw,
  RotateCcw,
  Square,
} from "@lucide/vue";
import { computed, onMounted, onUnmounted } from "vue";
import { RouterLink, useRouter } from "vue-router";

import type { DtsLiveServiceState } from "../../api";
import { useDtsLiveService } from "./useDtsLiveService";

const live = useDtsLiveService();
const router = useRouter();

const labels: Record<DtsLiveServiceState, string> = {
  stopped: "未启动",
  starting: "正在启动",
  waiting_for_explorer: "等待 Explorer",
  connected: "已连接",
  failed: "启动失败",
};

const stateLabel = computed(() =>
  live.status.value ? labels[live.status.value.state] : "读取中",
);
const stateTone = computed(() => {
  if (live.status.value?.state === "connected") return "success";
  if (live.status.value?.state === "failed") return "danger";
  if (live.running.value) return "warning";
  return "neutral";
});

onMounted(() => void live.initialize());
onUnmounted(live.dispose);

const displayTime = (value: string) =>
  new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
</script>

<template>
  <section class="dts-live-page">
    <header class="workspace-header dts-live-header">
      <button
        type="button"
        title="返回 DTS 工作台"
        aria-label="返回 DTS 工作台"
        @click="router.push('/dts')"
      >
        <ArrowLeft :size="18" />
      </button>
      <div>
        <span class="page-header__eyebrow">DTS 工作台</span>
        <h1>实时种植</h1>
        <p>启动短时 Explorer 会话，在当前场景完成单区域选取、种植与回写。</p>
      </div>
      <button
        class="icon-button"
        type="button"
        title="刷新状态"
        :disabled="live.action.value !== null"
        @click="live.refresh()"
      >
        <RefreshCw :size="17" :class="{ spinning: live.action.value === 'refresh' }" />
      </button>
    </header>

    <div v-if="live.errorMessage.value" class="inline-alert dts-live-alert" role="alert">
      <AlertCircle :size="18" />
      <div>
        <strong>实时种植未就绪</strong>
        <p>{{ live.errorMessage.value }}</p>
      </div>
    </div>

    <main class="dts-live-workspace">
      <section class="dts-live-control" aria-label="实时种植控制">
        <header class="dts-live-panel-header">
          <div>
            <span>服务控制</span>
            <h2>DTS Explorer 会话</h2>
          </div>
          <span class="dts-live-state" :data-tone="stateTone">
            <i />{{ stateLabel }}
          </span>
        </header>

        <div class="dts-live-control-body">
          <div class="dts-live-readiness">
            <CheckCircle2 v-if="live.managementAvailable.value" :size="18" />
            <AlertCircle v-else :size="18" />
            <div>
              <strong>{{ live.managementAvailable.value ? '桌面控制可用' : '缺少管理权限' }}</strong>
              <span>
                {{ live.managementAvailable.value
                  ? '服务只会在点击启动后创建，不随 Studio 启动。'
                  : '请使用 VerdFlow 桌面开发启动器。' }}
              </span>
            </div>
          </div>

          <dl class="dts-live-details">
            <div>
              <dt>Connector</dt>
              <dd>{{ live.status.value?.connector_base_url || '尚未创建' }}</dd>
            </div>
            <div>
              <dt>临时会话</dt>
              <dd>{{ live.status.value?.connector_session_id || '无' }}</dd>
            </div>
            <div>
              <dt>Explorer</dt>
              <dd>{{ live.status.value?.webui_connected ? 'WebUI 已连接' : '未连接' }}</dd>
            </div>
          </dl>

          <div v-if="!live.managementAvailable.value" class="dts-live-settings-link">
            <RouterLink to="/settings"><ExternalLink :size="15" />检查外部运行环境</RouterLink>
          </div>
        </div>

        <footer class="dts-live-actions">
          <button
            v-if="!live.running.value"
            class="primary-button"
            type="button"
            :disabled="!live.managementAvailable.value || live.action.value !== null"
            @click="live.start"
          >
            <LoaderCircle v-if="live.action.value === 'start'" :size="17" class="spinning" />
            <Play v-else :size="17" fill="currentColor" />
            {{ live.action.value === 'start' ? '正在启动' : '启动实时种植' }}
          </button>
          <template v-else>
            <button
              class="secondary-button"
              type="button"
              :disabled="live.action.value !== null"
              @click="live.relaunch"
            >
              <RotateCcw :size="16" />重建会话
            </button>
            <button
              class="secondary-button dts-live-stop"
              type="button"
              :disabled="live.action.value !== null"
              @click="live.stop"
            >
              <Square :size="15" fill="currentColor" />停止服务
            </button>
          </template>
        </footer>
      </section>

      <section class="dts-live-log" aria-label="实时种植日志">
        <header class="dts-live-panel-header">
          <div>
            <span>运行监控</span>
            <h2>实时事件</h2>
          </div>
          <small>{{ live.status.value?.events.length || 0 }} 条</small>
        </header>

        <div v-if="!live.status.value?.events.length" class="dts-live-empty">
          <Clock3 :size="22" />
          <strong>尚无运行事件</strong>
          <span>启动实时种植后，这里会显示连接和会话状态。</span>
        </div>
        <ol v-else class="dts-live-event-list">
          <li
            v-for="event in [...live.status.value.events].reverse()"
            :key="event.sequence"
            :data-level="event.level"
          >
            <span class="dts-live-event-marker" />
            <div>
              <strong>{{ event.message }}</strong>
              <small>{{ displayTime(event.occurred_at) }} · {{ event.code }}</small>
            </div>
          </li>
        </ol>
      </section>
    </main>
  </section>
</template>
