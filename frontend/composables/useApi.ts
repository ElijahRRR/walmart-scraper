/**
 * useApi — 封装 fetch，自动携带 X-API-Key 请求头，统一 basePath。
 *
 * 用法：
 *   const api = useApi()
 *   const data = await api.get<TaskList>('/tasks', { limit: 20, offset: 0 })
 *   const task = await api.post<Task>('/collect/ids', { ids: [...] })
 *   const result = await api.postForm<ImportResult>('/collect/import', formData)
 *   await api.download('/export/products', { fmt: 'csv', task_id: 1 }, 'products.csv')
 *
 * 方法签名：
 *   get<T>(path: string, params?: Record<string, unknown>): Promise<T>
 *     — GET 请求，params 拼接为查询字符串
 *
 *   post<T>(path: string, body: unknown): Promise<T>
 *     — POST JSON 请求
 *
 *   postForm<T>(path: string, form: FormData): Promise<T>
 *     — POST multipart/form-data（不设 Content-Type，让浏览器自动）
 *
 *   download(path: string, params?: Record<string, unknown>, filename?: string): Promise<void>
 *     — 触发文件下载（GET，blob 模式），自动用 <a> 标签保存
 */

export function useApi() {
  const config = useRuntimeConfig()
  const { apiKey } = useApiKey()

  // 基础路径，默认 '/api'
  const basePath = config.public.apiBase as string

  /**
   * 构建完整 URL（拼接查询参数）
   */
  function buildUrl(path: string, params?: Record<string, unknown>): string {
    const url = `${basePath}${path}`
    if (!params || Object.keys(params).length === 0) return url
    const qs = Object.entries(params)
      .filter(([, v]) => v !== undefined && v !== null && v !== '')
      .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
      .join('&')
    return qs ? `${url}?${qs}` : url
  }

  /**
   * 公共请求头：带 X-API-Key
   */
  function headers(extra?: Record<string, string>): Record<string, string> {
    return {
      'X-API-Key': apiKey.value,
      ...extra,
    }
  }

  /**
   * 统一错误处理：非 2xx 时抛出带 message 的 Error
   */
  async function checkResponse(res: Response): Promise<void> {
    if (!res.ok) {
      let msg = `HTTP ${res.status}`
      try {
        const json = await res.json()
        msg = json.detail ?? json.message ?? msg
      } catch {
        // 忽略 json 解析错误
      }
      throw new Error(msg)
    }
  }

  /**
   * GET 请求，返回解析后的 JSON
   */
  async function get<T>(path: string, params?: Record<string, unknown>): Promise<T> {
    const res = await fetch(buildUrl(path, params), {
      method: 'GET',
      headers: headers(),
    })
    await checkResponse(res)
    return res.json() as Promise<T>
  }

  /**
   * POST JSON 请求，返回解析后的 JSON
   */
  async function post<T>(path: string, body: unknown): Promise<T> {
    const res = await fetch(buildUrl(path), {
      method: 'POST',
      headers: headers({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(body),
    })
    await checkResponse(res)
    return res.json() as Promise<T>
  }

  /**
   * POST multipart/form-data（文件上传），返回解析后的 JSON
   */
  async function postForm<T>(path: string, form: FormData): Promise<T> {
    const res = await fetch(buildUrl(path), {
      method: 'POST',
      // 不设 Content-Type，让浏览器自动设置 multipart boundary
      headers: headers(),
      body: form,
    })
    await checkResponse(res)
    return res.json() as Promise<T>
  }

  /**
   * 文件下载（GET blob），触发浏览器保存对话框
   */
  async function download(
    path: string,
    params?: Record<string, unknown>,
    filename?: string,
  ): Promise<void> {
    const res = await fetch(buildUrl(path, params), {
      method: 'GET',
      headers: headers(),
    })
    await checkResponse(res)

    const blob = await res.blob()
    // 尝试从 Content-Disposition 提取文件名
    const disposition = res.headers.get('content-disposition') ?? ''
    const nameMatch = disposition.match(/filename="?([^";\n]+)"?/i)
    const finalName = filename ?? nameMatch?.[1] ?? 'download'

    // 用 <a> 标签触发下载
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = finalName
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }

  return { get, post, postForm, download }
}
