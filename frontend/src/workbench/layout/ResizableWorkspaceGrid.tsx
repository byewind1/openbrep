import type { CSSProperties, PointerEvent as ReactPointerEvent, ReactNode } from 'react'
import { useEffect, useRef, useState } from 'react'
import { useT } from '../../i18n'
import {
  clampWorkspaceColumns,
  DEFAULT_WORKSPACE_COLUMNS,
  parseStoredWorkspaceColumns,
  serializeWorkspaceColumns,
  WORKSPACE_COLUMNS_STORAGE_KEY,
  type WorkspaceColumns,
} from './resizableWorkspace'

const COLLAPSED_RAIL_WIDTH = 28

interface ResizableWorkspaceGridProps {
  previewWorkspaceOpen: boolean
  loading: boolean
  left: ReactNode
  main: ReactNode
  right: ReactNode
}

interface DragState {
  side: 'left' | 'right'
  startX: number
  startColumns: WorkspaceColumns
}

export function ResizableWorkspaceGrid({ previewWorkspaceOpen, loading, left, main, right }: ResizableWorkspaceGridProps) {
  const gridRef = useRef<HTMLElement | null>(null)
  const dragRef = useRef<DragState | null>(null)
  const [columns, setColumns] = useState<WorkspaceColumns>(() => {
    if (typeof window === 'undefined') return DEFAULT_WORKSPACE_COLUMNS
    return parseStoredWorkspaceColumns(window.localStorage.getItem(WORKSPACE_COLUMNS_STORAGE_KEY)) ?? DEFAULT_WORKSPACE_COLUMNS
  })

  useEffect(() => {
    const width = gridRef.current?.getBoundingClientRect().width ?? 0
    if (!width) return
    setColumns((current) => clampWorkspaceColumns(current, width, { previewWorkspaceOpen }))
  }, [previewWorkspaceOpen])

  useEffect(() => {
    if (typeof window === 'undefined') return
    window.localStorage.setItem(WORKSPACE_COLUMNS_STORAGE_KEY, serializeWorkspaceColumns(columns))
  }, [columns])

  useEffect(() => {
    function handlePointerMove(event: PointerEvent) {
      const drag = dragRef.current
      const grid = gridRef.current
      if (!drag || !grid) return

      event.preventDefault()
      const width = grid.getBoundingClientRect().width
      const dx = event.clientX - drag.startX
      const next =
        drag.side === 'left'
          ? { ...drag.startColumns, left: drag.startColumns.left + dx }
          : { ...drag.startColumns, right: drag.startColumns.right - dx }
      setColumns(clampWorkspaceColumns(next, width, { previewWorkspaceOpen }))
    }

    function handlePointerUp() {
      dragRef.current = null
      document.body.classList.remove('workspace-resizing')
    }

    window.addEventListener('pointermove', handlePointerMove)
    window.addEventListener('pointerup', handlePointerUp)
    return () => {
      window.removeEventListener('pointermove', handlePointerMove)
      window.removeEventListener('pointerup', handlePointerUp)
      document.body.classList.remove('workspace-resizing')
    }
  }, [previewWorkspaceOpen])

  function isCollapsed(side: 'left' | 'right') {
    return side === 'left' ? (columns.leftCollapsed ?? false) : (columns.rightCollapsed ?? false)
  }

  function startResize(side: 'left' | 'right', event: ReactPointerEvent<HTMLButtonElement>) {
    if (event.button !== 0) return
    if (isCollapsed(side)) return
    event.currentTarget.setPointerCapture(event.pointerId)
    dragRef.current = {
      side,
      startX: event.clientX,
      startColumns: columns,
    }
    document.body.classList.add('workspace-resizing')
  }

  function toggleCollapse(side: 'left' | 'right') {
    setColumns((current) =>
      side === 'left'
        ? { ...current, leftCollapsed: !(current.leftCollapsed ?? false) }
        : { ...current, rightCollapsed: !(current.rightCollapsed ?? false) },
    )
  }

  function resetColumns() {
    const width = gridRef.current?.getBoundingClientRect().width ?? 0
    // 双击复位只重置宽度，不碰折叠态
    setColumns((current) =>
      clampWorkspaceColumns(
        { ...DEFAULT_WORKSPACE_COLUMNS, leftCollapsed: current.leftCollapsed, rightCollapsed: current.rightCollapsed },
        width,
        { previewWorkspaceOpen },
      ),
    )
  }

  const style = {
    '--workspace-left-width': `${isCollapsed('left') ? COLLAPSED_RAIL_WIDTH : columns.left}px`,
    '--workspace-right-width': `${isCollapsed('right') ? COLLAPSED_RAIL_WIDTH : columns.right}px`,
  } as CSSProperties

  return (
    <section
      ref={gridRef}
      className={`workspace-grid${previewWorkspaceOpen ? ' preview-workspace-open' : ''}`}
      style={style}
      aria-busy={loading}
    >
      <div className={`workspace-rail${isCollapsed('left') ? ' workspace-rail-collapsed' : ''}`}>
        {left}
        {isCollapsed('left') ? <CollapsedRailBar side="left" onExpand={() => toggleCollapse('left')} /> : null}
      </div>
      <ResizeHandle
        side="left"
        collapsed={isCollapsed('left')}
        onPointerDown={startResize}
        onDoubleClick={resetColumns}
        onToggleCollapse={() => toggleCollapse('left')}
      />
      {main}
      <ResizeHandle
        side="right"
        collapsed={isCollapsed('right')}
        onPointerDown={startResize}
        onDoubleClick={resetColumns}
        onToggleCollapse={() => toggleCollapse('right')}
      />
      <div className={`workspace-rail${isCollapsed('right') ? ' workspace-rail-collapsed' : ''}`}>
        {right}
        {isCollapsed('right') ? <CollapsedRailBar side="right" onExpand={() => toggleCollapse('right')} /> : null}
      </div>
    </section>
  )
}

