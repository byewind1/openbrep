import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, test, vi } from 'vitest'
import { UpdateSettingsSection } from './UpdateSettingsSection'
import { useUpdateStore } from '../../state/updateStore'
import { checkForUpdate, isTauriDesktop } from '../../api/updater'

vi.mock('../../api/updater', () => ({
  isTauriDesktop: vi.fn(() => true),
  fetchAppVersion: vi.fn(async () => '0.9.1'),
  checkForUpdate: vi.fn(),
  downloadAndInstallUpdate: vi.fn(async () => undefined),
  openReleasesPage: vi.fn(async () => undefined),
}))

const mockIsTauri = vi.mocked(isTauriDesktop)
const mockCheck = vi.mocked(checkForUpdate)

const initialState = useUpdateStore.getState()

beforeEach(() => {
  vi.clearAllMocks()
  window.localStorage.clear()
  mockIsTauri.mockReturnValue(true)
  useUpdateStore.setState({ ...initialState, channel: 'stable' }, true)
  vi.spyOn(window, 'confirm').mockReturnValue(true)
})

describe('UpdateSettingsSection', () => {
  test('shows the not-desktop hint in browser mode', () => {
    mockIsTauri.mockReturnValue(false)
    render(<UpdateSettingsSection />)
    expect(screen.getByText('仅桌面安装版支持自动更新')).toBeTruthy()
    expect(screen.queryByText('检查更新')).toBeNull()
  })

  test('manual check reports up-to-date when no update is returned', async () => {
    mockCheck.mockResolvedValue(null)
    render(<UpdateSettingsSection />)
    fireEvent.click(screen.getByText('检查更新'))
    expect(await screen.findByText('已是最新版本')).toBeTruthy()
  })

  test('available update opens the shared update dialog', async () => {
    mockCheck.mockResolvedValue({ version: '0.9.2', current_version: '0.9.1', notes: null })
    render(<UpdateSettingsSection />)
    fireEvent.click(screen.getByText('检查更新'))
    await screen.findByText(/0\.9\.2/)
    fireEvent.click(screen.getByText('立即更新'))
    expect(useUpdateStore.getState().dialogOpen).toBe(true)
  })

  test('check failure shows the manual download fallback', async () => {
    mockCheck.mockRejectedValue(new Error('network'))
    render(<UpdateSettingsSection />)
    fireEvent.click(screen.getByText('检查更新'))
    expect(await screen.findByText('检查失败，请稍后重试')).toBeTruthy()
    expect(screen.getByText('前往下载页')).toBeTruthy()
  })

  test('button is disabled while a check is in flight', async () => {
    let resolve: (v: null) => void = () => {}
    mockCheck.mockImplementation(() => new Promise((r) => { resolve = r }))
    render(<UpdateSettingsSection />)
    fireEvent.click(screen.getByText('检查更新'))
    const checking = (await screen.findByText('检查中…')) as HTMLButtonElement
    expect(checking.disabled).toBe(true)
    resolve(null)
    expect(await screen.findByText('已是最新版本')).toBeTruthy()
  })

  test('channel selection stays draft until explicitly saved and confirmed', () => {
    render(<UpdateSettingsSection />)
    fireEvent.change(screen.getByLabelText('更新通道'), { target: { value: 'development' } })
    expect(useUpdateStore.getState().channel).toBe('stable')
    expect(window.localStorage.getItem('openbrep.update-channel')).toBeNull()
    expect(screen.getByText('有未保存的通道变更')).toBeTruthy()

    fireEvent.click(screen.getByText('保存通道'))
    expect(window.confirm).toHaveBeenCalledTimes(1)
    expect(useUpdateStore.getState().channel).toBe('development')
    expect(window.localStorage.getItem('openbrep.update-channel')).toBe('development')
    expect(screen.getByText(/开发版随 main 构建/)).toBeTruthy()
  })

  test('cancelled channel confirmation does not persist', () => {
    vi.mocked(window.confirm).mockReturnValue(false)
    render(<UpdateSettingsSection />)
    fireEvent.change(screen.getByLabelText('更新通道'), { target: { value: 'development' } })
    fireEvent.click(screen.getByText('保存通道'))
    expect(useUpdateStore.getState().channel).toBe('stable')
    expect(window.localStorage.getItem('openbrep.update-channel')).toBeNull()
  })
})
