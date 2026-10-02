import { useCallback, useEffect, useMemo, useState } from 'react'
import { fetchUiLayout } from '../api/client'
import type { UIControl, UILayoutPayload, WorkbenchParameter } from '../api/types'

interface ArchicadParamPanelProps {
  parameters: WorkbenchParameter[]
  draftParameters: Record<string, unknown>
  onChange: (name: string, value: unknown) => void
}

/** L0b：按 ui.gdl 控件树绝对定位渲染，观感对齐 Archicad Ctrl+T 自定义面板。 */
export function ArchicadParamPanel({
  parameters,
  draftParameters,
  onChange,
}: ArchicadParamPanelProps) {
  const [layout, setLayout] = useState<UILayoutPayload | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const paramByName = useMemo(() => {
    const map = new Map<string, WorkbenchParameter>()
    for (const p of parameters) {
      map.set(p.name, p)
      map.set(p.name.toLowerCase(), p)
      map.set(p.name.toUpperCase(), p)
    }
    return map
  }, [parameters])

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await fetchUiLayout(draftParameters)
      if (!result.ok) {
        setError(result.error || 'ui.gdl 解析失败')
        setLayout(null)
        return
      }
      setLayout(result)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc))
    } finally {
      setLoading(false)
    }
  }, [draftParameters])

  useEffect(() => {
    void load()
  }, [load])

  const controls = layout?.controls ?? []
  const infields = controls.filter((c) => c.type === 'infield')
  const width = layout?.width || 480
  const height = Math.max(layout?.height || 320, 80)

  if (loading && !layout) {
    return <div className="archicad-panel-status">解析 ui.gdl…</div>
  }
  if (error) {
    return (
      <div className="archicad-panel-status" role="alert">
        {error}
        <button type="button" onClick={() => void load()}>重试</button>
      </div>
    )
  }
  if (!infields.length) {
    return (
      <div className="archicad-panel-status">
        <p>当前 ui.gdl 没有可渲染的参数输入控件。</p>
        {layout?.unsupported?.length ? (
          <ul className="archicad-panel-unsupported">
            {layout.unsupported.slice(0, 4).map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        ) : null}
        <p className="archicad-panel-hint">请使用「参数」页查看完整参数表。</p>
        <button type="button" onClick={() => void load()}>重新解析</button>
      </div>
    )
  }

  // Archicad UI 坐标是对话框像素；按容器宽度缩放
  const scale = 1

  return (
    <div className="archicad-panel-root">
      {layout?.title ? <div className="archicad-panel-title">{layout.title}</div> : null}
      <div
        className="archicad-panel-canvas"
        style={{ width: width * scale, height: height * scale, position: 'relative' }}
      >
        {controls.map((control, index) => (
          <ArchicadControl
            key={`${control.type}-${control.param ?? control.text ?? index}-${control.line ?? index}`}
            control={control}
            scale={scale}
            parameter={control.param ? paramByName.get(control.param) : undefined}
            draftValue={control.param ? draftParameters[control.param] : undefined}
            onChange={onChange}
          />
        ))}
      </div>
      {layout?.warnings?.length ? (
        <div className="archicad-panel-warnings">
          {layout.warnings.slice(0, 3).map((w) => (
            <div key={w}>{w}</div>
          ))}
        </div>
      ) : null}
      {layout?.unsupported?.length ? (
        <div className="archicad-panel-unsupported">
          {layout.unsupported.length} 个复杂控件未渲染，请在「参数」页编辑
        </div>
      ) : null}
    </div>
  )
}

function ArchicadControl({
  control,
  scale,
  parameter,
  draftValue,
  onChange,
}: {
  control: UIControl
  scale: number
  parameter?: WorkbenchParameter
  draftValue?: unknown
  onChange: (name: string, value: unknown) => void
}) {
  const style: React.CSSProperties = {
    position: 'absolute',
    left: control.x * scale,
    top: control.y * scale,
    width: Math.max(control.w * scale, 8),
    height: Math.max(control.h * scale, 8),
  }

  if (control.type === 'separator') {
    return <div className="archicad-separator" style={style} />
  }
  if (control.type === 'groupbox') {
    return (
      <div className="archicad-groupbox" style={style}>
        {control.text}
      </div>
    )
  }
  if (control.type === 'outfield') {
    return (
      <div className="archicad-outfield" style={style} title={control.text ?? undefined}>
        {control.text}
      </div>
    )
  }
  if (control.type === 'infield' && control.param) {
    const param = parameter
    const type = param?.type_tag ?? 'RealNum'
    const current =
      draftValue !== undefined
        ? draftValue
        : parseCurrent(param?.value)

    if (control.options?.length || param?.options?.length) {
      const options = (control.options ?? param?.options ?? []) as Array<
        { value: unknown; label: string } | string
      >
      return (
        <select
          className="archicad-infield"
          style={style}
          value={String(current ?? '')}
          onChange={(e) => onChange(control.param as string, e.currentTarget.value)}
        >
          {options.map((opt, i) => {
            const value = typeof opt === 'string' ? opt : opt.value
            const label = typeof opt === 'string' ? opt : opt.label
            return (
              <option key={`${String(value)}-${i}`} value={String(value)}>
                {label}
              </option>
            )
          })}
        </select>
      )
    }
    if (type === 'Boolean') {
      return (
        <input
          className="archicad-infield"
          style={style}
          type="checkbox"
          checked={Boolean(current)}
          onChange={(e) => onChange(control.param as string, e.currentTarget.checked)}
        />
      )
    }
    return (
      <input
        className="archicad-infield"
        style={style}
        type={type === 'String' ? 'text' : 'number'}
        step={type === 'Integer' ? 1 : 'any'}
        value={current === undefined || current === null ? '' : String(current)}
        onChange={(e) => {
          const raw = e.currentTarget.value
          if (type === 'Integer') {
            const n = Number(raw)
            onChange(control.param as string, Number.isFinite(n) ? Math.round(n) : raw)
          } else if (type === 'Length' || type === 'RealNum' || type === 'Angle') {
            const n = Number(raw)
            onChange(control.param as string, raw === '' || Number.isNaN(n) ? raw : n)
          } else {
            onChange(control.param as string, raw)
          }
        }}
      />
    )
  }
  return null
}

function parseCurrent(value: unknown): unknown {
  if (typeof value !== 'string') return value
  if (value === '0' || value === '1') {
    // Boolean 常以 0/1 字符串落盘；数字参数仍可 Number 解析
    const n = Number(value)
    return Number.isFinite(n) ? n : value
  }
  const n = Number(value)
  return value !== '' && Number.isFinite(n) ? n : value
}
