import { describe, expect, test } from 'vitest'
import type { PreviewPayload } from '../api/types'
import { computePreviewBounds } from './previewCamera'
import {
  avoidSelectionAmber,
  buildPartsView,
  componentColorIdentity,
  filterVisibleMeshes,
  hashColor,
  hashHue,
  hashLightness,
  hashSaturation,
  hashString,
  rawHueFromHash,
} from './previewParts'

function makePreview(meshes: PreviewPayload['meshes']): PreviewPayload {
  return { meshes, wires: [] }
}

describe('hashString (FNV-1a)', () => {
  test('deterministic: same input yields the same hash', () => {
    expect(hashString('BLOCK#0:0')).toBe(hashString('BLOCK#0:0'))
    expect(hashString('front-left-leg')).toBe(hashString('front-left-leg'))
  })

  test('different inputs produce different hashes', () => {
    expect(hashString('BLOCK#0:0')).not.toBe(hashString('BLOCK#0:1'))
    expect(hashString('BLOCK#1:0')).not.toBe(hashString('BLOCK#0:0'))
    expect(hashString('BOX')).not.toBe(hashString('CYLIND'))
  })

  test('returns an unsigned 32-bit integer', () => {
    expect(hashString('x')).toBeGreaterThanOrEqual(0)
    expect(hashString('x')).toBeLessThanOrEqual(0xffffffff)
  })
})

describe('rawHueFromHash + avoidSelectionAmber', () => {
  test('raw hue maps a hash into [0, 360)', () => {
    expect(rawHueFromHash(0)).toBe(0)
    expect(rawHueFromHash(359)).toBe(359)
    expect(rawHueFromHash(360)).toBe(0)
    expect(rawHueFromHash(720 + 42)).toBe(42)
  })

  test('amber avoidance shifts [35,55] by +180 and leaves the rest alone', () => {
    expect(avoidSelectionAmber(35)).toBe(215)
    expect(avoidSelectionAmber(42)).toBe(222)
    expect(avoidSelectionAmber(55)).toBe(235)
    expect(avoidSelectionAmber(30)).toBe(30)
    expect(avoidSelectionAmber(60)).toBe(60)
    expect(avoidSelectionAmber(180)).toBe(180)
    expect(avoidSelectionAmber(215)).toBe(215)
    expect(avoidSelectionAmber(300)).toBe(300)
  })

  test('hashHue never lands near the selection amber (#ffc94d, h≈42°)', () => {
    for (let i = 0; i < 200; i++) {
      const hue = hashHue(`part-${i}`)
      expect(hue).toBeGreaterThanOrEqual(0)
      expect(hue).toBeLessThan(360)
      expect(hue >= 35 && hue <= 55).toBe(false)
    }
  })

  test('hashHue is deterministic (same part keeps its color across refreshes)', () => {
    expect(hashHue('BLOCK#3:0')).toBe(hashHue('BLOCK#3:0'))
  })
})

describe('hashColor HSL ranges', () => {
  test('saturation stays in [40,65] and lightness in [50,70] for many identities', () => {
    for (let i = 0; i < 100; i++) {
      const s = hashSaturation(`p${i}`)
      const l = hashLightness(`p${i}`)
      expect(s).toBeGreaterThanOrEqual(40)
      expect(s).toBeLessThanOrEqual(65)
      expect(l).toBeGreaterThanOrEqual(50)
      expect(l).toBeLessThanOrEqual(70)
    }
  })

  test('returns a css hsl string and is deterministic', () => {
    expect(hashColor('BLOCK#0:0')).toMatch(/^hsl\(\d+, \d+%, \d+%\)$/)
    expect(hashColor('BLOCK#0:0')).toBe(hashColor('BLOCK#0:0'))
  })

  test('more than 12 parts do not collide (full hue domain + s/l variance)', () => {
    const colors = new Set<string>()
    for (let i = 0; i < 30; i++) {
      colors.add(hashColor(`part#${i}:${i}`))
    }
    expect(colors.size).toBe(30)
  })
})

