// T3：CopilotPage 行为测试（mock fetch，不走真实后端）。
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, test, vi } from 'vitest'
import { CopilotPage, parseMessageWithCodeBlocks } from './CopilotPage'

const STATUS_OK = { ok: true, version: '0.2.0', min_addon_version: '0.4.0' }
const EMPTY_BUFFER = { ok: true, items: [] }

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function mockFetch(handler: (url: string, init?: RequestInit) => Response) {
  const fetchMock = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => handler(String(url), init))
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function stubClipboard() {
  const writeText = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText },
    configurable: true,
  })
  return writeText
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  window.history.replaceState({}, '', '/')
})

describe('parseMessageWithCodeBlocks', () => {
  test('splits text and gdl fences', async () => {
    expect(parseMessageWithCodeBlocks('原因\n```gdl\nGOSUB 100\n```\n结尾')).toEqual([
      { type: 'text', content: '原因\n' },
      { type: 'code', content: 'GOSUB 100' },
      { type: 'text', content: '\n结尾' },
    ])
  })

  test('ignores case and trims code', async () => {
    expect(parseMessageWithCodeBlocks('```GDL\n   END   \n```')).toEqual([
      { type: 'code', content: 'END' },
    ])
  })

  test('no fences -> single text segment', async () => {
    expect(parseMessageWithCodeBlocks('plain')).toEqual([{ type: 'text', content: 'plain' }])
  })
})

describe('CopilotPage render', () => {
  test('renders header, welcome message, input and send button', async () => {
    mockFetch((url) => {
      if (url === '/api/copilot/status') return jsonResponse(STATUS_OK)
      if (url === '/api/copilot/clipboard-buffer') return jsonResponse(EMPTY_BUFFER)
      return jsonResponse({ ok: false })
    })
    await act(async () => { render(<CopilotPage />) })

    expect(screen.getByText(/GDL Copilot/)).toBeTruthy()
    expect(screen.getByText(/你好！/)).toBeTruthy()
    expect(screen.getByPlaceholderText(/粘贴报错信息/)).toBeTruthy()
    expect(screen.getByRole('button', { name: /发送/ })).toBeTruthy()
  })

  test('renders clipboard collector toggle with zero count', async () => {
    mockFetch((url) => {
      if (url === '/api/copilot/status') return jsonResponse(STATUS_OK)
      if (url === '/api/copilot/clipboard-buffer') return jsonResponse(EMPTY_BUFFER)
      return jsonResponse({ ok: false })
    })
    await act(async () => { render(<CopilotPage />) })
    expect(screen.getByRole('button', { name: /错误收集区（0）/ })).toBeTruthy()
  })
})

