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

  test('history drawer shows an import button, disabled without a project (P6a)', () => {
    render(<AssistantPanel {...baseProps} messages={[{ role: 'user', content: '旧记录' }]} />)
    fireEvent.click(screen.getByRole('button', { name: 'History' }))
    const importBtn = screen.getByRole('button', { name: '导入…' })
    expect((importBtn as HTMLButtonElement).disabled).toBe(true)
  })

  test('history drawer import button is enabled with a project (P6a)', () => {
    render(<AssistantPanel {...baseProps} hasProject messages={[{ role: 'user', content: '旧记录' }]} />)
    fireEvent.click(screen.getByRole('button', { name: 'History' }))
    const importBtn = screen.getByRole('button', { name: '导入…' })
    expect((importBtn as HTMLButtonElement).disabled).toBe(false)
  })

  test('history import picker hints to mount a workspace when workspace is null (P6a)', () => {
    render(<AssistantPanel {...baseProps} hasProject workspace={null} messages={[{ role: 'user', content: '旧记录' }]} />)
    fireEvent.click(screen.getByRole('button', { name: 'History' }))
    fireEvent.click(screen.getByRole('button', { name: '导入…' }))
    expect(screen.getByText('先在工作区面板挂载工作区')).toBeTruthy()
  })

  test('history import picker lists workspace projects excluding the current one (P6a)', () => {
    render(
      <AssistantPanel
        {...baseProps}
        hasProject
        currentProjectPath="/workspace/hsf/Current"
        messages={[{ role: 'user', content: '旧记录' }]}
        workspace={{
          path: '/workspace',
          project_count: 2,
          projects: [
            { name: 'Current', path: '/workspace/hsf/Current', parameter_count: 3, scripts_present: [], latest_revision_id: null, origin: null, artifact_count: 0, active: true },
            { name: 'SourceShelf', path: '/workspace/hsf/SourceShelf', parameter_count: 3, scripts_present: [], latest_revision_id: null, origin: null, artifact_count: 0, active: false },
          ],
        }}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'History' }))
    fireEvent.click(screen.getByRole('button', { name: '导入…' }))
    expect(screen.getByText('SourceShelf')).toBeTruthy()
    expect(screen.queryByText('Current')).toBeNull()
  })

  test('confirming an import source calls onImportAssistantHistory (P6a)', async () => {
    const onImportAssistantHistory = vi.fn()
    render(
      <AssistantPanel
        {...baseProps}
        hasProject
        currentProjectPath="/workspace/hsf/Current"
        messages={[{ role: 'user', content: '旧记录' }]}
        onImportAssistantHistory={onImportAssistantHistory}
        workspace={{
          path: '/workspace',
          project_count: 1,
          projects: [
            { name: 'SourceShelf', path: '/workspace/hsf/SourceShelf', parameter_count: 3, scripts_present: [], latest_revision_id: null, origin: null, artifact_count: 0, active: false },
          ],
        }}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'History' }))
    fireEvent.click(screen.getByRole('button', { name: '导入…' }))
    fireEvent.click(screen.getByRole('button', { name: '从项目 SourceShelf 导入' }))

    // 主题化确认弹窗
    const dialog = await screen.findByRole('dialog', { name: '导入聊天记录' })
    expect(dialog.textContent).toContain('SourceShelf')
    fireEvent.click(within(dialog).getByRole('button', { name: '确认导入' }))

    await waitFor(() => expect(onImportAssistantHistory).toHaveBeenCalledWith('/workspace/hsf/SourceShelf'))
  })

  test('cancelling the import confirm does not call onImportAssistantHistory (P6a)', async () => {
    const onImportAssistantHistory = vi.fn()
    render(
      <AssistantPanel
        {...baseProps}
        hasProject
        currentProjectPath="/workspace/hsf/Current"
        messages={[{ role: 'user', content: '旧记录' }]}
        onImportAssistantHistory={onImportAssistantHistory}
        workspace={{
          path: '/workspace',
          project_count: 1,
          projects: [
            { name: 'SourceShelf', path: '/workspace/hsf/SourceShelf', parameter_count: 3, scripts_present: [], latest_revision_id: null, origin: null, artifact_count: 0, active: false },
          ],
        }}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'History' }))
    fireEvent.click(screen.getByRole('button', { name: '导入…' }))
    fireEvent.click(screen.getByRole('button', { name: '从项目 SourceShelf 导入' }))

    const dialog = await screen.findByRole('dialog', { name: '导入聊天记录' })
    fireEvent.click(within(dialog).getByRole('button', { name: '取消' }))

    expect(onImportAssistantHistory).not.toHaveBeenCalled()
  })

  test('history drawer distill button is disabled without a project (P6b)', () => {
    render(<AssistantPanel {...baseProps} messages={[{ role: 'user', content: '旧记录' }]} />)
    fireEvent.click(screen.getByRole('button', { name: 'History' }))
    const distillBtn = screen.getByRole('button', { name: '整理成指令' })
    expect((distillBtn as HTMLButtonElement).disabled).toBe(true)
  })

  test('history drawer is unreachable without messages, so distill stays disabled (P6b)', () => {
    render(<AssistantPanel {...baseProps} hasProject messages={[]} />)
    const historyBtn = screen.getByRole('button', { name: 'History' })
    expect((historyBtn as HTMLButtonElement).disabled).toBe(true)
    // 无记录时抽屉不可打开，无历史可整理（后端空记录也会报错）
    expect(screen.queryByRole('dialog', { name: 'Assistant history' })).toBeNull()
  })

  test('history drawer distill button is enabled with a project and messages (P6b)', () => {
    render(<AssistantPanel {...baseProps} hasProject messages={[{ role: 'user', content: '旧记录' }]} />)
    fireEvent.click(screen.getByRole('button', { name: 'History' }))
    const distillBtn = screen.getByRole('button', { name: '整理成指令' })
    expect((distillBtn as HTMLButtonElement).disabled).toBe(false)
  })

  test('distill click calls the handler, fills the draft seed and never auto-sends (P6b)', async () => {
    const onChat = vi.fn()
    const onDistill = vi.fn(async () => {})
    const onConsume = vi.fn()
    const { rerender } = render(
      <AssistantPanel
        {...baseProps}
        hasProject
        onChat={onChat}
        onDistillAssistantHistory={onDistill}
        onConsumeDraftSeed={onConsume}
        messages={[{ role: 'user', content: '把书架层板数改成 5' }]}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'History' }))
    fireEvent.click(screen.getByRole('button', { name: '整理成指令' }))
    expect(onDistill).toHaveBeenCalledTimes(1)
    // 整理完成：seed 到达 → 填入输入框草稿、关抽屉、消费 seed
    rerender(
      <AssistantPanel
        {...baseProps}
        hasProject
        onChat={onChat}
        onDistillAssistantHistory={onDistill}
        onConsumeDraftSeed={onConsume}
        draftSeed="把书架层板数改成 5，保留 3D 代码"
        messages={[{ role: 'user', content: '把书架层板数改成 5' }]}
      />,
    )
    const ta = screen.getByLabelText('Ask or generate') as HTMLTextAreaElement
    expect(ta.value).toBe('把书架层板数改成 5，保留 3D 代码')
    expect(onConsume).toHaveBeenCalledTimes(1)
    // 绝不自动发送
    expect(onChat).not.toHaveBeenCalled()
    // 抽屉已关闭
    expect(screen.queryByRole('dialog', { name: 'Assistant history' })).toBeNull()
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
            summary_lines: ['当前参数下几何未变化'],
            geometry_delta: { status: 'unchanged' },
            checks: [{ name: 'compile', status: 'pass', detail: '编译通过' }],
          },
        }]}
      />,
    )
    // HF3：几何未变文案中性化（不得读成“修改失败”）；无对比表仍不渲染
    expect(screen.getByText('当前参数下几何未变化')).toBeTruthy()
    expect(screen.queryByText('修改前')).toBeNull()
  })
})

