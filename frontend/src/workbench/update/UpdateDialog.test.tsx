import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, test, vi } from 'vitest'
import { UpdateDialog } from './UpdateDialog'
import { useUpdateStore } from '../../state/updateStore'
import {
  checkForUpdate,
  downloadAndInstallUpdate,
  fetchAppVersion,
  isTauriDesktop,
  openReleasesPage,
} from '../../api/updater'

vi.mock('../../api/updater', () => ({
  isTauriDesktop: vi.fn(() => true),
  fetchAppVersion: vi.fn(async () => '0.9.1'),
  checkForUpdate: vi.fn(),
  downloadAndInstallUpdate: vi.fn(async () => undefined),
  openReleasesPage: vi.fn(async () => undefined),
}))

const mockIsTauri = vi.mocked(isTauriDesktop)
const mockCheck = vi.mocked(checkForUpdate)
const mockDownload = vi.mocked(downloadAndInstallUpdate)
const mockOpenPage = vi.mocked(openReleasesPage)

const sampleInfo = {
  version: '0.9.2',
  current_version: '0.9.1',
  notes: [
    "## What's Changed",
    '* feat: 顶栏版本入口与更新对话框 by @dev in https://github.com/o/r/pull/1',
    '* fix: 修复预览崩溃 by @dev in https://github.com/o/r/pull/2',
  ].join('\n'),
}

const initialState = useUpdateStore.getState()

beforeEach(() => {
  vi.clearAllMocks()
  mockIsTauri.mockReturnValue(true)
  useUpdateStore.setState(initialState, true)
})

function openDialogWith(overrides: Partial<ReturnType<typeof useUpdateStore.getState>> = {}) {
  useUpdateStore.setState({ dialogOpen: true, checked: true, currentVersion: '0.9.1', ...overrides })
}

describe('UpdateDialog', () => {
  test('renders nothing when closed', () => {
    const { container } = render(<UpdateDialog />)
    expect(container.firstChild).toBeNull()
  })

  test('shows up-to-date state when checked and no update', () => {
    openDialogWith()
    render(<UpdateDialog />)
    expect(screen.getByText('软件更新')).toBeTruthy()
    expect(screen.getByText('已是最新版本')).toBeTruthy()
    expect(screen.getByText(/0\.9\.1/)).toBeTruthy()
  })

  test('shows version transition and parsed highlights when update available', () => {
    openDialogWith({ info: sampleInfo })
    render(<UpdateDialog />)
    expect(screen.getByText('发现新版本 0.9.2')).toBeTruthy()
    expect(screen.getByText('更新要点')).toBeTruthy()
    expect(screen.getByText('新增')).toBeTruthy()
    expect(screen.getByText('顶栏版本入口与更新对话框')).toBeTruthy()
    expect(screen.getByText('修复')).toBeTruthy()
    expect(screen.getByText('修复预览崩溃')).toBeTruthy()
  })

  test('falls back to no-notes hint when notes have no bullets', () => {
    openDialogWith({ info: { ...sampleInfo, notes: 'see release page' } })
    render(<UpdateDialog />)
    expect(screen.getByText('暂无更新要点，可查看完整更新说明')).toBeTruthy()
  })

  test('update button drives download then installing state', async () => {
    mockDownload.mockImplementation(async (onProgress) => {
      onProgress({ downloaded: 10, total: 20 })
    })
    openDialogWith({ info: sampleInfo })
    render(<UpdateDialog />)
    fireEvent.click(screen.getByText('立即更新'))
    expect(mockDownload).toHaveBeenCalledTimes(1)
    expect(await screen.findByText(/正在安装并重启/)).toBeTruthy()
    expect(useUpdateStore.getState().phase).toBe('installing')
  })

  test('failure shows error with retry and manual download fallback', async () => {
    mockDownload.mockRejectedValue(new Error('bad signature'))
    openDialogWith({ info: sampleInfo })
    render(<UpdateDialog />)
    fireEvent.click(screen.getByText('立即更新'))
    expect(await screen.findByText('自动更新失败')).toBeTruthy()
    fireEvent.click(screen.getByText('完整更新说明'))
    expect(mockOpenPage).toHaveBeenCalledTimes(1)
    expect(screen.getByText('重试')).toBeTruthy()
  })

  test('manual recheck button triggers a new check', async () => {
    mockCheck.mockResolvedValue(sampleInfo)
    openDialogWith({ info: null })
    render(<UpdateDialog />)
    fireEvent.click(screen.getByText('检查更新'))
    await waitFor(() => expect(useUpdateStore.getState().info?.version).toBe('0.9.2'))
  })

  test('close button hides the dialog', () => {
    openDialogWith({ info: sampleInfo })
    render(<UpdateDialog />)
    fireEvent.click(screen.getByText('关闭'))
    expect(useUpdateStore.getState().dialogOpen).toBe(false)
  })
})
