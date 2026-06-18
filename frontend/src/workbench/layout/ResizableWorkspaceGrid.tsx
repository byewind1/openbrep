import type { CSSProperties, PointerEvent as ReactPointerEvent, ReactNode } from 'react'
import { useEffect, useRef, useState } from 'react'
import {
  clampWorkspaceColumns,
  DEFAULT_WORKSPACE_COLUMNS,
  parseStoredWorkspaceColumns,
  serializeWorkspaceColumns,
  WORKSPACE_COLUMNS_STORAGE_KEY,
  type WorkspaceColumns,
} from './resizableWorkspace'

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

  function startResize(side: 'left' | 'right', event: ReactPointerEvent<HTMLButtonElement>) {
    if (event.button !== 0) return
    event.currentTarget.setPointerCapture(event.pointerId)
    dragRef.current = {
      side,
      startX: event.clientX,
      startColumns: columns,
    }
    document.body.classList.add('workspace-resizing')
  }

  function resetColumns() {
    const width = gridRef.current?.getBoundingClientRect().width ?? 0
    setColumns(clampWorkspaceColumns(DEFAULT_WORKSPACE_COLUMNS, width, { previewWorkspaceOpen }))
  }

  const style = {
    '--workspace-left-width': `${columns.left}px`,
    '--workspace-right-width': `${columns.right}px`,
  } as CSSProperties

  return (
    <section
      ref={gridRef}
      className={`workspace-grid${previewWorkspaceOpen ? ' preview-workspace-open' : ''}`}
      style={style}
      aria-busy={loading}
    >
      {left}
      <ResizeHandle side="left" onPointerDown={startResize} onDoubleClick={resetColumns} />
      {main}
      <ResizeHandle side="right" onPointerDown={startResize} onDoubleClick={resetColumns} />
      {right}
    </section>
  )
}

function ResizeHandle({
  side,
  onPointerDown,
  onDoubleClick,
}: {
  side: 'left' | 'right'
  onPointerDown: (side: 'left' | 'right', event: ReactPointerEvent<HTMLButtonElement>) => void
  onDoubleClick: () => void
}) {
  return (
    <button
      type="button"
      className={`workspace-resize-handle workspace-resize-handle-${side}`}
      aria-label={`Resize ${side === 'left' ? 'left' : 'right'} workspace panel`}
      title="Drag to resize. Double-click to reset."
      onPointerDown={(event) => onPointerDown(side, event)}
      onDoubleClick={onDoubleClick}
    />
  )
}
