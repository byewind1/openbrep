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
    await waitFor(() => expect(screen.getByRole('button', { name: 'Remove image 图1' })).toBeTruthy())

    fireEvent.change(screen.getByLabelText('Ask or generate'), { target: { value: '按图调整比例[图1]' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))

    expect(onChat).toHaveBeenCalledWith(
      '按图调整比例[图1]',
      [expect.objectContaining({ name: 'shelf.png', mime: 'image/png', b64: expect.any(String) })],
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
    expect(onChat).toHaveBeenCalledWith('测试消息', [])
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


describe('AssistantPanel multi-image intake (P5a)', () => {
  test('paste image data appends [图1] token and renders a chip', async () => {
    const onChat = vi.fn()
    render(<AssistantPanel {...baseProps} onChat={onChat} />)

    const ta = screen.getByLabelText('Ask or generate') as HTMLTextAreaElement
    const file = new File(['png-bytes'], 'pasted.png', { type: 'image/png' })
    fireEvent.paste(ta, {
      clipboardData: { items: [{ kind: 'file', type: 'image/png', getAsFile: () => file }] },
    })
    await waitFor(() => expect(screen.getByText(/图1: pasted.png/)).toBeTruthy())
    expect(ta.value).toContain('[图1]')
  })

  test('drag-and-drop files append ordered tokens and chips', async () => {
    const onChat = vi.fn()
    render(<AssistantPanel {...baseProps} onChat={onChat} />)

    const form = screen.getByRole('form')
    const files = [
      new File(['a'], 'a.png', { type: 'image/png' }),
      new File(['b'], 'b.jpg', { type: 'image/jpeg' }),
    ]
    fireEvent.drop(form, { dataTransfer: { files } })

    await waitFor(() => expect(screen.getByText(/图1: a.png/)).toBeTruthy())
    expect(screen.getByText(/图2: b.jpg/)).toBeTruthy()
    const ta = screen.getByLabelText('Ask or generate') as HTMLTextAreaElement
    expect(ta.value).toBe('[图1][图2]')
  })

  test('pasting a local absolute path attaches a path chip', async () => {
    const onChat = vi.fn()
    render(<AssistantPanel {...baseProps} onChat={onChat} />)

    const ta = screen.getByLabelText('Ask or generate') as HTMLTextAreaElement
    fireEvent.paste(ta, {
      clipboardData: { getData: () => '/Users/ren/pic.jpg', items: [] },
    })
    await waitFor(() => expect(screen.getByText(/图1: \/Users\/ren\/pic\.jpg/)).toBeTruthy())
    expect(ta.value).toContain('[图1]')
  })

  test('typing a local absolute path converts to a path chip', async () => {
    const onChat = vi.fn()
    render(<AssistantPanel {...baseProps} onChat={onChat} />)

    const ta = screen.getByLabelText('Ask or generate') as HTMLTextAreaElement
    fireEvent.change(ta, { target: { value: '/Users/ren/pic.jpg' } })
    await waitFor(() => expect(screen.getByText(/图1: \/Users\/ren\/pic\.jpg/)).toBeTruthy())
    expect(ta.value).not.toContain('/Users/ren/pic.jpg')
    expect(ta.value).toContain('[图1]')
  })

  test('rejects more than 4 attachments', async () => {
    const onChat = vi.fn()
    render(<AssistantPanel {...baseProps} onChat={onChat} />)

    const ta = screen.getByLabelText('Ask or generate') as HTMLTextAreaElement
    const files = Array.from({ length: 5 }, (_, i) => new File([`${i}`], `f${i}.png`, { type: 'image/png' }))
    fireEvent.change(screen.getByLabelText('Attach image'), { target: { files } })

    await waitFor(() => expect(screen.getByText(/最多 4 张图片（已有 0 张，最多再添 4 张）。/)).toBeTruthy())
    expect(ta.value).not.toContain('[图5]')
  })

  test('removing a chip strips its [图N] token from the draft', async () => {
    const onChat = vi.fn()
    render(<AssistantPanel {...baseProps} onChat={onChat} />)

    const ta = screen.getByLabelText('Ask or generate') as HTMLTextAreaElement
    const files = [
      new File(['a'], 'a.png', { type: 'image/png' }),
      new File(['b'], 'b.png', { type: 'image/png' }),
    ]
    fireEvent.change(screen.getByLabelText('Attach image'), { target: { files } })
    await waitFor(() => expect(screen.getByText(/图2: b.png/)).toBeTruthy())
    expect(ta.value).toBe('[图1][图2]')

    fireEvent.click(screen.getByRole('button', { name: 'Remove image 图1' }))
    expect(ta.value).toBe('[图2]')
    expect(screen.queryByText(/图1: a.png/)).toBeNull()
  })

  test('send payload keeps attach order', async () => {
    const onChat = vi.fn()
    render(<AssistantPanel {...baseProps} onChat={onChat} />)

    const files = [
      new File(['a'], 'first.png', { type: 'image/png' }),
      new File(['b'], 'second.png', { type: 'image/png' }),
    ]
    fireEvent.change(screen.getByLabelText('Attach image'), { target: { files } })
    await waitFor(() => expect(screen.getByText(/图2: second.png/)).toBeTruthy())

    fireEvent.change(screen.getByLabelText('Ask or generate'), { target: { value: '[图1][图2] 按图2的纹样做' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))

    const [sentMessage, sentImages] = onChat.mock.calls[0] as [string, Array<{ name: string }>]
    expect(sentMessage).toContain('[图1]')
    expect(sentMessage).toContain('[图2]')
    expect(sentImages.map((img) => img.name)).toEqual(['first.png', 'second.png'])
  })

  test('renders read-only thumbnail chips on user messages with images', () => {
    render(
      <AssistantPanel
        {...baseProps}
        messages={[
          {
            role: 'user',
            content: '按图2的纹样做[图1][图2]',
            images: [
              { name: 'outline.png', mime: 'image/png', b64: 'aGVsbG8=' },
              { name: '/Users/ren/pattern.jpg', mime: '', b64: '', path: '/Users/ren/pattern.jpg' },
            ],
          },
        ]}
      />,
    )
    expect(screen.getByText('outline.png')).toBeTruthy()
    expect(screen.getByText('/Users/ren/pattern.jpg')).toBeTruthy()
    expect(screen.getByAltText('outline.png')).toBeTruthy()
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


describe('AssistantPanel skill proposal card (P2-d)', () => {
  const proposal = {
    name: 'shelf_loop_pattern',
    pattern_type: 'shelf_loop',
    content: '## 适用场景 / When to Use\n层板循环对象。\n\n## 写法要点\n- FOR 循环 + ADD/DEL 配对。',
    evidence: {
      intent: 'MODIFY',
      changed_files: ['scripts/3d.gdl'],
      project: 'Shelf',
    },
  }

  test('renders name, pattern_type, content and evidence', () => {
    render(<AssistantPanel {...baseProps} hasProject pendingSkillProposal={proposal} onConfirmSkillProposal={vi.fn()} />)
    expect(screen.getByText('沉淀为 skill 提案')).toBeTruthy()
    expect(screen.getByText('shelf_loop_pattern')).toBeTruthy()
    expect(screen.getByText('shelf_loop')).toBeTruthy()
    expect(screen.getByText(/层板循环对象/)).toBeTruthy()
    expect(screen.getByText(/Shelf/)).toBeTruthy()
    expect(screen.getByText('scripts/3d.gdl')).toBeTruthy()
  })

  test('approve and ignore buttons call onConfirmSkillProposal with the right flag', () => {
    const onConfirm = vi.fn()
    render(<AssistantPanel {...baseProps} hasProject pendingSkillProposal={proposal} onConfirmSkillProposal={onConfirm} />)
    fireEvent.click(screen.getByRole('button', { name: '批准沉淀' }))
    expect(onConfirm).toHaveBeenCalledWith(true)
    fireEvent.click(screen.getByRole('button', { name: '忽略' }))
    expect(onConfirm).toHaveBeenCalledWith(false)
  })

  test('buttons are disabled while busy', () => {
    render(
      <AssistantPanel
        {...baseProps}
        busy
        hasProject
        pendingSkillProposal={proposal}
        onConfirmSkillProposal={vi.fn()}
      />,
    )
    expect(screen.getByRole('button', { name: '批准沉淀' })).toHaveProperty('disabled', true)
    expect(screen.getByRole('button', { name: '忽略' })).toHaveProperty('disabled', true)
  })

  test('renders without evidence section when evidence is absent', () => {
    render(
      <AssistantPanel
        {...baseProps}
        hasProject
        pendingSkillProposal={{ name: 'n', pattern_type: 'panel', content: '## 适用场景 / When to Use\n正文。' }}
        onConfirmSkillProposal={vi.fn()}
      />,
    )
    expect(screen.getByText('n')).toBeTruthy()
  })
})


describe('AssistantPanel empty state (P4-C)', () => {
  test('shows guidance and example chips when there are no messages', () => {
    render(<AssistantPanel {...baseProps} />)

    expect(screen.getByText('开始你的 GDL 工作流')).toBeTruthy()
    expect(screen.getByText(/用自然语言生成或修改 Archicad 构件/)).toBeTruthy()
    expect(screen.getByRole('button', { name: '生成一个参数化书架' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '把层板数改成 5' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '解释这段 GDL 代码是什么意思' })).toBeTruthy()
  })

  test('clicking an example chip fills the draft without sending', () => {
    const onChat = vi.fn()
    render(<AssistantPanel {...baseProps} onChat={onChat} />)

    fireEvent.click(screen.getByRole('button', { name: '生成一个参数化书架' }))

    const textarea = screen.getByLabelText('Ask or generate') as HTMLTextAreaElement
    expect(textarea.value).toBe('生成一个参数化书架')
    expect(onChat).not.toHaveBeenCalled()
  })

  test('does not show empty guidance once messages exist', () => {
    render(<AssistantPanel {...baseProps} messages={[{ role: 'user', content: 'hi' }]} />)

    expect(screen.queryByText('开始你的 GDL 工作流')).toBeNull()
  })
})


describe('AssistantPanel acceptance report card (V5)', () => {
  const acceptance = {
    summary_lines: ['参数 shelf_count 从 2 改为 5', '几何体数量从 1 变为 2'],
    geometry_delta: {
      status: 'ok' as const,
      reason: '',
      mesh_count: { from: 1, to: 2 },
      bbox_size: { from: [1, 0.4, 1.8], to: [1, 0.4, 2] },
    },
    checks: [
      { name: 'compile', status: 'pass', detail: '编译通过' },
      { name: 'semantic', status: 'fail', detail: '1 个阻塞问题' },
    ],
  }

  test('renders summary lines, before/after geometry comparison and checks', () => {
    render(
      <AssistantPanel
        {...baseProps}
        hasProject
        messages={[{ role: 'assistant', content: '改好了。', acceptance }]}
      />,
    )
    expect(screen.getByText('验收报告')).toBeTruthy()
    expect(screen.getByText('参数 shelf_count 从 2 改为 5')).toBeTruthy()
    expect(screen.getByText('几何体数量从 1 变为 2')).toBeTruthy()
    // 前后对比表
    expect(screen.getByText('修改前')).toBeTruthy()
    expect(screen.getByText('修改后')).toBeTruthy()
    expect(screen.getByText('1×0.4×1.8')).toBeTruthy()
    expect(screen.getByText('1×0.4×2')).toBeTruthy()
    // checks
    expect(screen.getByText('编译通过')).toBeTruthy()
    expect(screen.getByText('1 个阻塞问题')).toBeTruthy()
  })

  test('does not render geometry table when no computable delta', () => {
    render(
      <AssistantPanel
        {...baseProps}
        hasProject
        messages={[{
          role: 'assistant',
          content: '改好了。',
          acceptance: {
            summary_lines: ['几何未发生变化'],
            geometry_delta: { status: 'unchanged' },
            checks: [{ name: 'compile', status: 'pass', detail: '编译通过' }],
          },
        }]}
      />,
    )
    expect(screen.getByText('几何未发生变化')).toBeTruthy()
    expect(screen.queryByText('修改前')).toBeNull()
  })
})
