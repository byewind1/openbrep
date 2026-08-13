// Copilot 精简对话页的 API 直连层（不经过 workbench store / api/client.ts）。
//
// 注意：现有 HTTP transport 会把所有 {ok:false} 响应映射为 HTTP 404，
// 即使 JSON body 内含 status:400。因此这里一律解析 JSON body，
// 以 body 内的 error/status 为准展示具体错误，不能只看 HTTP 状态码。

export interface CopilotHistoryItem {
  role: 'user' | 'assistant'
  content: string
}

export interface CopilotImageItem {
  b64: string
  mime?: string
}

export interface CopilotStatus {
  ok: boolean
  version?: string
  min_addon_version?: string
  error?: string
}

export interface CopilotChatResponse {
  ok: boolean
  reply?: string
  code_blocks?: string[]
  error?: string
  status?: number
}

export interface ClipboardBufferResponse {
  ok: boolean
  items?: string[]
  error?: string
}

export interface SummarizeErrorsResponse {
  ok: boolean
  summary?: string
  error?: string
}

async function copilotFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init)
  // 无论 HTTP 状态，先解析 JSON body（{ok:false} 会以 404 返回，错误细节在 body）
  const payload = (await response.json().catch(() => ({}))) as T
  return payload
}

export function fetchCopilotStatus(): Promise<CopilotStatus> {
  return copilotFetch<CopilotStatus>('/api/copilot/status')
}

export function fetchClipboardBuffer(): Promise<ClipboardBufferResponse> {
  return copilotFetch<ClipboardBufferResponse>('/api/copilot/clipboard-buffer')
}

export function clearClipboardBuffer(items: string[] = []): Promise<ClipboardBufferResponse> {
  return copilotFetch<ClipboardBufferResponse>('/api/copilot/clipboard-buffer/clear', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ items }),
  })
}

export function sendCopilotChat(body: {
  message: string
  history: CopilotHistoryItem[]
  images?: CopilotImageItem[]
}): Promise<CopilotChatResponse> {
  return copilotFetch<CopilotChatResponse>('/api/copilot/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function summarizeClipboardErrors(): Promise<SummarizeErrorsResponse> {
  return copilotFetch<SummarizeErrorsResponse>('/api/copilot/summarize-errors', {
    method: 'POST',
  })
}

// ── 版本比较（D7 握手：min_addon_version vs 当前 ADDON 版本）─────────

export function parseVersion(value: string): number[] {
  return value
    .split('.')
    .map((part) => {
      const n = Number.parseInt(part, 10)
      return Number.isFinite(n) ? n : 0
    })
}

/** current >= min 才返回 true；current 缺失时视为未知，不拦（不弹横幅）。 */
export function isVersionAtLeast(current: string | null | undefined, min: string): boolean {
  if (!current) return true
  const a = parseVersion(current)
  const b = parseVersion(min)
  const len = Math.max(a.length, b.length)
  for (let i = 0; i < len; i += 1) {
    const x = a[i] ?? 0
    const y = b[i] ?? 0
    if (x !== y) return x > y
  }
  return true
}

/**
 * 当前 ADDON 版本来源：C++ 面板加载工作台时以 query 传入
 * （`?mode=copilot&addon_version=0.4.0`，由 ADDON T5 的 C++ 面板提供）；
 * 缺失时返回 null（前端无法判断版本，不显示升级横幅）。
 */
export function getAddonVersionFromUrl(search: string = window.location.search): string | null {
  return new URLSearchParams(search).get('addon_version')
}
