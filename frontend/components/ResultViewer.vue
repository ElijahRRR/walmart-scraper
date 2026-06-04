<script setup lang="ts">
/**
 * ResultViewer.vue — 结果查看面板
 *
 * 功能：
 *   - 监听 useSelectedTask().selectedTask，有任务时弹出全屏遮罩浮层
 *   - 内含 products / listings 两个子标签
 *   - 每个子标签使用 keyset 分页（after_id + limit），支持「加载更多」
 *   - 导出 CSV / Excel 按钮调用 useApi().download()
 *   - 「关闭」调用 closeTask() 清空状态
 *
 * 不依赖 props，通过 useSelectedTask() 跨组件接收任务；
 * 导出时自动携带 X-API-Key（useApi 内部处理）。
 */

import type { TaskItem } from '~/composables/useSelectedTask'

// ── 类型定义 ──────────────────────────────────────────────────────────────────

interface Product {
  id: number
  asin?: string
  title?: string
  price?: number | string
  rating?: number | string
  review_count?: number | string
  availability?: string
  brand?: string
  [key: string]: unknown
}

interface Listing {
  id: number
  asin?: string
  title?: string
  price?: number | string
  seller_id?: string
  [key: string]: unknown
}

type ResultItem = Product | Listing

interface PagedResponse<T> {
  items: T[]
  next_cursor: number | null
  count: number
}

// ── Composables ───────────────────────────────────────────────────────────────

const api = useApi()
const { selectedTask, closeTask } = useSelectedTask()

// ── 局部状态 ──────────────────────────────────────────────────────────────────

/** 当前子标签：products | listings */
const activeTab = ref<'products' | 'listings'>('products')

/** products 分页状态 */
const productsItems = ref<Product[]>([])
const productsNextCursor = ref<number | null>(null)
const productsTotal = ref<number>(0)
const productsLoading = ref(false)
const productsError = ref<string | null>(null)

/** listings 分页状态 */
const listingsItems = ref<Listing[]>([])
const listingsNextCursor = ref<number | null>(null)
const listingsTotal = ref<number>(0)
const listingsLoading = ref(false)
const listingsError = ref<string | null>(null)

/** 导出状态 */
const exportingCsv = ref(false)
const exportingXlsx = ref(false)

const PAGE_SIZE = 50

// ── 计算属性 ──────────────────────────────────────────────────────────────────

const isVisible = computed(() => selectedTask.value !== null)
const taskId = computed(() => selectedTask.value?.id ?? null)

/** 当前子标签的数据 */
const currentItems = computed<ResultItem[]>(() =>
  activeTab.value === 'products' ? productsItems.value : listingsItems.value,
)
const currentTotal = computed<number>(() =>
  activeTab.value === 'products' ? productsTotal.value : listingsTotal.value,
)
const currentLoading = computed<boolean>(() =>
  activeTab.value === 'products' ? productsLoading.value : listingsLoading.value,
)
const currentError = computed<string | null>(() =>
  activeTab.value === 'products' ? productsError.value : listingsError.value,
)
const hasMore = computed<boolean>(() => {
  if (activeTab.value === 'products') return productsNextCursor.value !== null
  return listingsNextCursor.value !== null
})

/** 面板标题：任务 ID + 任务类型 */
const panelTitle = computed(() => {
  const t = selectedTask.value as TaskItem | null
  if (!t) return '查看结果'
  return `任务 #${t.id} — 结果查看（${t.result_count ?? 0} 条）`
})

// ── 数据加载 ──────────────────────────────────────────────────────────────────

/** 加载 products 第一页（重置） */
async function loadProducts(reset = false) {
  if (!taskId.value) return
  if (productsLoading.value) return
  productsLoading.value = true
  productsError.value = null
  if (reset) {
    productsItems.value = []
    productsNextCursor.value = null
    productsTotal.value = 0
  }
  try {
    const params: Record<string, unknown> = {
      task_id: taskId.value,
      limit: PAGE_SIZE,
    }
    if (productsNextCursor.value !== null) {
      params.after_id = productsNextCursor.value
    }
    const res = await api.get<PagedResponse<Product>>('/products', params)
    productsItems.value.push(...(res.items ?? []))
    productsNextCursor.value = res.next_cursor ?? null
    productsTotal.value = productsItems.value.length
  } catch (e: unknown) {
    productsError.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    productsLoading.value = false
  }
}

