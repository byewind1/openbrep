import { useEffect } from 'react'
import { useWorkbenchStore } from '../state/useWorkbenchStore'

const CONFIG_POLL_INTERVAL_MS = 2000

/**
 * Poll the config.toml revision and refresh LLM settings when the file changes
 * outside the workbench (e.g. a model/provider added in a text editor), so the
 * active model — top-bar pill and settings list — stays in sync without a
 * manual reload. The compare-and-refresh logic lives in the `pollConfigRevision`
 * store action; this hook only drives the interval.
 */
export function useConfigAutoRefresh(intervalMs: number = CONFIG_POLL_INTERVAL_MS) {
  const pollConfigRevision = useWorkbenchStore((state) => state.pollConfigRevision)

  useEffect(() => {
    const timer = setInterval(() => void pollConfigRevision(), intervalMs)
    return () => clearInterval(timer)
  }, [pollConfigRevision, intervalMs])
}