describe('AssistantPanel vision extraction card (P5d-1)', () => {
  const extraction = {
    token: '图1',
    schema_name: 'lattice_window',
    fields: {
      opening_shape: 'rect',
      pattern_family: '冰裂',
      grid_topology: { kind: 'grid', rows: 4, cols: 4 },
      bar_width_ratio: 0.08,
    },
    confidence: {
      opening_shape: 'high',
      pattern_family: 'high',
      'grid_topology.rows': 'low',
      bar_width_ratio: 'low',
    },
    corrections: [
      // 顶层字段精确修正 → 旧值→新值
      { field: 'pattern_family', old: '冰裂', new: '海棠', evidence: '图中为海棠纹' },
      // 嵌套字段修正 → 行内标记
      { field: 'grid_topology.rows', old: 4, new: 3, evidence: '图中 3 行' },
    ],
    degraded: false,
    critic_degraded: false,
    raw_description: '',
    sha256: 'aa'.repeat(32),
  }

  test('renders schema name, field table, low-confidence highlight and correction old→new', () => {
    render(
      <AssistantPanel
        {...baseProps}
        hasProject
        messages={[{ role: 'assistant', content: '已根据参考图创建。', visionExtractions: [extraction] }]}
      />,
    )
    // 卡片标题 + schema 名
    expect(screen.getByText('读图提取')).toBeTruthy()
    expect(screen.getByText('图1 · lattice_window')).toBeTruthy()
    // 字段表
    expect(screen.getByText('opening_shape')).toBeTruthy()
    expect(screen.getByText('rect')).toBeTruthy()
    // 顶层字段精确修正：旧值→新值
    expect(screen.getByText('冰裂 → 海棠')).toBeTruthy()
    // 嵌套字段修正 → 行内标记
    expect(screen.getByText('grid_topology.rows 4→3')).toBeTruthy()
    // 顶层低置信字段：琥珀色行 + 低置信 badge
    const lowRow = screen.getByText('bar_width_ratio').closest('tr')
    expect(lowRow?.className).toContain('is-low-confidence')
    expect(screen.getByText('低置信')).toBeTruthy()
    // 嵌套低置信 → 行内标记（低置信：grid_topology.rows）
    expect(screen.getByText('低置信：grid_topology.rows')).toBeTruthy()
    // 非低置信字段不标
    expect(screen.queryByText('低置信：opening_shape')).toBeNull()
  })

  test('renders degraded markers for extraction failure and critic failure', () => {
    render(
      <AssistantPanel
        {...baseProps}
        hasProject
        messages={[
          {
            role: 'assistant',
            content: '已创建。',
            visionExtractions: [
              { ...extraction, token: '图1', degraded: true, fields: {}, raw_description: '原始分析文本' },
              { ...extraction, token: '图2', degraded: false, critic_degraded: true, fields: {} },
            ],
          },
        ]}
      />,
    )
    expect(screen.getByText(/【分析失败已降级】/)).toBeTruthy()
    expect(screen.getByText(/【critic 校验已降级】/)).toBeTruthy()
    // 降级图无字段时退回 raw_description 展示
    expect(screen.getByText('原始分析文本')).toBeTruthy()
  })

  test('skips skipped entries and renders nothing', () => {
    render(
      <AssistantPanel
        {...baseProps}
        hasProject
        messages={[
          { role: 'assistant', content: '已创建。', visionExtractions: [{ token: '图3', skipped: true }] },
        ]}
      />,
    )
    expect(screen.queryByText('读图提取')).toBeNull()
  })
})

