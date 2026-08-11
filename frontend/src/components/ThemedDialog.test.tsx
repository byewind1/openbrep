import { useState } from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, test } from 'vitest'
import { useThemedDialog } from './ThemedDialog'

/** 把 confirm/prompt 的 resolve 值写到 data-testid，便于断言。 */
function Harness({ mode }: { mode: 'confirm' | 'prompt' }) {
  const { confirm, prompt, dialogNode } = useThemedDialog()
  const [result, setResult] = useState<string>('pending')

  function open() {
    if (mode === 'confirm') {
      void confirm({ title: 'Delete project?', message: 'This cannot be undone.', danger: true }).then((ok) =>
        setResult(`confirmed:${ok}`),
      )
    } else {
      void prompt({ title: 'Project name', defaultValue: 'Shelf' }).then((value) => setResult(`prompted:${String(value)}`))
    }
  }

  return (
    <div>
      <button type="button" onClick={open}>
        open
      </button>
      <span data-testid="result">{result}</span>
      {dialogNode}
    </div>
  )
}

async function expectResult(value: string) {
  await waitFor(() => {
    expect(screen.getByTestId('result').textContent).toBe(value)
  })
}

describe('ThemedDialog confirm', () => {
  test('resolves true when the confirm button is clicked', async () => {
    render(<Harness mode="confirm" />)
    fireEvent.click(screen.getByRole('button', { name: 'open' }))
    fireEvent.click(screen.getByRole('button', { name: '确定' }))

    await expectResult('confirmed:true')
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  test('resolves false when the cancel button is clicked', async () => {
    render(<Harness mode="confirm" />)
    fireEvent.click(screen.getByRole('button', { name: 'open' }))
    fireEvent.click(screen.getByRole('button', { name: '取消' }))

    await expectResult('confirmed:false')
  })

  test('resolves false on Escape', async () => {
    render(<Harness mode="confirm" />)
    fireEvent.click(screen.getByRole('button', { name: 'open' }))
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })

    await expectResult('confirmed:false')
  })

  test('resolves true on Enter', async () => {
    render(<Harness mode="confirm" />)
    fireEvent.click(screen.getByRole('button', { name: 'open' }))
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Enter' })

    await expectResult('confirmed:true')
  })

  test('resolves false when the overlay is clicked, but not when the card is clicked', async () => {
    render(<Harness mode="confirm" />)
    fireEvent.click(screen.getByRole('button', { name: 'open' }))

    // 点卡片内部不关闭（stopPropagation）
    fireEvent.click(screen.getByText('Delete project?'))
    expect(screen.queryByRole('dialog')).not.toBeNull()

    // 点遮罩取消
    const overlay = screen.getByRole('dialog').parentElement as HTMLElement
    fireEvent.click(overlay)
    await expectResult('confirmed:false')
  })

  test('autofocuses the cancel button on a danger confirm', () => {
    render(<Harness mode="confirm" />)
    fireEvent.click(screen.getByRole('button', { name: 'open' }))

    expect((document.activeElement as HTMLButtonElement).textContent).toBe('取消')
  })
})

describe('ThemedDialog prompt', () => {
  test('resolves the entered value on confirm', async () => {
    render(<Harness mode="prompt" />)
    fireEvent.click(screen.getByRole('button', { name: 'open' }))
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Sofa' } })
    fireEvent.click(screen.getByRole('button', { name: '确定' }))

    await expectResult('prompted:Sofa')
  })

  test('resolves the entered value on Enter', async () => {
    render(<Harness mode="prompt" />)
    fireEvent.click(screen.getByRole('button', { name: 'open' }))
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Sofa' } })
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' })

    await expectResult('prompted:Sofa')
  })

  test('resolves null on cancel', async () => {
    render(<Harness mode="prompt" />)
    fireEvent.click(screen.getByRole('button', { name: 'open' }))
    fireEvent.click(screen.getByRole('button', { name: '取消' }))

    await expectResult('prompted:null')
  })

  test('resolves null on Escape', async () => {
    render(<Harness mode="prompt" />)
    fireEvent.click(screen.getByRole('button', { name: 'open' }))
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })

    await expectResult('prompted:null')
  })

  test('empty value is released (not treated as cancel)', async () => {
    render(<Harness mode="prompt" />)
    fireEvent.click(screen.getByRole('button', { name: 'open' }))
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: '确定' }))

    // 空串放行：与 window.prompt 一致，由调用方（如保存项目重命名）自行校验
    await expectResult('prompted:')
  })

  test('prefills the default value', () => {
    render(<Harness mode="prompt" />)
    fireEvent.click(screen.getByRole('button', { name: 'open' }))

    expect((screen.getByRole('textbox') as HTMLInputElement).value).toBe('Shelf')
  })
})
