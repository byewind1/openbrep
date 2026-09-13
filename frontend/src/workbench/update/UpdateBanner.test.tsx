import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, test, vi } from 'vitest'
import { UpdateBanner } from './UpdateBanner'
import {
  checkForUpdate,
  downloadAndInstallUpdate,
  isTauriDesktop,
  openReleasesPage,
} from '../../api/updater'

vi.mock('../../api/updater', () => ({
  isTauriDesktop: vi.fn(() => true),
  checkForUpdate: vi.fn(),
  downloadAndInstallUpdate: vi.fn(),
  openReleasesPage: vi.fn(async () => undefined),
}))

const mockIsTauri = vi.mocked(isTauriDesktop)
const mockCheck = vi.mocked(checkForUpdate)
const mockDownload = vi.mocked(downloadAndInstallUpdate)
const mockOpenPage = vi.mocked(openReleasesPage)

const sampleInfo = { version: '0.9.2', current_version: '0.9.1', notes: null }

beforeEach(() => {
  vi.clearAllMocks()
  mockIsTauri.mockReturnValue(true)
})

describe('UpdateBanner', () => {
  test('renders nothing outside the Tauri desktop shell', () => {
    mockIsTauri.mockReturnValue(false)
    const { container } = render(<UpdateBanner />)
    expect(container.firstChild).toBeNull()
    expect(mockCheck).not.toHaveBeenCalled()
  })

  test('renders nothing when no update is available', async () => {
    mockCheck.mockResolvedValue(null)
    const { container } = render(<UpdateBanner />)
    await waitFor(() => expect(mockCheck).toHaveBeenCalledTimes(1))
    expect(container.firstChild).toBeNull()
  })

  test('stays silent when the update check fails (offline / no release)', async () => {
    mockCheck.mockRejectedValue(new Error('network'))
    const { container } = render(<UpdateBanner />)
    await waitFor(() => expect(mockCheck).toHaveBeenCalledTimes(1))
    expect(container.firstChild).toBeNull()
  })

  test('shows the banner with versions when an update is available', async () => {
    mockCheck.mockResolvedValue(sampleInfo)
    render(<UpdateBanner />)
    expect(await screen.findByText(/0\.9\.2/)).toBeTruthy()
    expect(screen.getByText(/0\.9\.1/)).toBeTruthy()
  })

  test('dismiss hides the banner for this session', async () => {
    mockCheck.mockResolvedValue(sampleInfo)
    const { container } = render(<UpdateBanner />)
    await screen.findByText(/0\.9\.2/)
    fireEvent.click(screen.getByText('稍后'))
    await waitFor(() => expect(container.firstChild).toBeNull())
  })

  test('update flow: download then installing state', async () => {
    mockCheck.mockResolvedValue(sampleInfo)
    mockDownload.mockImplementation(async (onProgress) => {
      onProgress({ downloaded: 50, total: 100 })
      onProgress({ downloaded: 100, total: 100 })
    })
    render(<UpdateBanner />)
    await screen.findByText(/0\.9\.2/)
    fireEvent.click(screen.getByText('立即更新'))
    expect(mockDownload).toHaveBeenCalledTimes(1)
    expect(await screen.findByText(/正在安装并重启/)).toBeTruthy()
  })

  test('download failure falls back to the manual download page', async () => {
    mockCheck.mockResolvedValue(sampleInfo)
    mockDownload.mockRejectedValue(new Error('signature mismatch'))
    render(<UpdateBanner />)
    await screen.findByText(/0\.9\.2/)
    fireEvent.click(screen.getByText('立即更新'))
    expect(await screen.findByText('自动更新失败')).toBeTruthy()
    fireEvent.click(screen.getByText('前往下载页'))
    expect(mockOpenPage).toHaveBeenCalledTimes(1)
  })
})
