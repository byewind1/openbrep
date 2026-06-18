import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, test, vi } from 'vitest'
import { AssistantPanel } from './AssistantPanel'

describe('AssistantPanel', () => {
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