describe('CopilotPage chat', () => {
  test('sends message and renders assistant reply with code block copy button', async () => {
    mockFetch((url, init) => {
      if (url === '/api/copilot/status') return jsonResponse(STATUS_OK)
      if (url === '/api/copilot/clipboard-buffer') return jsonResponse(EMPTY_BUFFER)
      if (url === '/api/copilot/chat') {
        const body = JSON.parse(String(init?.body)) as { message: string; history: unknown[] }
        expect(body.message).toBe('GOSUB 报错怎么修')
        expect(Array.isArray(body.history)).toBe(true)
        return jsonResponse({ ok: true, reply: '原因：xxx\n\n```gdl\nGOSUB 100\n```', code_blocks: ['GOSUB 100'] })
      }
      return jsonResponse({ ok: false })
    })
    await act(async () => { render(<CopilotPage />) })

    fireEvent.change(screen.getByPlaceholderText(/粘贴报错信息/), { target: { value: 'GOSUB 报错怎么修' } })
    fireEvent.click(screen.getByRole('button', { name: /发送/ }))

    await waitFor(() => expect(screen.getByText(/原因：xxx/)).toBeTruthy())
    const code = screen.getByText('GOSUB 100')
    expect(code.tagName).toBe('PRE')
    expect(screen.getByRole('button', { name: '复制' })).toBeTruthy()
  })

  test('copy button writes code to clipboard', async () => {
    const writeText = stubClipboard()
    mockFetch((url) => {
      if (url === '/api/copilot/status') return jsonResponse(STATUS_OK)
      if (url === '/api/copilot/clipboard-buffer') return jsonResponse(EMPTY_BUFFER)
      if (url === '/api/copilot/chat') return jsonResponse({ ok: true, reply: '```gdl\nGOSUB 100\n```', code_blocks: ['GOSUB 100'] })
      return jsonResponse({ ok: false })
    })
    await act(async () => { render(<CopilotPage />) })

    fireEvent.change(screen.getByPlaceholderText(/粘贴报错信息/), { target: { value: 'hi' } })
    fireEvent.click(screen.getByRole('button', { name: /发送/ }))
    const copyBtn = await screen.findByRole('button', { name: '复制' })
    fireEvent.click(copyBtn)

    await waitFor(() => expect(writeText).toHaveBeenCalledWith('GOSUB 100'))
    expect(screen.getByRole('button', { name: '✓ 已复制' })).toBeTruthy()
  })

  test('shows server error from JSON body even when HTTP status is 404', async () => {
    // 现有 HTTP transport 把所有 {ok:false} 映射为 404；必须读 body.error 展示
    mockFetch((url, init) => {
      if (url === '/api/copilot/status') return jsonResponse(STATUS_OK)
      if (url === '/api/copilot/clipboard-buffer') return jsonResponse(EMPTY_BUFFER)
      if (url === '/api/copilot/chat') {
        void init
        return jsonResponse({ ok: false, error: 'LLM 配置错误：没有 API Key', status: 400 }, 404)
      }
      return jsonResponse({ ok: false })
    })
    await act(async () => { render(<CopilotPage />) })

    fireEvent.change(screen.getByPlaceholderText(/粘贴报错信息/), { target: { value: 'hi' } })
    fireEvent.click(screen.getByRole('button', { name: /发送/ }))

    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('配置错误'))
  })

  test('shows banner when chat request network-fails', async () => {
    mockFetch((url) => {
      if (url === '/api/copilot/status') return jsonResponse(STATUS_OK)
      if (url === '/api/copilot/clipboard-buffer') return jsonResponse(EMPTY_BUFFER)
      throw new TypeError('Failed to fetch')
    })
    await act(async () => { render(<CopilotPage />) })

    fireEvent.change(screen.getByPlaceholderText(/粘贴报错信息/), { target: { value: 'hi' } })
    fireEvent.click(screen.getByRole('button', { name: /发送/ }))

    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('请求失败'))
  })
})

