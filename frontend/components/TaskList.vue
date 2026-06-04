<script setup lang="ts">
/**
 * TaskList.vue — 任务列表面板
 *
 * 功能：
 *  - 轮询 GET /tasks，可配置刷新间隔
 *  - 状态徽章(pending/running/done/failed/blocked)
 *  - 进度条（progress / total）
 *  - 结果数量、创建时间（格式化 ISO）
 *  - 「查看结果」按钮 → 通过 useSelectedTask 通知 ResultViewer
 */

import type { TaskItem } from '~/composables/useSelectedTask'

// ─── API & 共享状态 ───────────────────────────────────────────
const api = useApi()
const { openTask } = useSelectedTask()

// ─── 任务列表数据 ─────────────────────────────────────────────
const tasks = ref<TaskItem[]>([])
const total = ref(0)
const loading = ref(false)
const error = ref('')

// ─── 分页 ─────────────────────────────────────────────────────
const limit = 50
const offset = ref(0)
const hasMore = computed(() => tasks.value.length < total.value)

// ─── 刷新间隔配置（秒） ───────────────────────────────────────
const intervalOptions = [3, 5, 10, 30, 60]
const refreshInterval = ref(5) // 默认 5 秒
let pollTimer: ReturnType<typeof setInterval> | null = null

// ─── 标志：用户是否已点过「加载更多」 ─────────────────────────
// true 时轮询不重置页面，只刷新已加载范围
const hasLoadedMore = ref(false)

// ─── 获取任务列表 ─────────────────────────────────────────────
async function fetchTasks(reset = false) {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    if (reset) {
      offset.value = 0
      tasks.value = []
    }

    interface TasksResponse {
      items: TaskItem[]
      total: number
    }

    const res = await api.get<TasksResponse>('/tasks', {
      limit,
      offset: offset.value,
    })
    if (reset) {
      tasks.value = res.items
    } else {
      tasks.value.push(...res.items)
    }
    // 优先使用后端返回的 total（BE1 已添加），fallback 到本次条数
    total.value = res.total ?? res.items.length
    offset.value += res.items.length
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : '获取任务列表失败'
  } finally {
    loading.value = false
  }
}

/**
 * 轮询专用刷新：
 * - 用户未展开「加载更多」时：正常 reset，始终显示最新 limit 条
 * - 用户已展开「加载更多」后：分批拉取已加载范围，原地更新（不重置滚动位置），
 *   避免静默清除用户浏览位置
 */
async function pollRefresh() {
  if (loading.value) return

  if (!hasLoadedMore.value) {
    // 未展开更多：普通 reset 即可
    await fetchTasks(true)
    return
  }

  // 已展开更多：按 offset 分批重新拉取，覆盖当前数据而不清空列表
  const loadedCount = tasks.value.length
  if (loadedCount === 0) {
    await fetchTasks(true)
    return
  }

  loading.value = true
  error.value = ''
  try {
    interface TasksResponse {
      items: TaskItem[]
      total: number
    }

    // 一次拉取已加载的全部条数（后端无限制，或 loadedCount 不超 limit 时等价于 reset）
    const res = await api.get<TasksResponse>('/tasks', {
      limit: loadedCount,
      offset: 0,
    })
    tasks.value = res.items
    total.value = res.total ?? res.items.length
    // 保持 offset = 已加载数量，让「加载更多」仍可续加
    offset.value = res.items.length
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : '获取任务列表失败'
  } finally {
    loading.value = false
  }
}

// ─── 轮询控制 ─────────────────────────────────────────────────
function startPolling() {
  stopPolling()
  pollTimer = setInterval(() => {
    pollRefresh()
  }, refreshInterval.value * 1000)
}

function stopPolling() {
  if (pollTimer !== null) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

// 切换刷新间隔时重置定时器
watch(refreshInterval, () => {
  startPolling()
})

// ─── 手动刷新 ─────────────────────────────────────────────────
function manualRefresh() {
  fetchTasks(true)
}

// ─── 加载更多 ─────────────────────────────────────────────────
function loadMore() {
  // 标记用户已展开更多，轮询时不再 reset 回首页
  hasLoadedMore.value = true
  fetchTasks(false)
}

// ─── 查看结果 ─────────────────────────────────────────────────
function viewResult(task: TaskItem) {
  openTask(task)
}

// ─── 格式化时间 ───────────────────────────────────────────────
function formatTime(iso: string): string {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    if (isNaN(d.getTime())) return iso
    const pad = (n: number) => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  } catch {
    return iso
  }
}

// ─── 进度百分比 ───────────────────────────────────────────────
function progressPct(task: TaskItem): number {
  if (!task.total || task.total === 0) {
    return task.status === 'done' ? 100 : 0
  }
  return Math.min(100, Math.round((task.progress / task.total) * 100))
}

// ─── 进度条颜色 ───────────────────────────────────────────────
function progressColor(task: TaskItem): string {
  if (task.status === 'done') return 'bg-green-500'
  if (task.status === 'failed') return 'bg-red-500'
  if (task.status === 'blocked') return 'bg-orange-500'
  if (task.status === 'running') return 'bg-walmart-500'
  return 'bg-gray-300'
}

