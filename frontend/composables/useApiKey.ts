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
const DEFAULT_KEY = 'dev-key-change-me'

// 模块级共享状态（单例），避免多组件各自读写不一致
const _apiKey = ref<string>('')

function _init() {
  if (import.meta.client) {
    const stored = localStorage.getItem(API_KEY_STORAGE)
    _apiKey.value = stored ?? DEFAULT_KEY
  } else {
    _apiKey.value = DEFAULT_KEY
  }
}

export function useApiKey() {
  // 首次调用时从 localStorage 初始化
  if (import.meta.client && _apiKey.value === '') {
    _init()
  }

  /**
   * 更新 API Key 并写入 localStorage
   */
  function setApiKey(key: string) {
    _apiKey.value = key
    if (import.meta.client) {
      localStorage.setItem(API_KEY_STORAGE, key)
    }
  }

  return {
    apiKey: _apiKey as Readonly<typeof _apiKey>,
    setApiKey,
  }
}
