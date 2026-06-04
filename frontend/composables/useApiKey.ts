/**
 * useApiKey — 从 localStorage 读写 X-API-Key，响应式。
 *
 * 用法：
 *   const { apiKey, setApiKey } = useApiKey()
 *
 * 方法：
 *   apiKey: Ref<string>          — 当前 API Key（响应式）
 *   setApiKey(key: string): void — 更新 key 并持久化到 localStorage
 */

const API_KEY_STORAGE = 'walmart_scraper_api_key'
// 默认 Key 仅供开发预填，生产环境请在后端 .env 设置真实 API_KEY
// WARNING: 使用默认 Key 时后端会打印告警日志，请勿在生产环境沿用
const DEFAULT_KEY = 'dev-key-change-me'

export function useApiKey() {
  // 使用 useState 替代模块级 ref，避免 SSR 跨请求状态污染（hydration mismatch）
  // SSR 侧初始值为空字符串，客户端 hydration 后从 localStorage 读取真实值
  const apiKey = useState<string>('apiKey', () => '')

  // 客户端 hydration 后从 localStorage 读取，若无则回填默认 Key（开发体验保留）
  if (import.meta.client && apiKey.value === '') {
    const stored = localStorage.getItem(API_KEY_STORAGE)
    apiKey.value = stored ?? DEFAULT_KEY
  }

  /**
   * 更新 API Key 并写入 localStorage
   */
  function setApiKey(key: string) {
    apiKey.value = key
    if (import.meta.client) {
      localStorage.setItem(API_KEY_STORAGE, key)
    }
  }

  return {
    apiKey: apiKey as Readonly<typeof apiKey>,
    setApiKey,
  }
}
