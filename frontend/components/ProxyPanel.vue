<template>
  <!-- 代理面板：展示各 lane 状态，支持手动切换 IP -->
  <div class="card">
    <!-- 卡片标题 -->
    <div class="card-header flex items-center justify-between">
      <div class="flex items-center gap-2">
        <!-- 网络/代理图标 -->
        <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="12" cy="12" r="10" />
          <line x1="2" y1="12" x2="22" y2="12" />
          <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
        </svg>
        代理面板
      </div>
      <div class="flex items-center gap-2 text-xs font-normal text-blue-100">
        <!-- 自动刷新间隔控制 -->
        <span>刷新间隔</span>
        <select
          v-model="refreshInterval"
          class="px-2 py-0.5 rounded text-gray-800 bg-white border-0 text-xs w-20 focus:ring-1"
          @change="onIntervalChange"
        >
          <option :value="5000">5 秒</option>
          <option :value="10000">10 秒</option>
          <option :value="30000">30 秒</option>
          <option :value="60000">60 秒</option>
          <option :value="0">暂停</option>
        </select>
        <button class="btn btn-sm bg-blue-100/20 text-white hover:bg-blue-100/30 border border-blue-200/30" @click="fetchStatus">
          <svg class="w-3 h-3" :class="{ 'animate-spin': loading }" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="23 4 23 10 17 10" />
            <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
          </svg>
          刷新
        </button>
      </div>
    </div>

    <div class="card-body space-y-4">
      <!-- 错误提示 -->
      <div v-if="error" class="flex items-center gap-2 text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2 text-sm">
        <svg class="w-4 h-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="12" cy="12" r="10" />
          <line x1="12" y1="8" x2="12" y2="12" />
          <line x1="12" y1="16" x2="12.01" y2="16" />
        </svg>
        {{ error }}
      </div>

      <!-- 加载骨架屏 -->
      <div v-if="loading && !lanes.length" class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
        <div v-for="i in 3" :key="i" class="rounded-xl border border-gray-100 p-3 space-y-2 animate-pulse">
          <div class="h-4 bg-gray-200 rounded w-2/3" />
          <div class="h-3 bg-gray-100 rounded w-full" />
          <div class="h-3 bg-gray-100 rounded w-4/5" />
        </div>
      </div>

      <!-- Lane 卡片网格 -->
      <div v-else-if="lanes.length" class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
        <div
          v-for="lane in lanes"
          :key="lane.lane_id"
          class="rounded-xl border p-3 space-y-2 transition-shadow hover:shadow-card-hover"
          :class="lane.state === 'blocked'
            ? 'border-orange-300 bg-orange-50 shadow-sm shadow-orange-100'
            : 'border-gray-200 bg-white'"
        >
          <!-- Lane 标题行 -->
          <div class="flex items-center justify-between">
            <span class="font-semibold text-sm text-gray-800 truncate">
              Lane {{ lane.lane_id }}
            </span>
            <!-- 状态徽章 -->
            <span
              class="badge shrink-0"
              :class="stateBadgeClass(lane.state)"
            >
              {{ stateLabel(lane.state) }}
            </span>
          </div>

          <!-- 当前 IP -->
          <div class="flex items-center gap-1.5 text-xs text-gray-600">
            <svg class="w-3.5 h-3.5 text-walmart-500 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="12" cy="12" r="3" />
              <path d="M19.07 4.93a10 10 0 0 1 0 14.14M4.93 4.93a10 10 0 0 0 0 14.14" />
            </svg>
            <span class="font-mono truncate">{{ lane.current_ip || '—' }}</span>
          </div>

          <!-- IP 寿命 & 使用次数 -->
          <div class="grid grid-cols-2 gap-1 text-xs">
            <div class="bg-gray-50 rounded px-2 py-1">
              <div class="text-gray-400 text-[10px]">IP 寿命</div>
              <div class="font-medium text-gray-700">{{ fmtAge(lane.ip_age_sec) }}</div>
            </div>
            <div class="bg-gray-50 rounded px-2 py-1" title="该IP累计发出的请求数，含重试/列表页/失败，故通常大于产出商品数">
              <div class="text-gray-400 text-[10px]">请求次数</div>
              <div class="font-medium text-gray-700">{{ lane.ip_uses ?? 0 }}</div>
            </div>
            <div class="bg-gray-50 rounded px-2 py-1" title="成功入库的商品数">
              <div class="text-gray-400 text-[10px]">产出商品</div>
              <div class="font-medium text-gray-700">{{ lane.total_products ?? 0 }}</div>
            </div>
            <div class="bg-gray-50 rounded px-2 py-1">
              <div class="text-gray-400 text-[10px]">封控原因</div>
              <div
                class="font-medium truncate"
                :class="lane.last_block ? 'text-orange-600' : 'text-gray-400'"
              >
                {{ lane.last_block || '无' }}
              </div>
            </div>
          </div>

          <!-- 封控高亮提示 -->
          <div
            v-if="lane.state === 'blocked'"
            class="flex items-center gap-1.5 text-xs text-orange-700 bg-orange-100 rounded px-2 py-1.5"
          >
            <svg class="w-3.5 h-3.5 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
              <line x1="12" y1="9" x2="12" y2="13" />
              <line x1="12" y1="17" x2="12.01" y2="17" />
            </svg>
            此 Lane 已被封控，建议切换 IP
          </div>

          <!-- 快捷切换按钮（针对当前卡片 lane） -->
          <button
            class="btn btn-sm btn-secondary w-full text-xs"
            :disabled="rotatingLane === lane.lane_id"
            @click="rotateSpecificLane(lane.lane_id)"
          >
            <svg
              class="w-3 h-3"
              :class="{ 'animate-spin': rotatingLane === lane.lane_id }"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
            >
              <polyline points="23 4 23 10 17 10" />
              <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
            </svg>
            {{ rotatingLane === lane.lane_id ? '切换中…' : '切换此 Lane 的 IP' }}
          </button>
        </div>
      </div>

      <!-- 空状态 -->
      <div v-else class="text-center py-10 text-gray-400 text-sm">
        <svg class="w-10 h-10 mx-auto mb-2 text-gray-300" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <circle cx="12" cy="12" r="10" />
          <line x1="2" y1="12" x2="22" y2="12" />
          <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
        </svg>
        暂无代理 Lane 数据
      </div>

      <!-- 手动指定 Lane ID 切换 IP -->
      <div class="border-t border-gray-100 pt-4">
        <div class="flex items-end gap-2">
          <div class="field flex-1 mb-0">
            <label class="field-label">手动指定 Lane ID</label>
            <!-- P2-12: 类型改 number；placeholder 不再示例字符串格式 -->
            <input
              v-model="manualLaneId"
              type="number"
              min="0"
              step="1"
              placeholder="输入 Lane ID，例如 0"
              @keydown.enter="rotateManualLane"
            />
          </div>
          <!-- P2-12: disabled/spin 比较改用 parseInt 后的数字，与 rotatingLane(number) 一致 -->
          <button
            class="btn btn-primary shrink-0"
            :disabled="Number.isNaN(parseInt(manualLaneId.trim(), 10)) || rotatingLane === parseInt(manualLaneId.trim(), 10)"
            @click="rotateManualLane"
          >
            <svg
              class="w-3.5 h-3.5"
              :class="{ 'animate-spin': rotatingLane === parseInt(manualLaneId.trim(), 10) && !Number.isNaN(parseInt(manualLaneId.trim(), 10)) }"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
            >
              <polyline points="23 4 23 10 17 10" />
              <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
            </svg>
            获取/切换 IP
          </button>
        </div>

        <!-- 切换结果提示 -->
        <div
          v-if="rotateResult"
          class="mt-2 flex items-start gap-2 text-xs rounded-lg px-3 py-2"
          :class="rotateResult.success
            ? 'bg-green-50 border border-green-200 text-green-700'
            : 'bg-red-50 border border-red-200 text-red-600'"
        >
          <svg class="w-3.5 h-3.5 shrink-0 mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path v-if="rotateResult.success" d="M20 6L9 17l-5-5" />
            <template v-else>
              <circle cx="12" cy="12" r="10" />
              <line x1="12" y1="8" x2="12" y2="12" />
              <line x1="12" y1="16" x2="12.01" y2="16" />
            </template>
          </svg>
          <span>{{ rotateResult.message }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
// 代理 Lane 的数据结构（与后端 /proxy/status 返回一致）
// P2-12: lane_id 后端返回 number（Python int），此处修正为 number
interface ProxyLane {
  lane_id: number
  state: string          // 'active' | 'blocked' | 'idle' | 其他
  current_ip: string
  ip_age_sec: number
  ip_uses: number
  total_products: number
  last_block: string | null
}

interface ProxyStatusResponse {
  lanes: ProxyLane[]
}

// P3-6: lane_status 后端实际返回 9 字段 dict，取 .state 字段；用 unknown 兼容两种情况
interface RotateResponse {
  lane_id: number
  new_ip: string
  lane_status: string | Record<string, unknown>
}

interface RotateResult {
  success: boolean
  message: string
}

const api = useApi()

// 响应式状态
const lanes = ref<ProxyLane[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
// P2-12: manualLaneId 存字符串（input value），提交时 parseInt 转 number
const manualLaneId = ref('')
// P2-12: rotatingLane 改为 number | null，与 lane_id 类型一致
const rotatingLane = ref<number | null>(null)
const rotateResult = ref<RotateResult | null>(null)
const refreshInterval = ref(10000)

// 定时器句柄
let intervalHandle: ReturnType<typeof setInterval> | null = null
// P3-8: 存储 setTimeout 句柄，以便在新调用前清除及 onUnmounted 时清除
let resultTimer: ReturnType<typeof setTimeout> | null = null

// ── 格式化 IP 寿命（秒 → 可读字符串）────────────────────────────────────────
function fmtAge(sec: number | null | undefined): string {
  if (sec == null || sec < 0) return '—'
  if (sec < 60) return `${Math.floor(sec)}s`
  if (sec < 3600) return `${Math.floor(sec / 60)}m ${Math.floor(sec % 60)}s`
  return `${Math.floor(sec / 3600)}h ${Math.floor((sec % 3600) / 60)}m`
}

// ── 状态徽章样式 ────────────────────────────────────────────────────────────
function stateBadgeClass(state: string): string {
  switch (state) {
    case 'active': return 'badge-done'
    case 'blocked': return 'badge-blocked'
    case 'idle': return 'badge-pending'
    case 'running': return 'badge-running'
    default: return 'badge bg-gray-100 text-gray-600'
  }
}

// ── 状态可读标签 ─────────────────────────────────────────────────────────────
function stateLabel(state: string): string {
  const map: Record<string, string> = {
    active: '正常',
    blocked: '封控',
    idle: '空闲',
    running: '工作中',
  }
  return map[state] ?? state
}

// ── 拉取 /proxy/status ────────────────────────────────────────────────────────
async function fetchStatus(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const resp = await api.get<ProxyStatusResponse>('/proxy/status')
    lanes.value = resp.lanes ?? []
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : '获取代理状态失败'
  } finally {
    loading.value = false
  }
}

// ── 切换指定 Lane 的 IP ───────────────────────────────────────────────────────
// P2-12: laneId 改为 number，与后端 int 字段一致
async function rotateLane(laneId: number): Promise<void> {
  rotatingLane.value = laneId
  rotateResult.value = null
  try {
    const resp = await api.post<RotateResponse>('/proxy/rotate', { lane_id: laneId })
    // P3-6: lane_status 后端返回 dict，取 .state；若已是字符串则直接用
    const statusStr = typeof resp.lane_status === 'object' && resp.lane_status !== null
      ? String((resp.lane_status as Record<string, unknown>).state ?? 'unknown')
      : String(resp.lane_status ?? 'unknown')
    rotateResult.value = {
      success: true,
      message: `Lane ${resp.lane_id} 已切换 → ${resp.new_ip}（状态：${statusStr}）`,
    }
    // 切换成功后立即刷新 Lane 列表
    await fetchStatus()
  } catch (e: unknown) {
    rotateResult.value = {
      success: false,
      message: e instanceof Error ? e.message : '切换 IP 失败',
    }
  } finally {
    rotatingLane.value = null
    // P3-8: 清除上一个计时器再重新设置，避免组件卸载后写已卸载组件
    if (resultTimer !== null) {
      clearTimeout(resultTimer)
    }
    // 5 秒后自动清除结果提示
    resultTimer = setTimeout(() => { rotateResult.value = null }, 5000)
  }
}

// 卡片内的快捷切换
// P2-12: lane_id 已是 number，直接传入
function rotateSpecificLane(laneId: number): void {
  rotateLane(laneId)
}

// 手动输入框切换
// P2-12: 手动输入框值为字符串，parseInt 后传入；NaN 时拒绝提交
function rotateManualLane(): void {
  const parsed = parseInt(manualLaneId.value.trim(), 10)
  if (Number.isNaN(parsed)) return
  rotateLane(parsed)
}

// ── 自动刷新控制 ───────────────────────────────────────────────────────────────
function startAutoRefresh(): void {
  stopAutoRefresh()
  if (refreshInterval.value > 0) {
    intervalHandle = setInterval(fetchStatus, refreshInterval.value)
  }
}

function stopAutoRefresh(): void {
  if (intervalHandle !== null) {
    clearInterval(intervalHandle)
    intervalHandle = null
  }
}

function onIntervalChange(): void {
  startAutoRefresh()
}

// ── 生命周期 ───────────────────────────────────────────────────────────────────
onMounted(() => {
  fetchStatus()
  startAutoRefresh()
})

onUnmounted(() => {
  stopAutoRefresh()
  // P3-8: 组件卸载时清除结果提示计时器，防止写已卸载组件触发 Vue 警告
  if (resultTimer !== null) {
    clearTimeout(resultTimer)
    resultTimer = null
  }
})
</script>
