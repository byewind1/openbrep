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
