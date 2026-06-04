// nuxt.config.ts — 沃尔玛采集前端配置
export default defineNuxtConfig({
  compatibilityDate: '2024-04-03',

  // 模块
  modules: ['@nuxtjs/tailwindcss'],

  // 运行时配置：apiBase 默认 '/api'，可由环境变量 NUXT_PUBLIC_API_BASE 覆盖
  runtimeConfig: {
    public: {
      apiBase: '/api',
    },
  },

  // Nitro 路由规则：把 /api/** 代理到后端 http://localhost:8900
  // 去掉 /api 前缀转发（proxyHeaders 透传所有请求头含 X-API-Key）
  nitro: {
    routeRules: {
      '/api/**': {
        proxy: 'http://localhost:8900/**',
        headers: {},
      },
    },
  },

  // TypeScript 严格模式
  typescript: {
    strict: true,
    shim: false,
  },

  // Tailwind CSS 在 assets/css/main.css 引入
  css: ['~/assets/css/main.css'],

  // Vite 开发服务器也配 proxy 做兜底（部分场景 nitro routeRules 不生效时）
  vite: {
    server: {
      proxy: {
        '/api': {
          target: 'http://localhost:8900',
          changeOrigin: true,
          rewrite: (path: string) => path.replace(/^\/api/, ''),
        },
      },
    },
  },

  devtools: { enabled: false },
})