function ResizeHandle({
  side,
  collapsed,
  onPointerDown,
  onDoubleClick,
  onToggleCollapse,
}: {
  side: 'left' | 'right'
  collapsed: boolean
  onPointerDown: (side: 'left' | 'right', event: ReactPointerEvent<HTMLButtonElement>) => void
  onDoubleClick: () => void
  onToggleCollapse: () => void
}) {
  const t = useT()
  const collapseLabel = side === 'left' ? t('layout.collapseLeft') : t('layout.collapseRight')
  return (
    <div className={`workspace-resize-cell workspace-resize-cell-${side}`}>
      <button
        type="button"
        className={`workspace-resize-handle workspace-resize-handle-${side}`}
        aria-label={`Resize ${side === 'left' ? 'left' : 'right'} workspace panel`}
        title="Drag to resize. Double-click to reset."
        disabled={collapsed}
        onPointerDown={(event) => onPointerDown(side, event)}
        onDoubleClick={onDoubleClick}
      />
      {!collapsed ? (
        <button
          type="button"
          className="workspace-handle-collapse"
          aria-label={collapseLabel}
          title={collapseLabel}
          onClick={onToggleCollapse}
        >
          {side === 'left' ? '◀' : '▶'}
        </button>
      ) : null}
    </div>
  )
}

function CollapsedRailBar({ side, onExpand }: { side: 'left' | 'right'; onExpand: () => void }) {
  const t = useT()
  const expandLabel = side === 'left' ? t('layout.expandLeft') : t('layout.expandRight')
  return (
    <div className={`collapsed-rail-bar collapsed-rail-bar-${side}`}>
      <button
        type="button"
        className="collapsed-rail-expand"
        aria-label={expandLabel}
        title={expandLabel}
        onClick={onExpand}
      >
        {side === 'left' ? '◀' : '▶'}
      </button>
    </div>
  )
}
