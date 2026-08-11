import { render, screen } from '@testing-library/react'
import { describe, expect, test, vi } from 'vitest'
import type { ReactNode } from 'react'
import { PreviewViewport } from './PreviewViewport'
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
