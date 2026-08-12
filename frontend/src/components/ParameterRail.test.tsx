import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, test, vi } from 'vitest'
import { ParameterRail } from './ParameterRail'
import type { WorkbenchParameter } from '../api/types'

// 参数面板「参数脚本」tab 内嵌的 Monaco 编辑器：测试环境 mock 掉
// （与 PreviewWorkspaceStage.test 同款做法，避免引入 monaco/jsdom 依赖）。
vi.mock('./ScriptEditor', () => ({
  ScriptEditor: () => <div data-testid="mock-script-editor" />,
}))

function makeParam(overrides: Partial<WorkbenchParameter> = {}): WorkbenchParameter {
  return {
    name: 'pattern_type',
    type_tag: 'String',
    description: '纹样',
    value: '直棂',
    is_fixed: false,
    ...overrides,
  }
}

function baseProps(overrides: Partial<Parameters<typeof ParameterRail>[0]> = {}) {
  return {
    title: '参数',
    parameterIssues: [],
    draftParameters: {},
    onChange: vi.fn(),
    onApply: vi.fn(),
    onReset: vi.fn(),
    onAddParameter: vi.fn(async () => true),
    onUpdateParameter: vi.fn(async () => true),
    onDeleteParameter: vi.fn(async () => true),
    onValidateParameters: vi.fn(),
    applying: false,
    ...overrides,
  }
}

// 参数 rail 里还有元数据编辑器的类型下拉（同为 combobox），
// 参数控件下拉统一用 .enum-select 精确定位。
function enumSelect(): HTMLSelectElement {
  const el = document.querySelector('.enum-select')
  if (!el) throw new Error('enum select not found')
  return el as HTMLSelectElement
}

describe('ParameterRail enum dropdown (P11)', () => {
  test('renders a select with VALUES options instead of a free text input', () => {
    const params = [makeParam({ options: ['直棂', '井字', '菱花'] })]
    render(<ParameterRail {...baseProps({ parameters: params })} />)

    const select = enumSelect()
    expect(select).toBeTruthy()
    // 自由文本框不再出现
    expect(document.querySelector('.text-input')).toBeNull()
    const options = Array.from(select.options).map((option) => option.textContent)
    expect(options).toEqual(['直棂', '井字', '菱花'])
    // 当前值在枚举内 → 下拉选中当前值，无兜底项
    expect(select.value).toBe('直棂')
  })

  test('selecting an option drafts its raw value', () => {
    const params = [makeParam({ options: ['直棂', '井字', '菱花'] })]
    const props = baseProps({ parameters: params })
    render(<ParameterRail {...props} />)

    fireEvent.change(enumSelect(), { target: { value: '井字' } })

    expect(props.onChange).toHaveBeenCalledWith('pattern_type', '井字')
  })

  test('current value outside VALUES list shows fallback option and keeps the value', () => {
    const params = [makeParam({ value: '拼音笔误', options: ['直棂', '井字', '菱花'] })]
    const props = baseProps({ parameters: params })
    render(<ParameterRail {...props} />)

    const select = enumSelect()
    expect(select.value).toBe('__not_in_values__')
    expect(screen.getByText('当前值：拼音笔误（不在 VALUES 列表）')).toBeTruthy()
    // 选中兜底项本身不触发 onChange（不静默改写）
    fireEvent.change(select, { target: { value: '__not_in_values__' } })
    expect(props.onChange).not.toHaveBeenCalled()
  })

  test('numeric enum renders a select with numeric options', () => {
    const params = [makeParam({ name: 'shelf_count', type_tag: 'Integer', value: '5', options: [3, 5, 7] })]
    const props = baseProps({ parameters: params })
    render(<ParameterRail {...props} />)

    const select = enumSelect()
    expect(select.value).toBe('5')
    fireEvent.change(select, { target: { value: '7' } })
    expect(props.onChange).toHaveBeenCalledWith('shelf_count', 7)
  })

  test('boolean enum keeps the checkbox (P11 择一：0/1 二值 checkbox 体验已完备)', () => {
    const params = [makeParam({ name: 'show_in_3d', type_tag: 'Boolean', value: '1', options: [0, 1] })]
    render(<ParameterRail {...baseProps({ parameters: params })} />)

    expect(document.querySelector('.toggle-input')).toBeTruthy()
    expect(document.querySelector('.enum-select')).toBeNull()
  })

  test('parameter without options keeps the existing text input', () => {
    const params = [makeParam({ options: null })]
    render(<ParameterRail {...baseProps({ parameters: params })} />)

    expect(document.querySelector('.text-input')).toBeTruthy()
    expect(document.querySelector('.enum-select')).toBeNull()
  })
})

describe('ParameterRail view toggle (P11)', () => {
  test('defaults to the parameters view with both tabs present', () => {
    const params = [makeParam()]
    render(<ParameterRail {...baseProps({ parameters: params })} />)

    const tabs = screen.getAllByRole('tab')
    expect(tabs.map((tab) => tab.textContent)).toEqual(['参数', '参数脚本'])
    expect(tabs[0].getAttribute('aria-selected')).toBe('true')
    expect(tabs[1].getAttribute('aria-selected')).toBe('false')
  })

  test('switching to the script tab embeds the script editor for vl.gdl', async () => {
    const params = [makeParam()]
    const props = baseProps({
      parameters: params,
      paramScriptContent: 'VALUES "pattern_type" "直棂", "井字"',
      paramScriptDirty: true,
      onParamScriptChange: vi.fn(),
      onParamScriptSave: vi.fn(),
    })
    render(<ParameterRail {...props} />)

    fireEvent.click(screen.getByRole('tab', { name: '参数脚本' }))

    expect(await screen.findByTestId('mock-script-editor')).toBeTruthy()
    // 参数控件区域让位给脚本视图
    expect(document.querySelector('.parameter-section')).toBeNull()
    // 保存按钮跟随脏状态
    const saveButton = screen.getByRole('button', { name: '保存' })
    expect((saveButton as HTMLButtonElement).disabled).toBe(false)
    fireEvent.click(saveButton)
    expect(props.onParamScriptSave).toHaveBeenCalled()
  })

  test('script tab save button is disabled when vl.gdl is clean', async () => {
    render(
      <ParameterRail
        {...baseProps({
          parameters: [makeParam()],
          paramScriptContent: '',
          paramScriptDirty: false,
          onParamScriptSave: vi.fn(),
        })}
      />,
    )

    fireEvent.click(screen.getByRole('tab', { name: '参数脚本' }))

    await screen.findByTestId('mock-script-editor')
    const saveButton = screen.getByRole('button', { name: '保存' })
    expect((saveButton as HTMLButtonElement).disabled).toBe(true)
  })

  test('switching back to the params tab restores the parameter controls', async () => {
    const params = [makeParam({ options: ['直棂', '井字', '菱花'] })]
    render(<ParameterRail {...baseProps({ parameters: params })} />)

    fireEvent.click(screen.getByRole('tab', { name: '参数脚本' }))
    await screen.findByTestId('mock-script-editor')
    expect(document.querySelector('.parameter-section')).toBeNull()

    fireEvent.click(screen.getByRole('tab', { name: '参数' }))
    expect(document.querySelector('.enum-select')).toBeTruthy()
  })
})
