<script setup lang="ts">
/**
 * TaskSubmit.vue — 任务提交面板
 * 三标签：ids / keyword / seller，每个标签都含文件导入功能。
 * ids 标签：多行 textarea（每行一个商品 ID），固定采集详情，无 with_detail 勾选。
 * keyword 标签：关键词 + max_pages + min_price + max_price + 采详情勾选。
 * seller 标签：seller_id + max_pages + 采详情勾选。
 * 文件导入：txt/csv/xlsx → POST /collect/import（multipart）。
 */

// ---- 类型定义 ----

interface CollectIdsResult {
  task_id: string | number
}

interface ImportResult {
  parsed: number
  created: number
  task_ids: (string | number)[]
}

// ---- Composables ----
const api = useApi()

// ---- 标签状态 ----
type TabKey = 'ids' | 'keyword' | 'seller'
const activeTab = ref<TabKey>('ids')

function switchTab(tab: TabKey) {
  activeTab.value = tab
  // 切换时清除旧的提交/导入结果提示
  submitResult.value = null
  importResult.value = null
  errorMsg.value = ''
}

// ---- ids 标签 ----
const idsText = ref('')                  // 多行文本，每行一个 ID
const idsLoading = ref(false)
const idsFileRef = ref<HTMLInputElement | null>(null)
const idsImportLoading = ref(false)

// ---- keyword 标签 ----
const kwKeyword = ref('')
const kwMaxPages = ref<number>(25)   // 默认翻满（沃尔玛搜索硬上限25页）
const kwMinPrice = ref<number | null>(null)
const kwMaxPrice = ref<number | null>(null)
const kwWithDetail = ref(true)
const kwLoading = ref(false)
const kwFileRef = ref<HTMLInputElement | null>(null)
const kwImportLoading = ref(false)

// ---- seller 标签 ----
const selSellerId = ref('')
const selMaxPages = ref<number>(30)   // 默认覆盖大店铺（卖家全店无25页硬限）
const selWithDetail = ref(true)
const selLoading = ref(false)
const selFileRef = ref<HTMLInputElement | null>(null)
const selImportLoading = ref(false)

// ---- 结果 / 错误 ----
const submitResult = ref<{ taskId: string | number } | null>(null)
const importResult = ref<ImportResult | null>(null)
const errorMsg = ref('')

// ---- 解析 ids 文本 ----
function parseIds(text: string): string[] {
  return text
    .split(/[\n,]+/)
    .map((s) => s.trim())
    .filter(Boolean)
}

// ---- 提交 ids ----
async function submitIds() {
  errorMsg.value = ''
  submitResult.value = null
  importResult.value = null

  const ids = parseIds(idsText.value)
  if (ids.length === 0) {
    errorMsg.value = '请至少输入一个商品 ID'
    return
  }
  idsLoading.value = true
  try {
    // ids 标签不传 with_detail，后端直接采详情
    const res = await api.post<CollectIdsResult>('/collect/ids', {
      ids,
      with_detail: true,
    })
    submitResult.value = { taskId: res.task_id }
  } catch (e: unknown) {
    errorMsg.value = e instanceof Error ? e.message : String(e)
  } finally {
    idsLoading.value = false
  }
}

// ---- 提交 keyword ----
async function submitKeyword() {
  errorMsg.value = ''
  submitResult.value = null
  importResult.value = null

  if (!kwKeyword.value.trim()) {
    errorMsg.value = '请输入搜索关键词'
    return
  }
  kwLoading.value = true
  try {
    const body: Record<string, unknown> = {
      keyword: kwKeyword.value.trim(),
      max_pages: kwMaxPages.value,
      with_detail: kwWithDetail.value,
    }
    if (kwMinPrice.value !== null) body.min_price = kwMinPrice.value
    if (kwMaxPrice.value !== null) body.max_price = kwMaxPrice.value

    const res = await api.post<CollectIdsResult>('/collect/keyword', body)
    submitResult.value = { taskId: res.task_id }
  } catch (e: unknown) {
    errorMsg.value = e instanceof Error ? e.message : String(e)
  } finally {
    kwLoading.value = false
  }
}

