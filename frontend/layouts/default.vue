<template>
  <!-- 默认布局：顶栏 + 主容器 -->
  <div class="min-h-screen flex flex-col">
    <!-- 顶栏 -->
    <header class="bg-walmart-500 text-white shadow-lg sticky top-0 z-50">
      <div class="max-w-6xl mx-auto px-4 py-3 flex items-center gap-4">
        <!-- 品牌标题 -->
        <div class="flex items-center gap-2 shrink-0">
          <!-- 购物车图标 -->
          <svg class="w-6 h-6" viewBox="0 0 24 24" fill="currentColor">
            <path d="M7 18c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2zm10 0c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2zM5.1 4H3V2H1v2h2l3.6 7.6L5.3 14c-.2.3-.3.7-.3 1 0 1.1.9 2 2 2h14v-2H7.5c-.1 0-.2-.1-.2-.2l.1-.3.9-1.5H19c.8 0 1.4-.4 1.8-1L23.8 6H6.2l-.9-2H5.1z"/>
          </svg>
          <h1 class="text-lg font-bold tracking-tight">沃尔玛采集服务</h1>
        </div>

        <!-- 弹性空间 -->
        <div class="flex-1" />

        <!-- API Key 输入 -->
        <div class="flex items-center gap-2 shrink-0">
          <label for="api-key-input" class="text-xs text-blue-100 whitespace-nowrap">
            X-API-Key
          </label>
          <input
            id="api-key-input"
            v-model="localApiKey"
            type="password"
            placeholder="输入 API Key"
            class="px-3 py-1.5 rounded-lg text-xs text-gray-800 bg-white border-0
                   focus:outline-none focus:ring-2 focus:ring-blue-200 w-48"
            @change="onApiKeyChange"
          />
        </div>
      </div>
    </header>

    <!-- 主内容区 — F1 组件在此渲染（pages/index.vue 通过 <NuxtPage> 挂载） -->
    <main class="flex-1 max-w-6xl mx-auto w-full px-4 py-6">
      <slot />
    </main>

    <!-- 底部版权 -->
    <footer class="text-center text-xs text-gray-400 py-3 border-t border-gray-100">
      沃尔玛商品数据采集系统
    </footer>
  </div>
</template>

<script setup lang="ts">
const { apiKey, setApiKey } = useApiKey()

// 本地双向绑定，change 时写入 localStorage
const localApiKey = ref(apiKey.value)

// 监听外部变更（多标签页同步）
watch(apiKey, (v) => {
  localApiKey.value = v
})

function onApiKeyChange() {
  setApiKey(localApiKey.value)
}
</script>
