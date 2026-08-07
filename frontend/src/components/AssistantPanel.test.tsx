import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, test, vi } from 'vitest'
import { AssistantPanel } from './AssistantPanel'

const baseProps = {
  messages: [] as Parameters<typeof AssistantPanel>[0]['messages'],
  busy: false,
  hasProject: false,
  onChat: vi.fn(),
  onStop: vi.fn(),
  onClearHistory: vi.fn(),
  onAdoptCode: vi.fn(),
}

describe('AssistantPanel', () => {
  test('attaches an image and sends via onChat', async () => {
    const onChat = vi.fn()
    const file = new File(['fake image'], 'shelf.png', { type: 'image/png' })

    render(<AssistantPanel {...baseProps} hasProject onChat={onChat} />)

    fireEvent.change(screen.getByLabelText('Attach image'), { target: { files: [file] } })
    await waitFor(() => expect(screen.getByRole('button', { name: 'Remove image shelf.png' })).toBeTruthy())

    fireEvent.change(screen.getByLabelText('Ask or generate'), { target: { value: '按图调整比例' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))

    expect(onChat).toHaveBeenCalledWith(
      '按图调整比例',
      expect.objectContaining({ name: 'shelf.png', mime: 'image/png', b64: expect.any(String) }),
    )
  })

  test('Enter key sends message, Shift+Enter does not', () => {
    const onChat = vi.fn()
    render(<AssistantPanel {...baseProps} onChat={onChat} />)

    const ta = screen.getByLabelText('Ask or generate')
    fireEvent.change(ta, { target: { value: '测试消息' } })
    fireEvent.keyDown(ta, { key: 'Enter', shiftKey: true })
    expect(onChat).not.toHaveBeenCalled()

    fireEvent.keyDown(ta, { key: 'Enter', shiftKey: false })
    expect(onChat).toHaveBeenCalledWith('测试消息', null)
  })

  test('shows stop button when busy and ESC calls onStop', () => {
    const onStop = vi.fn()
    render(<AssistantPanel {...baseProps} busy onStop={onStop} />)

    expect(screen.getByRole('button', { name: '■ 停止' })).toBeTruthy()

    const ta = screen.getByLabelText('Ask or generate')
    fireEvent.keyDown(ta, { key: 'Escape' })
    expect(onStop).toHaveBeenCalledTimes(1)
  })

  test('shows ↩ 重试上次 button when interruptedContext is set', () => {
    const onChat = vi.fn()
    render(
      <AssistantPanel
        {...baseProps}
        onChat={onChat}
        interruptedContext={{ message: '帮我生成一扇门', intent: 'create' }}
      />,
    )
    const resumeBtn = screen.getByRole('button', { name: '↩ 重试上次' })
    fireEvent.click(resumeBtn)
    expect(onChat).toHaveBeenCalledWith('继续')
  })

  test('opens a compact history drawer and adopts code from a history message', () => {
    const onAdoptCode = vi.fn()
    render(
      <AssistantPanel
        {...baseProps}
        messages={[
          { role: 'user', content: '把 3D 改成方块' },
          { role: 'assistant', content: '可以。\n```gdl\nBLOCK A, B, ZZYZX\n```' },
          { role: 'assistant', content: '普通解释，没有代码' },
        ]}
        onAdoptCode={onAdoptCode}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'History' }))
    const drawer = screen.getByRole('dialog', { name: 'Assistant history' })
    expect(within(drawer).getByText('把 3D 改成方块')).toBeTruthy()
    fireEvent.click(within(drawer).getByRole('button', { name: 'Adopt code from message 2' }))
    expect(onAdoptCode).toHaveBeenCalledWith(1)
  })

  test('renders a change summary card with clickable files and save revision', () => {
    const onOpenScript = vi.fn()
    const onSaveRevision = vi.fn()
    render(
      <AssistantPanel
        {...baseProps}
        messages={[
          { role: 'user', content: '把书架加一块层板，并加深背板颜色让它看起来更稳重一些，再调整层板间距保持均匀' },
          { role: 'assistant', content: '已修改。', changedFiles: ['scripts/3d.gdl', 'paramlist.xml'] },
        ]}
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

  test('shows error category badge and interrupted badge', () => {
    render(
      <AssistantPanel
        {...baseProps}
        messages={[
          { role: 'assistant', content: 'LLM settings error: API Key 无效', errorCategory: 'llm' },
          { role: 'assistant', content: '⏹ 已中断', interrupted: true },
        ]}
      />,
    )
    expect(screen.getByText('LLM settings')).toBeTruthy()
    expect(screen.getByText('已中断')).toBeTruthy()
  })
})


describe('AssistantPanel plan confirmation card (V3)', () => {
  test('renders the pending plan with user-visible changes and confirm/cancel buttons', () => {
    const onConfirmPlan = vi.fn()
    render(
      <AssistantPanel
        {...baseProps}
        hasProject
        pendingPlan={{
          intent_summary: '把书架加高',
          user_visible_changes: ['高度默认值从 1 米改为 1.2 米'],
          affected_files: ['paramlist.xml'],
          risk: '会改变默认尺寸',
        }}
        onConfirmPlan={onConfirmPlan}
      />,
    )
    expect(screen.getByText('修改计划')).toBeTruthy()
    expect(screen.getByText('把书架加高')).toBeTruthy()
    expect(screen.getByText('高度默认值从 1 米改为 1.2 米')).toBeTruthy()
    expect(screen.getByText('paramlist.xml')).toBeTruthy()
    expect(screen.getByText(/会改变默认尺寸/)).toBeTruthy()
  })

  test('confirm and cancel buttons call onConfirmPlan with the right flag', () => {
    const onConfirmPlan = vi.fn()
    render(
      <AssistantPanel
        {...baseProps}
        hasProject
        pendingPlan={{
          intent_summary: '把书架加高',
          user_visible_changes: ['高度默认值改为 1.2 米'],
          affected_files: [],
          risk: '无',
        }}
        onConfirmPlan={onConfirmPlan}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: '确认修改' }))
    expect(onConfirmPlan).toHaveBeenCalledWith(true)
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(onConfirmPlan).toHaveBeenCalledWith(false)
  })

  test('buttons are disabled while busy', () => {
    render(
      <AssistantPanel
        {...baseProps}
        busy
        hasProject
        pendingPlan={{
          intent_summary: '把书架加高',
          user_visible_changes: ['高度默认值改为 1.2 米'],
          affected_files: [],
          risk: '无',
        }}
        onConfirmPlan={vi.fn()}
      />,
    )
    expect(screen.getByRole('button', { name: '确认修改' })).toHaveProperty('disabled', true)
    expect(screen.getByRole('button', { name: '取消' })).toHaveProperty('disabled', true)
  })
})