// ---- 提交 seller ----
async function submitSeller() {
  errorMsg.value = ''
  submitResult.value = null
  importResult.value = null

  if (!selSellerId.value.trim()) {
    errorMsg.value = '请输入卖家 ID'
    return
  }
  selLoading.value = true
  try {
    const res = await api.post<CollectIdsResult>('/collect/seller', {
      seller_id: selSellerId.value.trim(),
      max_pages: selMaxPages.value,
      with_detail: selWithDetail.value,
    })
    submitResult.value = { taskId: res.task_id }
  } catch (e: unknown) {
    errorMsg.value = e instanceof Error ? e.message : String(e)
  } finally {
    selLoading.value = false
  }
}

// ---- 通用文件导入 ----
async function handleImport(
  file: File,
  type: 'ids' | 'keyword' | 'seller',
  withDetail: boolean,
  maxPages: number,
  minPrice?: number | null,
  maxPrice?: number | null,
) {
  errorMsg.value = ''
  submitResult.value = null
  importResult.value = null

  const form = new FormData()
  form.append('file', file)
  form.append('type', type)
  form.append('with_detail', String(withDetail))
  form.append('max_pages', String(maxPages))
  if (minPrice !== null && minPrice !== undefined) {
    form.append('min_price', String(minPrice))
  }
  if (maxPrice !== null && maxPrice !== undefined) {
    form.append('max_price', String(maxPrice))
  }

  try {
    const res = await api.postForm<ImportResult>('/collect/import', form)
    importResult.value = res
  } catch (e: unknown) {
    errorMsg.value = e instanceof Error ? e.message : String(e)
  }
}

// ---- 触发文件选择 ----
function triggerFileInput(inputRef: Ref<HTMLInputElement | null>) {
  inputRef.value?.click()
}

// ---- ids 文件导入处理 ----
async function onIdsFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  idsImportLoading.value = true
  await handleImport(file, 'ids', true, 1)
  idsImportLoading.value = false
  input.value = ''
}

// ---- keyword 文件导入处理 ----
async function onKwFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  kwImportLoading.value = true
  await handleImport(
    file,
    'keyword',
    kwWithDetail.value,
    kwMaxPages.value,
    kwMinPrice.value,
    kwMaxPrice.value,
  )
  kwImportLoading.value = false
  input.value = ''
}

// ---- seller 文件导入处理 ----
async function onSelFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  selImportLoading.value = true
  await handleImport(file, 'seller', selWithDetail.value, selMaxPages.value)
  selImportLoading.value = false
  input.value = ''
}

// ---- 当前标签是否正在加载 ----
const isSubmitting = computed(() => {
  if (activeTab.value === 'ids') return idsLoading.value
  if (activeTab.value === 'keyword') return kwLoading.value
  return selLoading.value
})

const isImporting = computed(() => {
  if (activeTab.value === 'ids') return idsImportLoading.value
  if (activeTab.value === 'keyword') return kwImportLoading.value
  return selImportLoading.value
})
</script>

