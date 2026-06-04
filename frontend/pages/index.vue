<template>
  <!-- pages/index.vue — 主页，挂载全部四大采集面板 + 结果查看器 -->
  <div class="flex flex-col gap-6">
    <!-- 任务提交面板 -->
    <TaskSubmit />

    <!-- 任务列表面板 -->
    <TaskList />

    <!-- 指标面板 -->
    <MetricsPanel />

    <!-- 代理面板 -->
    <ProxyPanel />

    <!-- 结果查看器（全屏遮罩，Teleport 到 body，通过 useSelectedTask 驱动） -->
    <ResultViewer />
  </div>
</template>