/** 加载 listings 第一页（重置） */
async function loadListings(reset = false) {
  if (!taskId.value) return
  if (listingsLoading.value) return
  listingsLoading.value = true
  listingsError.value = null
  if (reset) {
    listingsItems.value = []
    listingsNextCursor.value = null
    listingsTotal.value = 0
  }
  try {
    const params: Record<string, unknown> = {
      task_id: taskId.value,
      limit: PAGE_SIZE,
    }
    if (listingsNextCursor.value !== null) {
      params.after_id = listingsNextCursor.value
    }
    const res = await api.get<PagedResponse<Listing>>('/listings', params)
    listingsItems.value.push(...(res.items ?? []))
    listingsNextCursor.value = res.next_cursor ?? null
    listingsTotal.value = listingsItems.value.length
  } catch (e: unknown) {
    listingsError.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    listingsLoading.value = false
  }
}

/** 加载更多（当前激活标签） */
async function loadMore() {
  if (activeTab.value === 'products') {
    await loadProducts(false)
  } else {
    await loadListings(false)
  }
}

// ── 导出 ──────────────────────────────────────────────────────────────────────

async function exportFile(fmt: 'csv' | 'xlsx') {
  if (!taskId.value) return
  const kind = activeTab.value // 'products' | 'listings'
  const ext = fmt === 'csv' ? 'csv' : 'xlsx'
  const filename = `${kind}_task${taskId.value}.${ext}`

  if (fmt === 'csv') {
    exportingCsv.value = true
  } else {
    exportingXlsx.value = true
  }

  try {
    await api.download(
      `/export/${kind}`,
      { fmt, task_id: taskId.value },
      filename,
    )
  } catch (e: unknown) {
    alert(`导出失败：${e instanceof Error ? e.message : String(e)}`)
  } finally {
    exportingCsv.value = false
    exportingXlsx.value = false
  }
}

// ── 切换子标签 ────────────────────────────────────────────────────────────────

function switchTab(tab: 'products' | 'listings') {
  if (activeTab.value === tab) return
  activeTab.value = tab
  // 首次切换到该标签时才加载
  if (tab === 'products' && productsItems.value.length === 0) {
    loadProducts(true)
  }
  if (tab === 'listings' && listingsItems.value.length === 0) {
    loadListings(true)
  }
}

// ── 关闭 ──────────────────────────────────────────────────────────────────────

function handleClose() {
  closeTask()
}

// ── 监听 selectedTask 变化：任务变了重新加载 ───────────────────────────────────

watch(
  () => selectedTask.value,
  (task) => {
    if (task) {
      // 重置两个子标签的数据，然后加载当前激活标签
      activeTab.value = 'products'
      productsItems.value = []
      productsNextCursor.value = null
      productsTotal.value = 0
      productsError.value = null
      listingsItems.value = []
      listingsNextCursor.value = null
      listingsTotal.value = 0
      listingsError.value = null
      loadProducts(true)
    }
  },
  { immediate: false },
)

// ── 辅助：动态列表头 ──────────────────────────────────────────────────────────

/**
 * 从结果条目中提取列名（取前几条采样，过滤掉 id，最多 8 列）
 */
function inferColumns(items: ResultItem[]): string[] {
  const sample = items.slice(0, 5)
  const keys = new Set<string>()
  for (const item of sample) {
    for (const k of Object.keys(item)) {
      if (k !== 'id') keys.add(k)
    }
  }
  // id 永远排第一
  const rest = [...keys].slice(0, 8)
  return ['id', ...rest]
}

const productColumns = computed(() => inferColumns(productsItems.value))
const listingColumns = computed(() => inferColumns(listingsItems.value))

const currentColumns = computed(() =>
  activeTab.value === 'products' ? productColumns.value : listingColumns.value,
)

/** 友好列名映射 */
const columnLabels: Record<string, string> = {
  id: 'ID',
  asin: 'ASIN',
  title: '标题',
  price: '价格',
  rating: '评分',
  review_count: '评价数',
  availability: '库存',
  brand: '品牌',
  seller_id: '卖家 ID',
}

function colLabel(col: string): string {
  return columnLabels[col] ?? col
}