describe('AssistantPanel editable extraction gate (P5d-2)', () => {
  const extraction = {
    token: '图1',
    schema_name: 'lattice_window',
    fields: {
      opening_shape: 'rect',
      pattern_family: '冰裂',
      grid_topology: { kind: 'grid', rows: 4, cols: 4, cell_desc: '方冰裂单元' },
      bar_width_ratio: 0.08,
      symmetry_group: 'd4',
    },
    confidence: {
      opening_shape: 'high',
      'grid_topology.rows': 'low',
      bar_width_ratio: 'low',
    },
    corrections: [
      // 嵌套 critic 修正：旧值→新值 展示
      { field: 'grid_topology.rows', old: 4, new: 3, evidence: '图中 3 行' },
    ],
    required: ['opening_shape', 'pattern_family', 'grid_topology'],
    critic_checks: ['grid_topology.rows', 'grid_topology.cols', 'symmetry_group'],
    degraded: false,
    critic_degraded: false,
    raw_description: '',
    sha256: 'aa'.repeat(32),
  }

  function renderGate(onConfirmExtraction = vi.fn(), onCancel = vi.fn()) {
    render(
      <AssistantPanel
        {...baseProps}
        hasProject
        pendingExtraction={{ extractions: [extraction], message: '这是漏窗', images: [] }}
        onConfirmExtraction={(extractions, approve) =>
          approve ? onConfirmExtraction(extractions) : onCancel()
        }
      />,
    )
    return { onConfirmExtraction, onCancel }
  }

  test('renders editable inputs for required + critic_checks fields, read-only for the rest', () => {
    renderGate()
    expect(screen.getByText('读图结果确认')).toBeTruthy()
    // required 顶层字段 → 输入框
    expect(screen.getByLabelText('opening_shape')).toBeTruthy()
    expect(screen.getByLabelText('pattern_family')).toBeTruthy()
    // critic_checks 嵌套路径 → 输入框
    expect(screen.getByLabelText('grid_topology.rows')).toBeTruthy()
    expect(screen.getByLabelText('grid_topology.cols')).toBeTruthy()
    // critic_checks 顶层字段 → 输入框
    expect(screen.getByLabelText('symmetry_group')).toBeTruthy()
    // 非可编辑字段（bar_width_ratio）只读展示，无输入框
    expect(screen.queryByLabelText('bar_width_ratio')).toBeNull()
    expect(screen.getByText('0.08')).toBeTruthy()
    // 低置信嵌套路径输入框带低置信标注（低置信出现多处：嵌套输入框标注 + 只读行 badge）
    expect(screen.getAllByText(/低置信/).length).toBeGreaterThan(0)
    // critic 修正展示（嵌套行内）
    expect(screen.getByText('grid_topology.rows 4→3')).toBeTruthy()
  })

  test('confirm sends edited extractions with number parsing and preserved untouched fields', () => {
    const { onConfirmExtraction } = renderGate()
    fireEvent.change(screen.getByLabelText('opening_shape'), { target: { value: 'circle' } })
    // 原值是数字 4 → 数字解析为 5
    fireEvent.change(screen.getByLabelText('grid_topology.rows'), { target: { value: '5' } })
    fireEvent.click(screen.getByRole('button', { name: '确认并生成' }))

    expect(onConfirmExtraction).toHaveBeenCalledTimes(1)
    const payload = onConfirmExtraction.mock.calls[0][0] as typeof extraction[]
    expect(payload).toHaveLength(1)
    expect(payload[0].fields.opening_shape).toBe('circle')
    expect(payload[0].fields.pattern_family).toBe('冰裂') // 未编辑保持原值
    expect(payload[0].fields.grid_topology).toEqual({
      kind: 'grid',
      rows: 5, // 数字解析
      cols: 4,
      cell_desc: '方冰裂单元',
    })
    // schema 元数据原样透传（后端 from_dict 依赖）
    expect(payload[0].required).toEqual(['opening_shape', 'pattern_family', 'grid_topology'])
    expect(payload[0].critic_checks).toEqual(['grid_topology.rows', 'grid_topology.cols', 'symmetry_group'])
  })

  test('cancel clears without generating', () => {
    const { onCancel } = renderGate()
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(onCancel).toHaveBeenCalledTimes(1)
  })
})