// ─── 任务类型标签 ─────────────────────────────────────────────
// 键与后端 tasks.py VALID_TYPES 保持一致：detail / keyword / seller
function typeLabel(type: string): string {
  const map: Record<string, string> = {
    detail: 'ID采集',
    keyword: '关键词',
    seller: '店铺',
  }
  return map[type] ?? type
}

// ─── 状态中文 ─────────────────────────────────────────────────
function statusLabel(status: string): string {
  const map: Record<string, string> = {
    pending: '等待中',
    running: '运行中',
    done: '已完成',
    failed: '失败',
    blocked: '封控',
  }
  return map[status] ?? status
}

// ─── 生命周期 ─────────────────────────────────────────────────
onMounted(() => {
  fetchTasks(true)
  startPolling()
})

onUnmounted(() => {
  stopPolling()
})
</script>

<template>
  <div class="card">
    <!-- 标题栏 -->
    <div class="card-header flex items-center justify-between">
      <div class="flex items-center gap-2">
        <!-- 列表图标 -->
        <svg class="w-4 h-4" viewBox="0 0 20 20" fill="currentColor">
          <path fill-rule="evenodd"
            d="M3 4a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1zm0 4a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1zm0 4a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1zm0 4a1 1 0 011-1h6a1 1 0 110 2H4a1 1 0 01-1-1z"
            clip-rule="evenodd" />
        </svg>
        <span>任务列表</span>
        <!-- 任务总数徽章 -->
        <span v-if="total > 0"
          class="ml-1 inline-flex items-center px-1.5 py-0.5 rounded-full text-xs font-bold bg-white/20 text-white">
          {{ total }}
        </span>
      </div>

      <!-- 右侧：刷新间隔 + 手动刷新 -->
      <div class="flex items-center gap-2">
        <span class="text-xs text-white/80 hidden sm:inline">自动刷新</span>
        <select
          v-model="refreshInterval"
          class="text-xs bg-white/10 text-white border border-white/30 rounded px-2 py-1
                 focus:outline-none focus:ring-1 focus:ring-white/50 cursor-pointer w-20"
        >
          <option v-for="s in intervalOptions" :key="s" :value="s">{{ s }}秒</option>
        </select>
        <button
          class="btn btn-sm bg-white/10 text-white hover:bg-white/20 border border-white/30"
          :disabled="loading"
          title="立即刷新"
          @click="manualRefresh"
        >
          <!-- 刷新图标 -->
          <svg class="w-3.5 h-3.5" :class="{ 'animate-spin': loading }" viewBox="0 0 20 20" fill="currentColor">
            <path fill-rule="evenodd"
              d="M4 2a1 1 0 011 1v2.101a7.002 7.002 0 0111.601 2.566 1 1 0 11-1.885.666A5.002 5.002 0 005.999 7H9a1 1 0 010 2H4a1 1 0 01-1-1V3a1 1 0 011-1zm.008 9.057a1 1 0 011.276.61A5.002 5.002 0 0014.001 13H11a1 1 0 110-2h5a1 1 0 011 1v5a1 1 0 11-2 0v-2.101a7.002 7.002 0 01-11.601-2.566 1 1 0 01.61-1.276z"
              clip-rule="evenodd" />
          </svg>
          刷新
        </button>
      </div>
    </div>

    <!-- 主体 -->
    <div class="card-body p-0">
      <!-- 错误提示 -->
      <div v-if="error" class="mx-4 mt-4 px-3 py-2 bg-red-50 border border-red-200 rounded-lg text-sm text-red-600 flex items-center gap-2">
        <svg class="w-4 h-4 flex-shrink-0" viewBox="0 0 20 20" fill="currentColor">
          <path fill-rule="evenodd"
            d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z"
            clip-rule="evenodd" />
        </svg>
        {{ error }}
      </div>

      <!-- 空状态 -->
      <div v-else-if="!loading && tasks.length === 0"
        class="py-12 flex flex-col items-center gap-3 text-gray-400">
        <svg class="w-10 h-10 text-gray-200" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <path stroke-linecap="round" stroke-linejoin="round"
            d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
        </svg>
        <p class="text-sm">暂无任务，请先提交采集任务</p>
      </div>

      <!-- 任务列表 -->
      <div v-else class="divide-y divide-gray-100">
        <div
          v-for="task in tasks"
          :key="task.id"
          class="px-4 py-3 hover:bg-gray-50 transition-colors group"
        >
          <div class="flex items-start gap-3">
            <!-- 任务编码 + 类型 -->
            <div class="flex-shrink-0 flex flex-col items-start gap-1 pt-0.5 w-28">
              <span class="text-[10px] font-mono text-gray-500 break-all leading-tight"
                    :title="task.code ?? ('#' + task.id)">
                {{ task.code ?? ('#' + task.id) }}
              </span>
              <span class="text-xs px-1.5 py-0.5 bg-gray-100 text-gray-500 rounded font-medium">
                {{ typeLabel(task.type) }}
              </span>
            </div>

            <!-- 中间内容：状态 + 进度 + 元信息 -->
            <div class="flex-1 min-w-0">
              <!-- 第一行：状态徽章 + 进度文字 + 结果数 -->
              <div class="flex items-center gap-2 flex-wrap mb-1.5">
                <span :class="`badge badge-${task.status}`">
                  {{ statusLabel(task.status) }}
                </span>

                <!-- 进度文字 -->
                <span class="text-xs text-gray-500">
                  <template v-if="task.total > 0">
                    {{ task.progress }} / {{ task.total }}
                    <span class="text-gray-400">({{ progressPct(task) }}%)</span>
                  </template>
                  <template v-else-if="task.status === 'done'">
                    已完成
                  </template>
                  <template v-else>
                    —
                  </template>
                </span>

                <!-- 结果数 -->
                <span v-if="task.result_count > 0"
                  class="text-xs bg-green-50 text-green-700 px-2 py-0.5 rounded-full font-medium">
                  {{ task.result_count }} 条结果
                </span>
              </div>

              <!-- 进度条 -->
              <div class="h-1.5 bg-gray-100 rounded-full overflow-hidden mb-1.5">
                <div
                  :class="['h-full rounded-full transition-all duration-500', progressColor(task)]"
                  :style="{ width: `${progressPct(task)}%` }"
                />
              </div>

              <!-- 错误信息（blocked/failed 时显示 error_msg） -->
              <p v-if="task.error_msg && (task.status === 'failed' || task.status === 'blocked')"
                class="text-xs text-red-500 truncate mt-1"
                :title="task.error_msg">
                {{ task.error_msg }}
              </p>

              <!-- 创建时间 / 更新时间 -->
              <p class="text-xs text-gray-400">
                <svg class="w-3 h-3 inline mr-0.5 -mt-0.5" viewBox="0 0 20 20" fill="currentColor">
                  <path fill-rule="evenodd"
                    d="M10 18a8 8 0 100-16 8 8 0 000 16zm1-12a1 1 0 10-2 0v4a1 1 0 00.293.707l2.828 2.829a1 1 0 101.415-1.415L11 9.586V6z"
                    clip-rule="evenodd" />
                </svg>
                <!-- 优先展示 updated_at（运行中/完成状态下更有参考价值） -->
                <template v-if="task.updated_at && task.updated_at !== task.created_at">
                  {{ formatTime(task.created_at) }}
                  <span class="text-gray-300 mx-1">→</span>
                  {{ formatTime(task.updated_at) }}
                </template>
                <template v-else>
                  {{ formatTime(task.created_at) }}
                </template>
              </p>
            </div>

            <!-- 右侧操作按钮 -->
            <div class="flex-shrink-0 flex items-start pt-0.5">
              <button
                class="btn btn-sm btn-secondary opacity-0 group-hover:opacity-100 transition-opacity"
                :class="{ 'opacity-100': task.status === 'done' }"
                :disabled="task.result_count === 0 && task.status !== 'done'"
                :title="task.result_count === 0 && task.status !== 'done' ? '暂无结果' : '查看采集结果'"
                @click="viewResult(task)"
              >
                <svg class="w-3.5 h-3.5" viewBox="0 0 20 20" fill="currentColor">
                  <path d="M10 12a2 2 0 100-4 2 2 0 000 4z" />
                  <path fill-rule="evenodd"
                    d="M.458 10C1.732 5.943 5.522 3 10 3s8.268 2.943 9.542 7c-1.274 4.057-5.064 7-9.542 7S1.732 14.057.458 10zM14 10a4 4 0 11-8 0 4 4 0 018 0z"
                    clip-rule="evenodd" />
                </svg>
                查看结果
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- 加载中占位（首次） -->
      <div v-if="loading && tasks.length === 0" class="py-8 flex justify-center">
        <div class="flex items-center gap-2 text-gray-400 text-sm">
          <svg class="w-4 h-4 animate-spin text-walmart-500" viewBox="0 0 24 24" fill="none">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
            <path class="opacity-75" fill="currentColor"
              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
          </svg>
          加载中…
        </div>
      </div>

      <!-- 底部：加载更多 / 已加载全部 -->
      <div v-if="tasks.length > 0" class="px-4 py-3 border-t border-gray-100 flex items-center justify-between">
        <span class="text-xs text-gray-400">
          已显示 {{ tasks.length }} / {{ total }} 条
        </span>
        <button
          v-if="hasMore"
          class="btn btn-sm btn-secondary"
          :disabled="loading"
          @click="loadMore"
        >
          <svg v-if="loading" class="w-3.5 h-3.5 animate-spin" viewBox="0 0 24 24" fill="none">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
            <path class="opacity-75" fill="currentColor"
              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
          </svg>
          加载更多
        </button>
        <span v-else class="text-xs text-gray-400">— 已全部加载 —</span>
      </div>
    </div>
  </div>
</template>
