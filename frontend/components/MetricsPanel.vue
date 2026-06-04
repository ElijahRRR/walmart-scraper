<script setup lang="ts">
/**
 * MetricsPanel.vue — 指标面板
 * 自动轮询 /metrics，以卡片形式展示采集系统运行指标。
 */

// ─── 接口定义 ───────────────────────────────────────────────
interface MetricsData {
  total_requests: number
  total_success: number
  total_429: number
  total_blocked: number
  total_products: number
  total_ip_used: number
  success_rate: number | null
  rate_429: number | null
  blocked_rate: number | null
  avg_yield_per_ip: number | null
}

// ─── Composable ─────────────────────────────────────────────
const api = useApi()

// ─── 响应式状态 ──────────────────────────────────────────────
const metrics = ref<MetricsData | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)
// 自动刷新间隔，单位秒（用户可调）
const refreshInterval = ref(15)
let timerId: ReturnType<typeof setInterval> | null = null

// ─── 工具函数 ────────────────────────────────────────────────
/** 将数字格式化为千分符字符串 */
function fmt(n: number): string {
  return n.toLocaleString('zh-CN')
}

/** 将比率（0~1 的小数）格式化为百分比字符串；null 返回 — */
function fmtRate(r: number | null): string {
  if (r === null || r === undefined) return '—'
  return (r * 100).toFixed(1) + '%'
}

/** 格式化每 IP 产出；null 返回 — */
function fmtYield(y: number | null): string {
  if (y === null || y === undefined) return '—'
  return y.toFixed(1)
}

// ─── 数据拉取 ────────────────────────────────────────────────
async function fetchMetrics() {
  loading.value = true
  error.value = null
  try {
    metrics.value = await api.get<MetricsData>('/metrics')
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : '获取指标失败'
  } finally {
    loading.value = false
  }
}

// ─── 自动刷新控制 ────────────────────────────────────────────
function startTimer() {
  stopTimer()
  timerId = setInterval(fetchMetrics, refreshInterval.value * 1000)
}

function stopTimer() {
  if (timerId !== null) {
    clearInterval(timerId)
    timerId = null
  }
}

function onIntervalChange() {
  startTimer()
}

// ─── 生命周期 ────────────────────────────────────────────────
onMounted(() => {
  fetchMetrics()
  startTimer()
})

onUnmounted(() => {
  stopTimer()
})

// ─── 指标卡片定义（便于扩展） ─────────────────────────────────
interface MetricCard {
  label: string
  getValue: () => string
  getSub: () => string | null
  icon: string
  iconBg: string
  valueTone: string
}

const cards = computed<MetricCard[]>(() => {
  const m = metrics.value

  return [
    {
      label: '总请求',
      getValue: () => m ? fmt(m.total_requests) : '—',
      getSub: () => null,
      icon: 'M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2',
      iconBg: 'bg-blue-50 text-blue-500',
      valueTone: 'text-walmart-600',
    },
    {
      label: '成功请求',
      getValue: () => m ? fmt(m.total_success) : '—',
      getSub: () => m ? `成功率 ${fmtRate(m.success_rate)}` : null,
      icon: 'M5 13l4 4L19 7',
      iconBg: 'bg-green-50 text-green-500',
      valueTone: 'text-green-600',
    },
    {
      label: '限流(429)',
      getValue: () => m ? fmt(m.total_429) : '—',
      getSub: () => m ? `占比 ${fmtRate(m.rate_429)}` : null,
      icon: 'M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z',
      iconBg: 'bg-yellow-50 text-yellow-500',
      valueTone: 'text-yellow-600',
    },
    {
      label: '封控次数',
      getValue: () => m ? fmt(m.total_blocked) : '—',
      getSub: () => m ? `封控率 ${fmtRate(m.blocked_rate)}` : null,
      icon: 'M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636',
      iconBg: 'bg-red-50 text-red-500',
      valueTone: 'text-red-600',
    },
    {
      label: '入库商品',
      getValue: () => m ? fmt(m.total_products) : '—',
      getSub: () => null,
      icon: 'M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4',
      iconBg: 'bg-indigo-50 text-indigo-500',
      valueTone: 'text-indigo-600',
    },
    {
      label: '累计 IP 使用',
      getValue: () => m ? fmt(m.total_ip_used) : '—',
      getSub: () => m ? `每 IP 产出 ${fmtYield(m.avg_yield_per_ip)} 件` : null,
      icon: 'M21 12a9 9 0 01-9 9m9-9a9 9 0 00-9-9m9 9H3m9 9a9 9 0 01-9-9m9 9c1.657 0 3-4.03 3-9s-1.343-9-3-9m0 18c-1.657 0-3-4.03-3-9s1.343-9 3-9',
      iconBg: 'bg-purple-50 text-purple-500',
      valueTone: 'text-purple-600',
    },
  ]
})
</script>