describe('CopilotPage clipboard collector', () => {
  test('renders error chips from buffer when expanded', async () => {
    mockFetch((url) => {
      if (url === '/api/copilot/status') return jsonResponse(STATUS_OK)
      if (url === '/api/copilot/clipboard-buffer') {
        return jsonResponse({ ok: true, items: ['line 1: error near GOSUB', '第2行 错误：参数未定义'] })
      }
      return jsonResponse({ ok: false })
    })
    await act(async () => { render(<CopilotPage />) })

    // 异步拉取 buffer 后计数才变为 2：先等计数更新再展开
    const toggle = await screen.findByRole('button', { name: /错误收集区（2）/ })
    fireEvent.click(toggle)
    await waitFor(() => expect(screen.getByText('line 1: error near GOSUB')).toBeTruthy())
    expect(screen.getByText('第2行 错误：参数未定义')).toBeTruthy()
  })

  test('clicking a chip fills the input box', async () => {
    mockFetch((url) => {
      if (url === '/api/copilot/status') return jsonResponse(STATUS_OK)
      if (url === '/api/copilot/clipboard-buffer') {
        return jsonResponse({ ok: true, items: ['line 5: warning x.gdl'] })
      }
      return jsonResponse({ ok: false })
    })
    await act(async () => { render(<CopilotPage />) })

    const toggle = await screen.findByRole('button', { name: /错误收集区（1）/ })
    fireEvent.click(toggle)
    const chip = await screen.findByText('line 5: warning x.gdl')
    fireEvent.click(chip)

    const input = screen.getByPlaceholderText(/粘贴报错信息/) as HTMLTextAreaElement
    await waitFor(() => expect(input.value).toBe('line 5: warning x.gdl'))
  })

  test('shows body error and keeps chips when clear fails', async () => {
    mockFetch((url) => {
      if (url === '/api/copilot/status') return jsonResponse(STATUS_OK)
      if (url === '/api/copilot/clipboard-buffer') {
        return jsonResponse({ ok: true, items: ['line 5: warning x.gdl'] })
      }
      if (url === '/api/copilot/clipboard-buffer/clear') {
        return jsonResponse({ ok: false, error: 'buffer locked', status: 503 }, 404)
      }
      return jsonResponse({ ok: false })
    })
    await act(async () => { render(<CopilotPage />) })

    fireEvent.click(await screen.findByRole('button', { name: /错误收集区（1）/ }))
    fireEvent.click(await screen.findByRole('button', { name: '清空' }))

    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('buffer locked'))
    expect(screen.getByText('line 5: warning x.gdl')).toBeTruthy()
  })

  test('summarize sends the summary as a user message', async () => {
    const chatMessages: string[] = []
    mockFetch((url, init) => {
      if (url === '/api/copilot/status') return jsonResponse(STATUS_OK)
      if (url === '/api/copilot/clipboard-buffer') {
        return jsonResponse({ ok: true, items: ['line 1: error A', 'line 2: error B'] })
      }
      if (url === '/api/copilot/summarize-errors') return jsonResponse({ ok: true, summary: '第1、2行存在错误' })
      if (url === '/api/copilot/chat') {
        const body = JSON.parse(String(init?.body)) as { message: string }
        chatMessages.push(body.message)
        return jsonResponse({ ok: true, reply: '收到', code_blocks: [] })
      }
      return jsonResponse({ ok: false })
    })
    await act(async () => { render(<CopilotPage />) })

    const toggle = await screen.findByRole('button', { name: /错误收集区（2）/ })
    fireEvent.click(toggle)
    const summarizeBtn = await screen.findByRole('button', { name: /总结错误/ })
    fireEvent.click(summarizeBtn)

    await waitFor(() => expect(chatMessages).toContain('第1、2行存在错误'))
    // 摘要作为用户消息发出后，assistant 回复出现
    await waitFor(() => expect(screen.getByText('收到')).toBeTruthy())
  })
})

describe('CopilotPage status banner', () => {
  test('shows error banner when status fetch fails', async () => {
    mockFetch((url) => {
      if (url === '/api/copilot/status') throw new TypeError('Failed to fetch')
      return jsonResponse({ ok: false })
    })
    await act(async () => { render(<CopilotPage />) })

    await waitFor(() => expect(screen.getByRole('alert')).toBeTruthy())
    expect(screen.getByRole('alert').textContent).toContain('无法连接')
  })

  test('shows upgrade banner when addon version is below min_addon_version', async () => {
    window.history.pushState({}, '', '/?mode=copilot&addon_version=0.3.1')
    mockFetch((url) => {
      if (url === '/api/copilot/status') return jsonResponse(STATUS_OK)
      if (url === '/api/copilot/clipboard-buffer') return jsonResponse(EMPTY_BUFFER)
      return jsonResponse({ ok: false })
    })
    await act(async () => { render(<CopilotPage />) })

    await waitFor(() => expect(screen.getByRole('alert')).toBeTruthy())
    expect(screen.getByRole('alert').textContent).toContain('请升级 OpenBrep')
    expect(screen.getByRole('alert').textContent).toContain('0.4.0')
  })

  test('no upgrade banner when addon version satisfies min', async () => {
    window.history.pushState({}, '', '/?mode=copilot&addon_version=0.4.0')
    mockFetch((url) => {
      if (url === '/api/copilot/status') return jsonResponse(STATUS_OK)
      if (url === '/api/copilot/clipboard-buffer') return jsonResponse(EMPTY_BUFFER)
      return jsonResponse({ ok: false })
    })
    await act(async () => { render(<CopilotPage />) })

    await new Promise((resolve) => window.setTimeout(resolve, 50))
    expect(screen.queryByRole('alert')).toBeNull()
  })
})
