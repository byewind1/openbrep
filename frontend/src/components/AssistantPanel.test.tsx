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

  test('renders a change summary card with clickable files and save revision', () => {
    const onOpenScript = vi.fn()
    const onSaveRevision = vi.fn()

    render(
      <AssistantPanel
        messages={[
          { role: 'user', content: '把书架加一块层板，并加深背板颜色让它看起来更稳重一些，再调整层板间距保持均匀' },
          { role: 'assistant', content: '已修改。', changedFiles: ['scripts/3d.gdl', 'paramlist.xml'] },
        ]}
        busy={false}
        onSend={vi.fn()}
        onCreate={vi.fn()}
        onGenerate={vi.fn()}
        onClearHistory={vi.fn()}
        onAdoptCode={vi.fn()}
        onOpenScript={onOpenScript}
        onSaveRevision={onSaveRevision}
      />,
    )

    expect(screen.getByText('Changed files')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'scripts/3d.gdl' }))
    expect(onOpenScript).toHaveBeenCalledWith('3d.gdl')

    fireEvent.click(screen.getByRole('button', { name: 'Save revision' }))
    expect(onSaveRevision).toHaveBeenCalledTimes(1)
    const revisionMessage = onSaveRevision.mock.calls[0][0] as string
    expect(revisionMessage.startsWith('AI: 把书架加一块层板')).toBe(true)
    expect(revisionMessage.length).toBeLessThanOrEqual(65)
  })

  test('shows an error category badge on failed assistant replies', () => {
    render(
      <AssistantPanel
        messages={[
          { role: 'assistant', content: 'LLM settings error: API Key 无效', errorCategory: 'llm' },
          { role: 'assistant', content: 'Compile failed: missing END', errorCategory: 'compile' },
        ]}
        busy={false}
        onSend={vi.fn()}
        onCreate={vi.fn()}
        onGenerate={vi.fn()}
        onClearHistory={vi.fn()}
        onAdoptCode={vi.fn()}
      />,
    )

    expect(screen.getByText('LLM settings')).toBeTruthy()
    expect(screen.getByText('Compile')).toBeTruthy()
  })
})