describe('componentColorIdentity', () => {
  test('embeds mesh name, mesh index and comp id so components never share identity', () => {
    expect(componentColorIdentity('BLOCK', 0, 0)).toBe('BLOCK#0:0')
    expect(componentColorIdentity('BLOCK', 1, 0)).toBe('BLOCK#1:0')
    expect(componentColorIdentity('BLOCK', 0, 1)).toBe('BLOCK#0:1')
    expect(componentColorIdentity('RULED_0', 0, 0)).toBe('RULED_0#0:0')
  })
})

describe('buildPartsView (部件列表视图模型)', () => {
  const preview = makePreview([
    {
      name: 'BLOCK',
      vertices: [[0, 0, 0]],
      faces: [],
      source_ref: { script_type: '3d', line: 42, command: 'BLOCK', label: '' },
    },
    { name: 'RULED_0', vertices: [[1, 1, 1]], faces: [] },
  ])

  test('creates one row per mesh with name and source line', () => {
    const parts = buildPartsView(preview, 'random', null, new Set())
    expect(parts).toHaveLength(2)
    expect(parts[0]).toMatchObject({ meshIndex: 0, meshName: 'BLOCK', sourceLine: '3d.gdl:42', visible: true })
    expect(parts[1]).toMatchObject({ meshIndex: 1, meshName: 'RULED_0', sourceLine: null, visible: true })
  })

  test('random mode chip color is the stable hash color of comp-0 identity', () => {
    const parts = buildPartsView(preview, 'random', null, new Set())
    expect(parts[0]?.color).toBe(hashColor(componentColorIdentity('BLOCK', 0, 0)))
    expect(parts[1]?.color).toBe(hashColor(componentColorIdentity('RULED_0', 1, 0)))
  })

  test('flat mode uses the given mode color for every row', () => {
    const parts = buildPartsView(preview, 'flat', '#8595ab', new Set())
    expect(parts.every((part) => part.color === '#8595ab')).toBe(true)
  })

  test('hidden set is reflected in the visible flag', () => {
    const parts = buildPartsView(preview, 'random', null, new Set([1]))
    expect(parts[0]?.visible).toBe(true)
    expect(parts[1]?.visible).toBe(false)
  })

  test('returns [] for a null preview', () => {
    expect(buildPartsView(null, 'random', null, new Set())).toEqual([])
  })
})

describe('filterVisibleMeshes (可见性过滤后的 bounds 输入)', () => {
  test('drops hidden meshes before bounds computation', () => {
    const preview = makePreview([
      { name: 'A', vertices: [[0, 0, 0]], faces: [] },
      { name: 'B', vertices: [[10, 10, 10]], faces: [] },
    ])
    const filtered = filterVisibleMeshes(preview, new Set([1]))
    expect(filtered?.meshes.map((m) => m.name)).toEqual(['A'])
    const bounds = computePreviewBounds(filtered)
    expect(bounds.center).toEqual([0, 0, 0])
    expect(bounds.size).toEqual([0.01, 0.01, 0.01])
  })

  test('bounds include hidden vertices when nothing is filtered out', () => {
    const preview = makePreview([
      { name: 'A', vertices: [[0, 0, 0]], faces: [] },
      { name: 'B', vertices: [[10, 10, 10]], faces: [] },
    ])
    const bounds = computePreviewBounds(filterVisibleMeshes(preview, new Set()))
    expect(bounds.center).toEqual([5, 5, 5])
  })

  test('null preview stays null', () => {
    expect(filterVisibleMeshes(null, new Set())).toBeNull()
  })

  test('hiding every mesh yields an empty payload (bounds fall back to defaults)', () => {
    const preview = makePreview([{ name: 'A', vertices: [[0, 0, 0]], faces: [] }])
    const filtered = filterVisibleMeshes(preview, new Set([0]))
    expect(filtered?.meshes).toEqual([])
    expect(computePreviewBounds(filtered).radius).toBe(2)
  })
})
