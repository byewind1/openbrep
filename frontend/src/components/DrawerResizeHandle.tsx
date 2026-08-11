import { useRef } from 'react'
import type { PointerEvent as ReactPointerEvent } from 'react'

interface DrawerResizeHandleProps {
  height: number
  onHeightChange: (height: number) => void
  onReset: () => void
}

/**
 * 底部抽屉顶边水平拖柄（P4-D）：row-resize。指针捕获保证移出元素后
 * 事件仍送达；clamp 由调用方（BottomDrawer）负责。
 */
export function DrawerResizeHandle({ height, onHeightChange, onReset }: DrawerResizeHandleProps) {
  const dragRef = useRef<{ startY: number; startHeight: number } | null>(null)

  function startDrag(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return
    event.currentTarget.setPointerCapture(event.pointerId)
    dragRef.current = { startY: event.clientY, startHeight: height }
    document.body.classList.add('drawer-resizing')
  }

  function handlePointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    const drag = dragRef.current
    if (!drag) return
    event.preventDefault()
    onHeightChange(drag.startHeight + (drag.startY - event.clientY))
  }

  function endDrag() {
    if (!dragRef.current) return
    dragRef.current = null
    document.body.classList.remove('drawer-resizing')
  }

  return (
    <div
      className="drawer-resize-handle"
      role="separator"
      aria-orientation="horizontal"
      title="Drag to resize. Double-click to reset."
      onPointerDown={startDrag}
      onPointerMove={handlePointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onDoubleClick={onReset}
    />
  )
}
