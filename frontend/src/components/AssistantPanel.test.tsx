import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, test, vi } from 'vitest'
import { AssistantPanel } from './AssistantPanel'

describe('AssistantPanel', () => {
  test('attaches an image to generate requests', async () => {
    const onGenerate = vi.fn()
    const file = new File(['fake image'], 'shelf.png', { type: 'image/png' })

    render(
      <AssistantPanel
        messages={[]}
        busy={false}
        onSend={vi.fn()}
        onCreate={vi.fn()}
        onGenerate={onGenerate}
        onClearHistory={vi.fn()}
        onAdoptCode={vi.fn()}
      />,
    )

    fireEvent.change(screen.getByLabelText('Attach image'), { target: { files: [file] } })

    await waitFor(() => expect(screen.getByRole('button', { name: 'Remove image shelf.png' })).toBeTruthy())

    fireEvent.change(screen.getByPlaceholderText('Ask or generate...'), { target: { value: '按图调整比例' } })
    fireEvent.click(screen.getByRole('button', { name: 'Generate changes' }))

    expect(onGenerate).toHaveBeenCalledWith(
      '按图调整比例',
      expect.objectContaining({
        name: 'shelf.png',
        mime: 'image/png',
        b64: expect.any(String),
      }),
    )
  })

  test('opens a compact history drawer and adopts code from a history message', () => {
    const onAdoptCode = vi.fn()

    render(
      <AssistantPanel
        messages={[
          { role: 'user', content: '把 3D 改成方块' },
          { role: 'assistant', content: '可以。\n```gdl\nBLOCK A, B, ZZYZX\n```' },
          { role: 'assistant', content: '普通解释，没有代码' },
        ]}
        busy={false}
        onSend={vi.fn()}
        onCreate={vi.fn()}
        onGenerate={vi.fn()}
        onClearHistory={vi.fn()}
        onAdoptCode={onAdoptCode}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'History' }))

    const drawer = screen.getByRole('dialog', { name: 'Assistant history' })
    expect(within(drawer).getByText('把 3D 改成方块')).toBeTruthy()
    expect(within(drawer).getByText('普通解释，没有代码')).toBeTruthy()

    fireEvent.click(within(drawer).getByRole('button', { name: 'Adopt code from message 2' }))

    expect(onAdoptCode).toHaveBeenCalledWith(1)
  })
})
