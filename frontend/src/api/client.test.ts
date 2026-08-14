import { afterEach, describe, expect, test, vi } from 'vitest'
import { fetchPreview, fetchPreview2D } from './client'

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