<template>
  <div class="card">
    <!-- 卡片标题 -->
    <div class="card-header">
      <!-- 上传图标 -->
      <svg class="w-4 h-4 flex-shrink-0" viewBox="0 0 20 20" fill="currentColor">
        <path
          fill-rule="evenodd"
          d="M3 17a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1zM6.293 6.707a1 1 0 010-1.414l3-3a1 1 0 011.414 0l3 3a1 1 0 01-1.414 1.414L11 5.414V13a1 1 0 11-2 0V5.414L7.707 6.707a1 1 0 01-1.414 0z"
          clip-rule="evenodd"
        />
      </svg>
      提交采集任务
    </div>

    <div class="card-body">
      <!-- 标签页 -->
      <div class="tabs">
        <button
          class="tab"
          :class="{ active: activeTab === 'ids' }"
          @click="switchTab('ids')"
        >
          按商品 ID
        </button>
        <button
          class="tab"
          :class="{ active: activeTab === 'keyword' }"
          @click="switchTab('keyword')"
        >
          关键词搜索
        </button>
        <button
          class="tab"
          :class="{ active: activeTab === 'seller' }"
          @click="switchTab('seller')"
        >
          卖家店铺
        </button>
      </div>

      <!-- ===== ids 标签 ===== -->
      <div v-if="activeTab === 'ids'">
        <!-- 说明提示 -->
        <div
          class="flex items-start gap-2 bg-blue-50 border border-blue-200 rounded-lg px-3 py-2 mb-3 text-xs text-blue-700"
        >
          <svg
            class="w-4 h-4 flex-shrink-0 mt-0.5"
            viewBox="0 0 20 20"
            fill="currentColor"
          >
            <path
              fill-rule="evenodd"
              d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z"
              clip-rule="evenodd"
            />
          </svg>
          按 ID 采集即采详情，无需二段式采集。
        </div>

        <div class="field">
          <label class="field-label">商品 ID 列表（每行一个）</label>
          <textarea
            v-model="idsText"
            rows="6"
            placeholder="每行输入一个商品 ID，例如：&#10;123456789&#10;987654321"
            class="font-mono text-xs"
          />
        </div>

        <div class="flex gap-2 mt-4">
          <!-- 提交按钮 -->
          <button
            class="btn btn-primary flex-1"
            :disabled="isSubmitting || !idsText.trim()"
            @click="submitIds"
          >
            <svg
              v-if="idsLoading"
              class="w-4 h-4 animate-spin"
              viewBox="0 0 24 24"
              fill="none"
            >
              <circle
                class="opacity-25"
                cx="12"
                cy="12"
                r="10"
                stroke="currentColor"
                stroke-width="4"
              />
              <path
                class="opacity-75"
                fill="currentColor"
                d="M4 12a8 8 0 018-8v8H4z"
              />
            </svg>
            {{ idsLoading ? '提交中...' : '开始采集' }}
          </button>

          <!-- 文件导入按钮 -->
          <button
            class="btn btn-secondary"
            :disabled="idsImportLoading"
            @click="triggerFileInput(idsFileRef)"
          >
            <svg
              v-if="idsImportLoading"
              class="w-4 h-4 animate-spin"
              viewBox="0 0 24 24"
              fill="none"
            >
              <circle
                class="opacity-25"
                cx="12"
                cy="12"
                r="10"
                stroke="currentColor"
                stroke-width="4"
              />
              <path
                class="opacity-75"
                fill="currentColor"
                d="M4 12a8 8 0 018-8v8H4z"
              />
            </svg>
            <svg
              v-else
              class="w-4 h-4"
              viewBox="0 0 20 20"
              fill="currentColor"
            >
              <path
                fill-rule="evenodd"
                d="M3 17a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1zM6.293 6.707a1 1 0 010-1.414l3-3a1 1 0 011.414 0l3 3a1 1 0 01-1.414 1.414L11 5.414V13a1 1 0 11-2 0V5.414L7.707 6.707a1 1 0 01-1.414 0z"
                clip-rule="evenodd"
              />
            </svg>
            {{ idsImportLoading ? '导入中...' : '从文件导入' }}
          </button>

          <!-- 隐藏 file input -->
          <input
            ref="idsFileRef"
            type="file"
            accept=".txt,.csv,.xlsx"
            class="hidden"
            @change="onIdsFileChange"
          />
        </div>

        <p class="text-xs text-gray-400 mt-1.5">支持 .txt / .csv / .xlsx，ids 合并为一个任务</p>
      </div>

      <!-- ===== keyword 标签 ===== -->
      <div v-else-if="activeTab === 'keyword'">
        <div class="field">
          <label class="field-label">搜索关键词</label>
          <input
            v-model="kwKeyword"
            type="text"
            placeholder="例如：wireless earbuds"
          />
        </div>

        <div class="grid grid-cols-3 gap-3">
          <div class="field">
            <label class="field-label">最大页数（1-25）</label>
            <input
              v-model.number="kwMaxPages"
              type="number"
              min="1"
              max="25"
              placeholder="25"
            />
          </div>
          <div class="field">
            <label class="field-label">最低价格（$，选填）</label>
            <input
              v-model.number="kwMinPrice"
              type="number"
              min="0"
              step="0.01"
              placeholder="留空则不限"
            />
          </div>
          <div class="field">
            <label class="field-label">最高价格（$，选填）</label>
            <input
              v-model.number="kwMaxPrice"
              type="number"
              min="0"
              step="0.01"
              placeholder="留空则不限"
            />
          </div>
        </div>

        <!-- 采详情勾选 -->
        <label class="flex items-center gap-2 cursor-pointer mb-4 text-sm text-gray-700 select-none">
          <input
            v-model="kwWithDetail"
            type="checkbox"
            class="w-4 h-4 rounded text-walmart-500 focus:ring-walmart-500 focus:ring-2"
          />
          采集详情（二段式，获取完整商品信息）
        </label>

        <div class="flex gap-2">
          <button
            class="btn btn-primary flex-1"
            :disabled="isSubmitting || !kwKeyword.trim()"
            @click="submitKeyword"
          >
            <svg
              v-if="kwLoading"
              class="w-4 h-4 animate-spin"
              viewBox="0 0 24 24"
              fill="none"
            >
              <circle
                class="opacity-25"
                cx="12"
                cy="12"
                r="10"
                stroke="currentColor"
                stroke-width="4"
              />
              <path
                class="opacity-75"
                fill="currentColor"
                d="M4 12a8 8 0 018-8v8H4z"
              />
            </svg>
            {{ kwLoading ? '提交中...' : '开始采集' }}
          </button>

          <button
            class="btn btn-secondary"
            :disabled="kwImportLoading"
            @click="triggerFileInput(kwFileRef)"
          >
            <svg
              v-if="kwImportLoading"
              class="w-4 h-4 animate-spin"
              viewBox="0 0 24 24"
              fill="none"
            >
              <circle
                class="opacity-25"
                cx="12"
                cy="12"
                r="10"
                stroke="currentColor"
                stroke-width="4"
              />
              <path
                class="opacity-75"
                fill="currentColor"
                d="M4 12a8 8 0 018-8v8H4z"
              />
            </svg>
            <svg
              v-else
              class="w-4 h-4"
              viewBox="0 0 20 20"
              fill="currentColor"
            >
              <path
                fill-rule="evenodd"
                d="M3 17a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1zM6.293 6.707a1 1 0 010-1.414l3-3a1 1 0 011.414 0l3 3a1 1 0 01-1.414 1.414L11 5.414V13a1 1 0 11-2 0V5.414L7.707 6.707a1 1 0 01-1.414 0z"
                clip-rule="evenodd"
              />
            </svg>
            {{ kwImportLoading ? '导入中...' : '从文件导入' }}
          </button>

          <input
            ref="kwFileRef"
            type="file"
            accept=".txt,.csv,.xlsx"
            class="hidden"
            @change="onKwFileChange"
          />
        </div>

        <p class="text-xs text-gray-400 mt-1.5">文件每行一个关键词，每行建一个任务</p>
      </div>

      <!-- ===== seller 标签 ===== -->
      <div v-else-if="activeTab === 'seller'">
        <div class="field">
          <label class="field-label">卖家 ID</label>
          <input
            v-model="selSellerId"
            type="text"
            placeholder="例如：F92R2K2JQF4A4"
          />
        </div>

        <div class="field">
          <label class="field-label">最大页数（1-25）</label>
          <input
            v-model.number="selMaxPages"
            type="number"
            min="1"
            max="25"
            placeholder="5"
            class="max-w-xs"
          />
        </div>

        <label class="flex items-center gap-2 cursor-pointer mb-4 text-sm text-gray-700 select-none">
          <input
            v-model="selWithDetail"
            type="checkbox"
            class="w-4 h-4 rounded text-walmart-500 focus:ring-walmart-500 focus:ring-2"
          />
          采集详情（二段式，获取完整商品信息）
        </label>

        <div class="flex gap-2">
          <button
            class="btn btn-primary flex-1"
            :disabled="isSubmitting || !selSellerId.trim()"
            @click="submitSeller"
          >
            <svg
              v-if="selLoading"
              class="w-4 h-4 animate-spin"
              viewBox="0 0 24 24"
              fill="none"
            >
              <circle
                class="opacity-25"
                cx="12"
                cy="12"
                r="10"
                stroke="currentColor"
                stroke-width="4"
              />
              <path
                class="opacity-75"
                fill="currentColor"
                d="M4 12a8 8 0 018-8v8H4z"
              />
            </svg>
            {{ selLoading ? '提交中...' : '开始采集' }}
          </button>

          <button
            class="btn btn-secondary"
            :disabled="selImportLoading"
            @click="triggerFileInput(selFileRef)"
          >
            <svg
              v-if="selImportLoading"
              class="w-4 h-4 animate-spin"
              viewBox="0 0 24 24"
              fill="none"
            >
              <circle
                class="opacity-25"
                cx="12"
                cy="12"
                r="10"
                stroke="currentColor"
                stroke-width="4"
              />
              <path
                class="opacity-75"
                fill="currentColor"
                d="M4 12a8 8 0 018-8v8H4z"
              />
            </svg>
            <svg
              v-else
              class="w-4 h-4"
              viewBox="0 0 20 20"
              fill="currentColor"
            >
              <path
                fill-rule="evenodd"
                d="M3 17a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1zM6.293 6.707a1 1 0 010-1.414l3-3a1 1 0 011.414 0l3 3a1 1 0 01-1.414 1.414L11 5.414V13a1 1 0 11-2 0V5.414L7.707 6.707a1 1 0 01-1.414 0z"
                clip-rule="evenodd"
              />
            </svg>
            {{ selImportLoading ? '导入中...' : '从文件导入' }}
          </button>

          <input
            ref="selFileRef"
            type="file"
            accept=".txt,.csv,.xlsx"
            class="hidden"
            @change="onSelFileChange"
          />
        </div>

        <p class="text-xs text-gray-400 mt-1.5">文件每行一个卖家 ID，每行建一个任务</p>
      </div>

      <!-- ===== 结果 / 错误提示 ===== -->

      <!-- 提交成功 -->
      <Transition name="fade">
        <div
          v-if="submitResult"
          class="mt-4 flex items-start gap-2 bg-green-50 border border-green-200 rounded-lg px-3 py-2 text-sm text-green-700"
        >
          <svg class="w-4 h-4 flex-shrink-0 mt-0.5" viewBox="0 0 20 20" fill="currentColor">
            <path
              fill-rule="evenodd"
              d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z"
              clip-rule="evenodd"
            />
          </svg>
          <span>
            任务已创建，Task ID：
            <code class="font-mono font-semibold bg-green-100 px-1 rounded">{{ submitResult.taskId }}</code>
          </span>
        </div>
      </Transition>

      <!-- 导入成功 -->
      <Transition name="fade">
        <div
          v-if="importResult"
          class="mt-4 bg-green-50 border border-green-200 rounded-lg px-3 py-2 text-sm text-green-700"
        >
          <div class="flex items-start gap-2 mb-1">
            <svg class="w-4 h-4 flex-shrink-0 mt-0.5" viewBox="0 0 20 20" fill="currentColor">
              <path
                fill-rule="evenodd"
                d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z"
                clip-rule="evenodd"
              />
            </svg>
            <span>
              文件导入成功：解析 <strong>{{ importResult.parsed }}</strong> 条，创建
              <strong>{{ importResult.created }}</strong> 个任务
            </span>
          </div>
          <div v-if="importResult.task_ids.length > 0" class="pl-6 text-xs text-green-600">
            Task IDs：
            <code class="font-mono bg-green-100 px-1 rounded">
              {{ importResult.task_ids.join(', ') }}
            </code>
          </div>
        </div>
      </Transition>

      <!-- 错误提示 -->
      <Transition name="fade">
        <div
          v-if="errorMsg"
          class="mt-4 flex items-start gap-2 bg-red-50 border border-red-200 rounded-lg px-3 py-2 text-sm text-red-600"
        >
          <svg class="w-4 h-4 flex-shrink-0 mt-0.5" viewBox="0 0 20 20" fill="currentColor">
            <path
              fill-rule="evenodd"
              d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z"
              clip-rule="evenodd"
            />
          </svg>
          <span>{{ errorMsg }}</span>
        </div>
      </Transition>
    </div>
  </div>
</template>

<style scoped>
/* 过渡动画 */
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease, transform 0.2s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}

/* checkbox 颜色修正（Tailwind 表单插件未安装时的兜底） */
input[type='checkbox'] {
  accent-color: #0071ce;
}
</style>
