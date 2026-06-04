<template>
  <!-- pages/index.vue — 主页占位（F1 阶段替换此文件实现全部功能面板）
       挂载约定：
         F1 在此文件中直接放置以下组件：
           - <TaskSubmitPanel>   任务提交（三标签 ids/keyword/seller + 导入）
           - <TaskListPanel>     任务列表（轮询 /tasks，含查看结果面板）
           - <MetricsPanel>      指标面板（/metrics 自动刷新）
           - <ProxyPanel>        代理面板（/proxy/status + /proxy/rotate）
  -->
  <div class="flex flex-col gap-6">
    <!-- 脚手架就绪提示（F1 完成后删除此块） -->
    <div class="card">
      <div class="card-header">
        <svg class="w-4 h-4" viewBox="0 0 20 20" fill="currentColor">
          <path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd"/>
        </svg>
        F0 脚手架就绪
      </div>
      <div class="card-body">
        <p class="text-sm text-gray-600 mb-3">
          Nuxt 3 + TypeScript + Tailwind CSS 脚手架已初始化完成。
        </p>
        <ul class="text-sm text-gray-500 space-y-1 list-disc list-inside">
          <li>Tailwind 主色：<code class="bg-blue-50 px-1 rounded text-walmart-600">walmart / walmart-500 / #0071ce</code></li>
          <li>API 代理：<code class="bg-gray-50 px-1 rounded">/api/**</code> → <code class="bg-gray-50 px-1 rounded">http://localhost:8900/**</code></li>
          <li>Composables：<code class="bg-gray-50 px-1 rounded">useApiKey()</code>、<code class="bg-gray-50 px-1 rounded">useApi()</code> 已就绪</li>
          <li>API Key 已持久化到 localStorage（顶栏输入框）</li>
        </ul>
      </div>
    </div>

    <!-- F1 组件挂载区（TaskSubmitPanel、TaskListPanel、MetricsPanel、ProxyPanel） -->
  </div>
</template>
