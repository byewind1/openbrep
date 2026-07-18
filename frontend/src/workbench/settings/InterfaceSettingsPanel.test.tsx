import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, test } from 'vitest'
import { InterfaceSettingsPanel, interfaceSummary } from './InterfaceSettingsPanel'
import { useUiPrefsStore } from '../../state/uiPrefsStore'

const STORAGE_KEY = 'openbrep-ui-prefs'

function resetUiPrefs() {
  useUiPrefsStore.setState({ locale: 'zh' })
  window.localStorage.removeItem(STORAGE_KEY)
}

describe('InterfaceSettingsPanel', () => {
  beforeEach(resetUiPrefs)
  afterEach(resetUiPrefs)

  test('defaults to Chinese selected', () => {
    render(<InterfaceSettingsPanel locale="zh" onLocaleChange={() => undefined} />)

    expect(screen.getByRole('radio', { name: '中文' })).toHaveProperty('checked', true)
    expect(screen.getByRole('radio', { name: 'English' })).toHaveProperty('checked', false)
  })

  test('invokes onLocaleChange when English is selected', () => {
    let selected = 'zh'
    render(<InterfaceSettingsPanel locale="zh" onLocaleChange={(locale) => { selected = locale }} />)

    fireEvent.click(screen.getByRole('radio', { name: 'English' }))

    expect(selected).toBe('en')
  })

  test('renders the language label and description via t()', () => {
    render(<InterfaceSettingsPanel locale="zh" onLocaleChange={() => undefined} />)

    expect(screen.getByText('语言')).toBeTruthy()
    expect(screen.getByText('选择工作台显示语言，切换后立即生效')).toBeTruthy()
  })
})

describe('interfaceSummary', () => {
  test('shows the native name of the active locale', () => {
    expect(interfaceSummary('zh')).toBe('中文')
    expect(interfaceSummary('en')).toBe('English')
  })
})

describe('useUiPrefsStore', () => {
  beforeEach(resetUiPrefs)
  afterEach(resetUiPrefs)

  test('defaults to zh', () => {
    expect(useUiPrefsStore.getState().locale).toBe('zh')
  })

  test('setLocale updates state and persists to localStorage', () => {
    useUiPrefsStore.getState().setLocale('en')

    expect(useUiPrefsStore.getState().locale).toBe('en')
    const stored = window.localStorage.getItem(STORAGE_KEY)
    expect(stored).toBeTruthy()
    expect(JSON.parse(stored as string).state.locale).toBe('en')
  })
})