<template>
  <div class="card">
    <!-- 卡片标题栏 -->
    <div class="card-header">
      <!-- 图表图标 -->
      <svg class="w-4 h-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path stroke-linecap="round" stroke-linejoin="round" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/>
      </svg>
      <span>采集指标</span>

      <!-- 弹性填充 -->
      <div class="flex-1" />

      <!-- 刷新间隔控制 -->
      <div class="flex items-center gap-2 text-xs font-normal text-blue-100">
        <span>刷新间隔</span>
        <select
          v-model.number="refreshInterval"
          class="bg-walmart-600 text-white border border-walmart-400 rounded px-1.5 py-0.5 text-xs focus:outline-none focus:ring-1 focus:ring-white"
          @change="onIntervalChange"
        >
          <option :value="5">5 秒</option>
          <option :value="10">10 秒</option>
          <option :value="15">15 秒</option>
          <option :value="30">30 秒</option>
          <option :value="60">60 秒</option>
        </select>
      </div>

      <!-- 手动刷新按钮 -->
      <button
        class="ml-2 p-1 rounded hover:bg-walmart-600 transition-colors"
        :class="{ 'animate-spin': loading }"
        title="立即刷新"
        @click="fetchMetrics"
      >
        <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
        </svg>
      </button>
    </div>

    <!-- 卡片主体 -->
    <div class="card-body">
      <!-- 错误提示 -->
      <div
        v-if="error"
        class="mb-4 flex items-center gap-2 px-3 py-2 rounded-lg bg-red-50 text-red-600 text-sm"
      >
        <svg class="w-4 h-4 shrink-0" viewBox="0 0 20 20" fill="currentColor">
          <path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z" clip-rule="evenodd"/>
        </svg>
        <span>{{ error }}</span>
        <button class="ml-auto text-red-400 hover:text-red-600" @click="error = null">✕</button>
      </div>

      <!-- 首次加载骨架屏 -->
      <div v-if="!metrics && loading" class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        <div
          v-for="i in 6"
          :key="i"
          class="metric-card animate-pulse"
        >
          <div class="w-8 h-8 rounded-lg bg-gray-100 mb-2" />
          <div class="h-7 w-16 bg-gray-100 rounded mb-1" />
          <div class="h-3 w-20 bg-gray-50 rounded" />
        </div>
      </div>

      <!-- 指标卡片网格 -->
      <div
        v-else
        class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3"
      >
        <div
          v-for="card in cards"
          :key="card.label"
          class="metric-card hover:shadow-card-hover transition-shadow duration-200"
        >
          <!-- 图标 -->
          <div class="w-8 h-8 rounded-lg flex items-center justify-center mb-2" :class="card.iconBg">
            <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" :d="card.icon"/>
            </svg>
          </div>

          <!-- 主要数值 -->
          <div class="metric-value" :class="card.valueTone">
            {{ card.getValue() }}
          </div>

          <!-- 指标名称 -->
          <div class="metric-label">{{ card.label }}</div>

          <!-- 次要信息（如成功率、占比等） -->
          <div v-if="card.getSub()" class="metric-sub mt-0.5 text-xs">
            {{ card.getSub() }}
          </div>
        </div>
      </div>

      <!-- 底部时间戳 -->
      <div v-if="metrics" class="mt-3 text-right text-xs text-gray-400">
        {{ loading ? '刷新中...' : `最后更新：${new Date().toLocaleTimeString('zh-CN')}` }}
      </div>
    </div>
  </div>
</template>
