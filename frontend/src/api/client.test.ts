import { afterEach, describe, expect, test, vi } from 'vitest'
import {
  askAssistant,
  confirmModifyPlan,
  fetchPreview,
  fetchPreview2D,
  generateWithAssistant,
  generateWithAssistantStream,
  requestModifyPlan,
  updateLlmModel,
  updateSessionLlmModel,
} from './client'

afterEach(() => {
  vi.unstubAllGlobals()
})

function stubFetch(previewBody: unknown) {
  const fetchMock = vi.fn(async (_url: string, _init: RequestInit) => ({
    ok: true,
    json: async () => ({ preview: previewBody }),
  }))
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('fetchPreview quality param (P1b)', () => {
  test('sends quality in the POST body when provided', async () => {
    const fetchMock = stubFetch({ meshes: [], wires: [], warnings: [] })

    await fetchPreview({ A: 1 }, undefined, 'accurate')

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/preview')
    const body = JSON.parse(String(init.body))
    expect(body.quality).toBe('accurate')
    expect(body.parameters).toEqual({ A: 1 })
  })

  test('omits quality when not provided (backwards compatible)', async () => {
    const fetchMock = stubFetch({ meshes: [], wires: [], warnings: [] })

    await fetchPreview({})

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    const body = JSON.parse(String(init.body))
    // JSON.stringify 会丢弃 undefined 字段：无 quality 时 body 只有 parameters
    expect(body).toEqual({ parameters: {} })
  })
})

describe('fetchPreview2D quality param (P1b)', () => {
  test('sends quality in the 2D preview body', async () => {
    const fetchMock = stubFetch({ lines: [], polygons: [], circles: [], arcs: [], warnings: [] })

    await fetchPreview2D({}, undefined, 'accurate')

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/preview/2d')
    const body = JSON.parse(String(init.body))
    expect(body.quality).toBe('accurate')
  })
})

describe('D9 routing mode save payload', () => {
  test('sends explicit routing mode with the preserved Fixed pair', async () => {
    const fetchMock = stubFetch({})
    await updateLlmModel('openai-codex/gpt-5.6-luna', 'low', 'auto')
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/settings/llm/model')
    expect(JSON.parse(String(init.body))).toEqual({
      model: 'openai-codex/gpt-5.6-luna',
      reasoning_effort: 'low',
      codex_routing_mode: 'auto',
    })
  })
})

describe('D16 session model switch payload', () => {
  test('posts to the session route (never the settings route)', async () => {
    const fetchMock = stubFetch({})
    await updateSessionLlmModel('glm-4-flash')
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/session/llm/model')
    expect(init.method).toBe('POST')
    expect(JSON.parse(String(init.body))).toEqual({ model: 'glm-4-flash' })
  })

  test('model=null clears the session override', async () => {
    const fetchMock = stubFetch({})
    await updateSessionLlmModel(null)
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(JSON.parse(String(init.body))).toEqual({ model: null })
  })
})

describe('HF4 assistant history payload', () => {
  const HISTORY = [
    { role: 'user' as const, content: '轮1' },
    { role: 'assistant' as const, content: '答1' },
  ]

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  // ok=false + text：让 readAssistantStream / requestJson 提前返回，
  // 只关心本轮发送的 body 是否携带 history。
  function stubFetch(body: unknown = {}, ok = true) {
    const fetchMock = vi.fn(async (_url: string, _init: RequestInit) => ({
      ok,
      text: async () => '',
      json: async () => body,
    }))
    vi.stubGlobal('fetch', fetchMock)
    return fetchMock
  }

  test('askAssistant sends history in the POST body', async () => {
    const fetchMock = stubFetch({ ok: true, assistant: { kind: 'chat', reply: '答' } })
    await askAssistant('按你提供的顺序加上', HISTORY)
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/assistant')
    const body = JSON.parse(String(init.body))
    expect(body.message).toBe('按你提供的顺序加上')
    expect(body.history).toEqual(HISTORY)
  })

  test('generateWithAssistant sends history without touching existing fields', async () => {
    const fetchMock = stubFetch({ ok: true, assistant: null, preview: null, warnings: [] })
    await generateWithAssistant('按你提供的顺序加上', 'settings', [], HISTORY)
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/assistant/generate')
    const body = JSON.parse(String(init.body))
    expect(body.message).toBe('按你提供的顺序加上')
    expect(body.assistant_settings).toBe('settings')
    expect(body.history).toEqual(HISTORY)
  })

  test('generateWithAssistantStream sends history in the stream body', async () => {
    const fetchMock = stubFetch({}, false)
    await generateWithAssistantStream('按你提供的顺序加上', 'settings', [], undefined, undefined, HISTORY)
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/assistant/generate')
    const body = JSON.parse(String(init.body))
    expect(body.message).toBe('按你提供的顺序加上')
    expect(body.stream).toBe(true)
    expect(body.history).toEqual(HISTORY)
  })

  test('requestModifyPlan sends history alongside intent/confirm_plan', async () => {
    const fetchMock = stubFetch({ ok: true, awaiting_confirmation: false })
    await requestModifyPlan('按你提供的顺序加上', 'settings', [], undefined, HISTORY)
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/assistant/generate')
    const body = JSON.parse(String(init.body))
    expect(body.message).toBe('按你提供的顺序加上')
    expect(body.intent).toBe('MODIFY')
    expect(body.confirm_plan).toBe(true)
    expect(body.stream).toBe(false)
    expect(body.history).toEqual(HISTORY)
  })

  test('confirmModifyPlan sends history alongside approve/stream', async () => {
    const fetchMock = stubFetch({ ok: true, assistant: null, preview: null, warnings: [] })
    await confirmModifyPlan(true, false, undefined, undefined, HISTORY)
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/modify/confirm')
    const body = JSON.parse(String(init.body))
    expect(body.approve).toBe(true)
    expect(body.stream).toBe(false)
    expect(body.history).toEqual(HISTORY)
  })

  test('history defaults to [] when omitted (backwards compatible)', async () => {
    const fetchMock = stubFetch({})
    await askAssistant('你好')
    const body = JSON.parse(String(fetchMock.mock.calls[0][1].body))
    expect(body.history).toEqual([])
  })
})

describe('Codex BYOA API (D1)', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function stubFetch(body: unknown) {
    const fetchMock = vi.fn(async (_url: string, init: RequestInit) => ({
      ok: true,
      json: async () => body,
    }))
    vi.stubGlobal('fetch', fetchMock)
    return fetchMock
  }

  test('fetchCodexStatus GETs the status endpoint', async () => {
    const fetchMock = stubFetch({ ok: true, state: 'signed_out', connected: false, account: null, codex_available: true })
    const { fetchCodexStatus } = await import('./client')
    const result = await fetchCodexStatus()
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/settings/llm/codex/status')
    expect(init.method).toBe('GET')
    expect(result.state).toBe('signed_out')
  })

  test('codexLoginStart POSTs login/start without a body secret', async () => {
    const fetchMock = stubFetch({ ok: true, state: 'login_started' })
    const { codexLoginStart } = await import('./client')
    const result = await codexLoginStart()
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/settings/llm/codex/login/start')
    expect(init.method).toBe('POST')
    const body = String(init.body)
    expect(body).not.toMatch(/token|jwt|authUrl|apiKey/i)
    expect(result.state).toBe('login_started')
  })

  test('fetchCodexModels returns provider-qualified ids', async () => {
    const fetchMock = stubFetch({
      ok: true,
      models: [{ id: 'openai-codex/gpt-5.6-luna', label: 'GPT-5.6 Luna', model: 'gpt-5.6-luna' }],
    })
    const { fetchCodexModels } = await import('./client')
    const result = await fetchCodexModels()
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/settings/llm/codex/models')
    expect(init.method).toBe('GET')
    expect(result.models?.[0]?.id).toBe('openai-codex/gpt-5.6-luna')
  })

  test('codexLogout POSTs logout', async () => {
    const fetchMock = stubFetch({ ok: true, state: 'signed_out' })
    const { codexLogout } = await import('./client')
    const result = await codexLogout()
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/settings/llm/codex/logout')
    expect(init.method).toBe('POST')
    expect(result.ok).toBe(true)
  })
})