/** 截断过长字符串 */
function truncate(val: unknown, max = 60): string {
  const s = val === null || val === undefined ? '' : String(val)
  return s.length > max ? s.slice(0, max) + '…' : s
}
</script>

<template>
  <!-- 全屏遮罩浮层 -->
  <Teleport to="body">
    <Transition name="rv-fade">
      <div
        v-if="isVisible"
        class="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 backdrop-blur-sm p-4"
        @click.self="handleClose"
      >
        <!-- 面板主体 -->
        <div
          class="relative w-full max-w-5xl mt-10 mb-10 bg-white rounded-2xl shadow-2xl overflow-hidden flex flex-col"
          style="min-height: 520px; max-height: calc(100vh - 80px)"
        >
          <!-- 面板标题栏 -->
          <div class="bg-walmart-500 text-white px-5 py-3.5 flex items-center justify-between shrink-0">
            <div class="flex items-center gap-2 font-semibold text-sm">
              <!-- 结果图标 -->
              <svg class="w-4 h-4 opacity-90" viewBox="0 0 20 20" fill="currentColor">
                <path d="M9 2a1 1 0 000 2h2a1 1 0 100-2H9z" />
                <path
                  fill-rule="evenodd"
                  d="M4 5a2 2 0 012-2 3 3 0 003 3h2a3 3 0 003-3 2 2 0 012 2v11a2 2 0 01-2 2H6a2 2 0 01-2-2V5zm3 4a1 1 0 000 2h.01a1 1 0 100-2H7zm3 0a1 1 0 000 2h3a1 1 0 100-2h-3zm-3 4a1 1 0 100 2h.01a1 1 0 100-2H7zm3 0a1 1 0 100 2h3a1 1 0 100-2h-3z"
                  clip-rule="evenodd"
                />
              </svg>
              {{ panelTitle }}
            </div>

            <!-- 操作区：导出 + 关闭 -->
            <div class="flex items-center gap-2">
              <button
                class="btn btn-sm bg-white/20 hover:bg-white/30 text-white"
                :disabled="exportingCsv || currentItems.length === 0"
                @click="exportFile('csv')"
              >
                <svg v-if="exportingCsv" class="w-3.5 h-3.5 animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <circle cx="12" cy="12" r="10" stroke-opacity=".3"/>
                  <path d="M12 2a10 10 0 0110 10" stroke-linecap="round"/>
                </svg>
                <svg v-else class="w-3.5 h-3.5" viewBox="0 0 20 20" fill="currentColor">
                  <path fill-rule="evenodd" d="M3 17a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1zm3.293-7.707a1 1 0 011.414 0L9 10.586V3a1 1 0 112 0v7.586l1.293-1.293a1 1 0 111.414 1.414l-3 3a1 1 0 01-1.414 0l-3-3a1 1 0 010-1.414z" clip-rule="evenodd"/>
                </svg>
                导出 CSV
              </button>

              <button
                class="btn btn-sm bg-white/20 hover:bg-white/30 text-white"
                :disabled="exportingXlsx || currentItems.length === 0"
                @click="exportFile('xlsx')"
              >
                <svg v-if="exportingXlsx" class="w-3.5 h-3.5 animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <circle cx="12" cy="12" r="10" stroke-opacity=".3"/>
                  <path d="M12 2a10 10 0 0110 10" stroke-linecap="round"/>
                </svg>
                <svg v-else class="w-3.5 h-3.5" viewBox="0 0 20 20" fill="currentColor">
                  <path fill-rule="evenodd" d="M3 17a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1zm3.293-7.707a1 1 0 011.414 0L9 10.586V3a1 1 0 112 0v7.586l1.293-1.293a1 1 0 111.414 1.414l-3 3a1 1 0 01-1.414 0l-3-3a1 1 0 010-1.414z" clip-rule="evenodd"/>
                </svg>
                导出 Excel
              </button>

              <!-- 关闭按钮 -->
              <button
                class="w-7 h-7 flex items-center justify-center rounded-lg bg-white/20 hover:bg-white/30 transition-colors"
                title="关闭"
                @click="handleClose"
              >
                <svg class="w-4 h-4" viewBox="0 0 20 20" fill="currentColor">
                  <path
                    fill-rule="evenodd"
                    d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z"
                    clip-rule="evenodd"
                  />
                </svg>
              </button>
            </div>
          </div>

          <!-- 子标签切换 -->
          <div class="px-5 pt-4 shrink-0">
            <div class="tabs">
              <button
                class="tab"
                :class="{ active: activeTab === 'products' }"
                @click="switchTab('products')"
              >
                商品数据
                <span
                  v-if="productsItems.length > 0"
                  class="ml-1 text-xs opacity-75"
                >({{ productsItems.length }})</span>
              </button>
              <button
                class="tab"
                :class="{ active: activeTab === 'listings' }"
                @click="switchTab('listings')"
              >
                Listing 数据
                <span
                  v-if="listingsItems.length > 0"
                  class="ml-1 text-xs opacity-75"
                >({{ listingsItems.length }})</span>
              </button>
            </div>
          </div>

          <!-- 数据区 -->
          <div class="flex-1 overflow-auto px-5 pb-5">
            <!-- 错误提示 -->
            <div
              v-if="currentError"
              class="mb-3 px-4 py-3 bg-red-50 border border-red-200 text-red-700 rounded-lg text-sm flex items-center gap-2"
            >
              <svg class="w-4 h-4 shrink-0" viewBox="0 0 20 20" fill="currentColor">
                <path
                  fill-rule="evenodd"
                  d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z"
                  clip-rule="evenodd"
                />
              </svg>
              {{ currentError }}
            </div>

            <!-- 空状态 -->
            <div
              v-else-if="!currentLoading && currentItems.length === 0"
              class="flex flex-col items-center justify-center py-16 text-gray-400"
            >
              <svg class="w-12 h-12 mb-3 opacity-30" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                <path stroke-linecap="round" stroke-linejoin="round" d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4" />
              </svg>
              <p class="text-sm">暂无数据</p>
            </div>

            <!-- 数据表格 -->
            <div v-else class="overflow-x-auto rounded-lg border border-gray-100">
              <table class="min-w-full text-xs">
                <thead>
                  <tr class="bg-gray-50 border-b border-gray-200">
                    <th
                      v-for="col in currentColumns"
                      :key="col"
                      class="px-3 py-2.5 text-left font-semibold text-gray-600 whitespace-nowrap"
                    >
                      {{ colLabel(col) }}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="(item, index) in currentItems"
                    :key="(item as ResultItem).id ?? index"
                    class="border-b border-gray-100 hover:bg-walmart-50 transition-colors"
                  >
                    <td
                      v-for="col in currentColumns"
                      :key="col"
                      class="px-3 py-2 text-gray-700 whitespace-nowrap"
                      :title="String((item as Record<string, unknown>)[col] ?? '')"
                    >
                      {{ truncate((item as Record<string, unknown>)[col]) }}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            <!-- 加载中骨架 -->
            <div v-if="currentLoading" class="mt-3 flex items-center justify-center py-6 text-gray-400 gap-2 text-sm">
              <svg class="w-4 h-4 animate-spin text-walmart-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="12" cy="12" r="10" stroke-opacity=".25"/>
                <path d="M12 2a10 10 0 0110 10" stroke-linecap="round" stroke="currentColor"/>
              </svg>
              加载中…
            </div>

            <!-- 加载更多 / 已加载全部 -->
            <div class="mt-4 flex items-center justify-between text-xs text-gray-500">
              <span>已加载 {{ currentItems.length }} 条</span>
              <button
                v-if="hasMore"
                class="btn btn-secondary btn-sm"
                :disabled="currentLoading"
                @click="loadMore"
              >
                加载更多
              </button>
              <span
                v-else-if="currentItems.length > 0 && !currentLoading"
                class="text-green-600"
              >
                已全部加载
              </span>
            </div>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
/* 遮罩淡入淡出动效 */
.rv-fade-enter-active,
.rv-fade-leave-active {
  transition: opacity 0.2s ease;
}
.rv-fade-enter-from,
.rv-fade-leave-to {
  opacity: 0;
}

/* 面板滑入动效（配合遮罩淡入） */
.rv-fade-enter-active .relative,
.rv-fade-leave-active .relative {
  transition: transform 0.22s cubic-bezier(0.34, 1.56, 0.64, 1), opacity 0.22s ease;
}
.rv-fade-enter-from .relative,
.rv-fade-leave-to .relative {
  transform: translateY(-16px);
  opacity: 0;
}
</style>
