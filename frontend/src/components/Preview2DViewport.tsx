import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import type { Preview2DPayload } from '../api/types'
import {
  clampView2D,
  computeBounds2D,
  fitView2D,
  panBy,
  pixelsToViewBox,
  toViewBoxString,
  zoomAt,
  type View2D,
} from './preview2dView'

interface Preview2DViewportProps {
  preview: Preview2DPayload | null
  warnings: string[]
}

/**
 * 2D 预览视口（P3c）：滚轮以光标为锚点缩放、左键拖拽平移、双击复位。
 * 视口几何数学在 preview2dView.ts（纯函数），本组件只持有 viewBox 状态。
 */
export function Preview2DViewport({ preview, warnings }: Preview2DViewportProps) {
  const bounds = useMemo(() => computeBounds2D(preview), [preview])
  const boundsKey = bounds ? `${bounds.minX},${bounds.minY},${bounds.maxX},${bounds.maxY}` : ''
  const entityCount = preview ? geometryCount(preview) : 0
  const hasGeometry = entityCount > 0

  const [fit, setFit] = useState<View2D | null>(() => (bounds ? fitView2D(bounds) : null))
  const [view, setView] = useState<View2D>(() => (bounds ? fitView2D(bounds) : { minX: -1, minY: -1, width: 2, height: 2 }))

  // payload 变化（boundsKey 不同）→ 重新 fit 并复位。同一 payload 的重复渲染
  // 不打断用户当前的 pan/zoom。
  useEffect(() => {
    if (!bounds) {
      return
    }
    const fitted = fitView2D(bounds)
    setFit(fitted)
    setView(fitted)
  }, [boundsKey]) // eslint-disable-line react-hooks/exhaustive-deps

  const svgRef = useRef<SVGSVGElement | null>(null)
  const viewRef = useRef(view)
  viewRef.current = view
  const fitRef = useRef(fit)
  fitRef.current = fit

  // 滚轮缩放（原生监听，passive:false 才能 preventDefault 页面滚动）
  useEffect(() => {
    const svg = svgRef.current
    if (!svg) {
      return
    }
    const onWheel = (event: WheelEvent) => {
      event.preventDefault()
      const current = viewRef.current
      const currentFit = fitRef.current
      if (!currentFit || !Number.isFinite(event.deltaY)) {
        return
      }
      const rect = svg.getBoundingClientRect()
      const wPx = svg.clientWidth || rect.width
      const hPx = svg.clientHeight || rect.height
      if (wPx <= 0 || hPx <= 0) {
        return
      }
      const px = event.clientX - rect.left
      const py = event.clientY - rect.top
      const cx = current.minX + (px / wPx) * current.width
      const cy = current.minY + (py / hPx) * current.height
      const factor = Math.min(Math.max(Math.exp(-event.deltaY * 0.0012), 0.5), 2)
      setView(clampView2D(zoomAt(current, cx, cy, factor), currentFit))
    }
    svg.addEventListener('wheel', onWheel, { passive: false })
    return () => svg.removeEventListener('wheel', onWheel)
  }, [])

  const dragRef = useRef<{ startX: number; startY: number; view: View2D } | null>(null)

  const onPointerDown = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (event.button !== 0) {
      return
    }
    try {
      event.currentTarget.setPointerCapture(event.pointerId)
    } catch {
      // jsdom / 老旧环境未实现 pointer capture：拖拽仍可工作
    }
    dragRef.current = { startX: event.clientX, startY: event.clientY, view: viewRef.current }
  }

  const onPointerMove = (event: ReactPointerEvent<SVGSVGElement>) => {
    const drag = dragRef.current
    const svg = svgRef.current
    if (!drag || !svg) {
      return
    }
    const wPx = svg.clientWidth || svg.getBoundingClientRect().width
    const hPx = svg.clientHeight || svg.getBoundingClientRect().height
    const { dx, dy } = pixelsToViewBox(drag.view, event.clientX - drag.startX, event.clientY - drag.startY, wPx, hPx)
    setView(panBy(drag.view, -dx, -dy))
  }

  const endDrag = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (dragRef.current) {
      try {
        if (event.currentTarget.hasPointerCapture(event.pointerId)) {
          event.currentTarget.releasePointerCapture(event.pointerId)
        }
      } catch {
        // ignore
      }
    }
    dragRef.current = null
  }

  const resetView = () => {
    if (fitRef.current) {
      setView(fitRef.current)
    }
  }

  return (
    <section className="viewport-surface viewport-surface-2d">
      <div className="viewport-toolbar">
        <div>
          <span>2D View</span>
          <span>{entityCount} entities</span>
        </div>
      </div>
      <div className="preview2d-surface">
        {hasGeometry && preview ? (
          <svg
            ref={svgRef}
            className="preview2d-svg"
            viewBox={toViewBoxString(view)}
            role="img"
            aria-label="2D preview"
            style={{ touchAction: 'none', cursor: 'grab' }}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={endDrag}
            onPointerCancel={endDrag}
            onDoubleClick={resetView}
          >
            <g>
              {preview.polygons.map((polygon, index) => (
                <polygon className="preview2d-polygon" points={polygon.map((point) => point.join(',')).join(' ')} key={`poly-${index}`} />
              ))}
              {preview.lines.map((line, index) => (
                <line
                  className="preview2d-line"
                  x1={line.from[0]}
                  y1={line.from[1]}
                  x2={line.to[0]}
                  y2={line.to[1]}
                  key={`line-${index}`}
                />
              ))}
              {preview.circles.map((circle, index) => (
                <circle className="preview2d-line" cx={circle.cx} cy={circle.cy} r={circle.r} key={`circle-${index}`} />
              ))}
              {preview.arcs.map((arc, index) => (
                <path className="preview2d-line" d={arcPath(arc)} fill="none" key={`arc-${index}`} />
              ))}
            </g>
          </svg>
        ) : (
          <div className="preview2d-empty">No 2D geometry</div>
        )}
      </div>
      <footer className="viewport-footer">
        <span className="viewport-fidelity-hint" title="The built-in previewer renders a GDL subset. Compile and open in Archicad for the final result.">
          Approximate preview · verify in Archicad
        </span>
        <span>
          {entityCount} entities | {warnings.length} warnings
        </span>
      </footer>
    </section>
  )
}

function geometryCount(preview: Preview2DPayload) {
  return preview.lines.length + preview.polygons.length + preview.circles.length + preview.arcs.length
}

function arcPath(arc: { cx: number; cy: number; r: number; a0: number; a1: number }) {
  const start = polar(arc.cx, arc.cy, arc.r, arc.a0)
  const end = polar(arc.cx, arc.cy, arc.r, arc.a1)
  const largeArc = Math.abs(arc.a1 - arc.a0) > 180 ? 1 : 0
  const sweep = arc.a1 >= arc.a0 ? 1 : 0
  return `M ${start[0]} ${start[1]} A ${arc.r} ${arc.r} 0 ${largeArc} ${sweep} ${end[0]} ${end[1]}`
}

function polar(cx: number, cy: number, r: number, angle: number): [number, number] {
  const radians = (angle * Math.PI) / 180
  return [cx + r * Math.cos(radians), cy + r * Math.sin(radians)]
}
