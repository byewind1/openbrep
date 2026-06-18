import { useStore } from 'zustand'
import { workbenchStore } from './workbenchStore'

export function useWorkbenchStore<T>(selector: (state: ReturnType<typeof workbenchStore.getState>) => T): T {
  return useStore(workbenchStore, selector)
}
