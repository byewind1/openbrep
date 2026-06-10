import type { Preview2DPayload } from '../api/types'

interface Preview2DViewportProps {
  preview: Preview2DPayload | null
  warnings: string[]
}

export function Preview2DViewport({ preview, warnings }: Preview2DViewportProps) {
  const bounds = computeBounds(preview)
  const entityCount = preview ? geometryCount(preview) : 0
  const hasGeometry = entityCount > 0

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
          <svg className="preview2d-svg" viewBox={bounds.viewBox} role="img" aria-label="2D preview">
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

function computeBounds(preview: Preview2DPayload | null) {
  const points: Array<[number, number]> = []
  for (const line of preview?.lines ?? []) {
    points.push(line.from, line.to)
  }
  for (const polygon of preview?.polygons ?? []) {
    points.push(...polygon)
  }
  for (const circle of preview?.circles ?? []) {
    points.push([circle.cx - circle.r, circle.cy - circle.r], [circle.cx + circle.r, circle.cy + circle.r])
  }
  for (const arc of preview?.arcs ?? []) {
    points.push([arc.cx - arc.r, arc.cy - arc.r], [arc.cx + arc.r, arc.cy + arc.r])
  }

  if (!points.length) {
    return { viewBox: '-1 -1 2 2' }
  }

  const xs = points.map((point) => point[0])
  const ys = points.map((point) => point[1])
  const minX = Math.min(...xs)
  const maxX = Math.max(...xs)
  const minY = Math.min(...ys)
  const maxY = Math.max(...ys)
  const width = Math.max(maxX - minX, 0.1)
  const height = Math.max(maxY - minY, 0.1)
  const pad = Math.max(width, height) * 0.08
  return {
    viewBox: `${minX - pad} ${minY - pad} ${width + pad * 2} ${height + pad * 2}`,
  }
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
