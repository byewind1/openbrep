import { render, screen } from '@testing-library/react'
import { describe, expect, test, vi } from 'vitest'
import type { ReactNode } from 'react'
import { PreviewViewport } from './PreviewViewport'
import type { PreviewSourceControl } from './PreviewViewport'
import type { PreviewPayload } from '../api/types'

// jsdom 无 WebGL：把 r3f/drei 换成假实现，three 只打 PMREMGenerator（环境光贴图
// 需要真实 gl 上下文）。其余 three 对象（BufferGeometry/ShaderMaterial 等）
// 保持真实——它们不依赖 WebGL 渲染器。
vi.mock('@react-three/fiber', () => ({
  Canvas: ({ children }: { children?: ReactNode }) => <div data-testid="r3f-canvas">{children}</div>,
  useThree: () => {
    const position = { copy: () => position, add: () => position }
    return {
      camera: {
        up: { copy: () => {} },
        position,
        lookAt: () => {},
        zoom: 1,
        fov: 38,
        near: 1,
        far: 1000,
        updateProjectionMatrix: () => {},
      },
      gl: {},
      scene: { environment: null },
      size: { width: 800, height: 600 },
    }
  },
}))

vi.mock('@react-three/drei', () => ({
  ContactShadows: () => null,
  Edges: () => null,
  GizmoHelper: () => null,
  GizmoViewport: () => null,
  OrbitControls: () => null,
  OrthographicCamera: () => null,
  PerspectiveCamera: () => null,
}))

vi.mock('three', async (importOriginal) => {
  const actual = await importOriginal<typeof import('three')>()
  return {
    ...actual,
    PMREMGenerator: class {
      fromScene() {
        return { texture: { dispose: () => {} } }
      }
      dispose() {}
    },
  }
})

function makePreview(): PreviewPayload {
  return {
    meshes: [{ name: 'shelf', vertices: [[0, 0, 0], [1, 0, 0], [0, 1, 0]], faces: [[0, 1, 2]] }],
    wires: [],
  }
}

describe('PreviewViewport empty state (P4-C)', () => {
  test('shows the empty overlay with guidance when preview is null', () => {
    render(<PreviewViewport preview={null} warnings={[]} />)

    expect(screen.getByText('还没有可预览的模型')).toBeTruthy()
    expect(screen.getByText(/打开\/新建项目，或在 AI 面板描述一个构件/)).toBeTruthy()
  })

  test('shows the empty overlay when preview has no meshes', () => {
    render(<PreviewViewport preview={{ meshes: [], wires: [] }} warnings={[]} />)

    expect(screen.getByText('还没有可预览的模型')).toBeTruthy()
  })

  test('hides geometry-only toolbar controls while empty', () => {
    render(<PreviewViewport preview={null} warnings={[]} />)

    expect(screen.queryByText('剖切')).toBeNull()
    expect(screen.queryByText('对比')).toBeNull()
    expect(screen.queryByText('爆炸')).toBeNull()
    expect(screen.queryByText('部件')).toBeNull()
    // 视角/渲染类控件保留
    expect(screen.getByText('Fit')).toBeTruthy()
  })

  test('hides the overlay and restores geometry controls when meshes exist', () => {
    render(<PreviewViewport preview={makePreview()} warnings={[]} />)

    expect(screen.queryByText('还没有可预览的模型')).toBeNull()
    expect(screen.getByText('爆炸')).toBeTruthy()
    expect(screen.getByText('剖切')).toBeTruthy()
  })
})

function makeSourceControl(overrides: Partial<PreviewSourceControl> = {}): PreviewSourceControl {
  return {
    active: false,
    loading: false,
    error: null,
    stale: false,
    showingAuthoritative: false,
    onModeChange: () => {},
    onRefresh: () => {},
    ...overrides,
  }
}

describe('PreviewViewport authoritative source (Archicad 权威预览)', () => {
  test('does not render source controls without the sourceControl prop (pure local mode)', () => {
    render(<PreviewViewport preview={makePreview()} warnings={[]} />)

    expect(screen.queryByText('本地')).toBeNull()
    expect(screen.queryByText('AC权威')).toBeNull()
    expect(screen.getByText('Approximate preview · verify in Archicad')).toBeTruthy()
  })

  test('marks local mode active by default and shows no authoritative badge', () => {
    render(<PreviewViewport preview={makePreview()} warnings={[]} sourceControl={makeSourceControl()} />)

    expect(screen.getByText('本地').className).toContain('active')
    expect(screen.queryByText('Archicad 权威')).toBeNull()
    expect(screen.queryByText('刷新权威')).toBeNull()
  })

  test('switches modes through onModeChange', () => {
    const onModeChange = vi.fn()
    render(
      <PreviewViewport preview={makePreview()} warnings={[]} sourceControl={makeSourceControl({ onModeChange })} />,
    )

    screen.getByText('AC权威').click()
    expect(onModeChange).toHaveBeenCalledWith('authoritative')
  })

  test('shows the Archicad 权威 badge and refresh button in authoritative mode', () => {
    const onRefresh = vi.fn()
    render(
      <PreviewViewport
        preview={makePreview()}
        warnings={[]}
        sourceControl={makeSourceControl({ active: true, showingAuthoritative: true, onRefresh })}
      />,
    )

    expect(screen.getByText('Archicad 权威')).toBeTruthy()
    expect(screen.getByText('Archicad 权威预览（由 Archicad 渲染）')).toBeTruthy()
    expect(screen.queryByText('Approximate preview · verify in Archicad')).toBeNull()

    screen.getByText('刷新权威').click()
    expect(onRefresh).toHaveBeenCalledTimes(1)
  })

  test('shows the stale hint when parameters changed after the last fetch', () => {
    render(
      <PreviewViewport
        preview={makePreview()}
        warnings={[]}
        sourceControl={makeSourceControl({ active: true, showingAuthoritative: true, stale: true })}
      />,
    )

    expect(screen.getByText('参数已变，点击「刷新权威」更新')).toBeTruthy()
  })

  test('surfaces the fetch error and drops the authoritative badge on failure', () => {
    render(
      <PreviewViewport
        preview={makePreview()}
        warnings={[]}
        sourceControl={makeSourceControl({ active: true, error: 'Archicad 未连接', showingAuthoritative: false })}
      />,
    )

    expect(screen.getByRole('alert').textContent).toContain('Archicad 未连接')
    expect(screen.queryByText('Archicad 权威')).toBeNull()
  })
})
