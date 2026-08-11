import { useEffect, useRef, useState } from 'react'
import {
  clampDrawerHeight,
  DEFAULT_DRAWER_HEIGHT,
  DRAWER_STATE_STORAGE_KEY,
  parseStoredDrawerState,
  serializeDrawerState,
  type DrawerState,
} from '../workbench/layout/resizableWorkspace'

// 折叠 = 高度收成 tab 条（styles.css .drawer-tabs 行高）
export const COLLAPSED_DRAWER_HEIGHT = 34

/**
 * 底部抽屉高度/折叠态（P4-D）：状态 + localStorage 持久化 + 内联覆盖
 * app-shell 的 --bottom-drawer-height。纯浏览器视图偏好，不进 zustand/config。
 * 抽成 hook 以控制 BottomDrawer.tsx 行数；`shellRef` 挂在抽屉根元素上，
 * 其 parentElement 即 app-shell。
 */
export function useDrawerLayout() {
  const [drawer, setDrawer] = useState<DrawerState>(() => {
    if (typeof window === 'undefined') return { height: DEFAULT_DRAWER_HEIGHT, collapsed: false }
    return (
      parseStoredDrawerState(window.localStorage.getItem(DRAWER_STATE_STORAGE_KEY)) ?? {
        height: DEFAULT_DRAWER_HEIGHT,
        collapsed: false,
      }
    )
  })
  const { height, collapsed } = drawer
  const shellRef = useRef<HTMLElement | null>(null)

  useEffect(() => {
    if (typeof window === 'undefined') return
    window.localStorage.setItem(DRAWER_STATE_STORAGE_KEY, serializeDrawerState(drawer))
  }, [drawer])

  useEffect(() => {
    const shell = shellRef.current?.parentElement
    if (!shell) return
    shell.style.setProperty('--bottom-drawer-height', `${collapsed ? COLLAPSED_DRAWER_HEIGHT : height}px`)
  }, [height, collapsed])

  function applyHeight(next: number) {
    const viewportHeight = typeof window !== 'undefined' ? window.innerHeight : 0
    setDrawer((current) => ({ ...current, height: clampDrawerHeight(next, viewportHeight) }))
  }

  function setCollapsed(next: boolean) {
    setDrawer((current) => ({ ...current, collapsed: next }))
  }

  function resetHeight() {
    setDrawer((current) => ({ ...current, height: DEFAULT_DRAWER_HEIGHT }))
  }

  return { height, collapsed, setCollapsed, applyHeight, resetHeight, shellRef }
}
