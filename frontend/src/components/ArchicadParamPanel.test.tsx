import { render, screen, waitFor } from '@testing-library/react'
import { vi } from 'vitest'
import { ArchicadParamPanel } from './ArchicadParamPanel'

vi.mock('../api/client', () => ({
  fetchUiLayout: vi.fn(),
}))

import { fetchUiLayout } from '../api/client'

const fetchUiLayoutMock = fetchUiLayout as unknown as ReturnType<typeof vi.fn>

function baseParams() {
  return [
    {
      name: 'A',
      type: 'Length',
      type_tag: 'Length',
      description: '',
      value: '1.2',
      is_fixed: false,
      options: null,
      range: null,
    },
  ]
}

describe('ArchicadParamPanel (L0b)', () => {
  beforeEach(() => {
    fetchUiLayoutMock.mockReset()
  })

  test('renders infield controls from ui layout payload', async () => {
    fetchUiLayoutMock.mockResolvedValue({
      ok: true,
      title: '休闲沙发',
      width: 480,
      height: 300,
      has_infield: true,
      controls: [
        { type: 'outfield', text: '宽度', x: 20, y: 20, w: 80, h: 16 },
        { type: 'infield', param: 'A', x: 110, y: 20, w: 90, h: 20 },
      ],
      unsupported: [],
      warnings: [],
    })

    render(
      <ArchicadParamPanel
        parameters={baseParams()}
        draftParameters={{}}
        onChange={() => {}}
      />,
    )

    await waitFor(() => {
      expect(screen.getByText('休闲沙发')).toBeTruthy()
    })
    expect(screen.getByText('宽度')).toBeTruthy()
    expect(screen.getByDisplayValue('1.2')).toBeTruthy()
  })

  test('shows fallback when no infield controls', async () => {
    fetchUiLayoutMock.mockResolvedValue({
      ok: true,
      has_infield: false,
      controls: [],
      unsupported: [],
      warnings: [],
    })

    render(
      <ArchicadParamPanel
        parameters={[]}
        draftParameters={{}}
        onChange={() => {}}
      />,
    )

    await waitFor(() => {
      expect(screen.getByText(/没有可渲染的参数输入控件/)).toBeTruthy()
    })
  })
})
