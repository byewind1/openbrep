import { useState } from 'react'
import { useT } from '../i18n'
import type { VisionExtraction } from '../api/types'

/**
 * 读图提取卡片（P5d-1，只读）。
 *
 * 渲染 vision 提取结果：schema 名 + 字段表 + 低置信字段琥珀色高亮 +
 * critic 修正（旧值→新值）+ 降级标记（【分析失败已降级】/【critic 校验已降级】）。
 * 数据源是 assistant 消息上的 visionExtractions（来自 vision_analysis_done
 * 事件 payload.extraction，与后端 metadata["vision_extractions"] 同构）。
 */
export function ExtractionCardList({ extractions }: { extractions: VisionExtraction[] }) {
  const visible = extractions.filter((e) => !e.skipped)
  if (!visible.length) return null
  return (
    <div className="assistant-vision-extractions">
      {visible.map((ext, i) => (
        <ExtractionCard key={`${ext.sha256 ?? ''}-${i}`} extraction={ext} />
      ))}
    </div>
  )
}

function renderValue(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

/** 顶层字段行的附加标记：嵌套路径修正（grid_topology.rows 4→3）与低置信（低置信：grid_topology.rows） */
function fieldMarkers(key: string, extraction: VisionExtraction, lowConfidenceLabel: string): string[] {
  const markers: string[] = []
  const prefix = `${key}.`
  for (const c of extraction.corrections ?? []) {
    const field = c.field ?? ''
    if (field.startsWith(prefix)) {
      markers.push(`${field} ${renderValue(c.old)}→${renderValue(c.new)}`)
    }
  }
  for (const [path, confidence] of Object.entries(extraction.confidence ?? {})) {
    if (confidence === 'low' && path.startsWith(prefix)) {
      markers.push(`${lowConfidenceLabel}：${path}`)
    }
  }
  return markers
}

function ExtractionCard({ extraction }: { extraction: VisionExtraction }) {
  const t = useT()
  const { schema_name: schemaName, fields, confidence, corrections, degraded, critic_degraded, raw_description, token } = extraction
  const fieldEntries = Object.entries(fields ?? {})
  const header = token
    ? `${token}${schemaName ? ` · ${schemaName}` : ''}`
    : (schemaName ?? t('vision.extraction.title'))
  return (
    <div className="assistant-vision-card">
      <div className="assistant-vision-card-header">
        <strong>{t('vision.extraction.title')}</strong>
        <em>{header}</em>
      </div>
      {degraded ? (
        <p className="assistant-vision-degraded">⚠️ {t('vision.extraction.degraded')}</p>
      ) : null}
      {critic_degraded ? (
        <p className="assistant-vision-degraded">⚠️ {t('vision.extraction.criticDegraded')}</p>
      ) : null}
      {fieldEntries.length ? (
        <table className="assistant-vision-fields">
          <tbody>
            {fieldEntries.map(([key, value]) => {
              const low = (confidence ?? {})[key] === 'low'
              // 顶层字段精确修正：旧值→新值（critic evidence 作为悬浮提示）
              const correction = (corrections ?? []).find((c) => (c.field ?? '') === key)
              const markers = fieldMarkers(key, extraction, t('vision.extraction.lowConfidence'))
              return (
                <tr key={key} className={low ? 'is-low-confidence' : undefined}>
                  <th>{key}</th>
                  <td>
                    {correction ? (
                      <span
                        className="assistant-vision-correction"
                        title={correction.evidence ? t('vision.extraction.evidenceHint') : undefined}
                      >
                        {renderValue(correction.old)} → {renderValue(correction.new)}
                      </span>
                    ) : (
                      renderValue(value)
                    )}
                    {low ? (
                      <em className="assistant-vision-low-badge">{t('vision.extraction.lowConfidence')}</em>
                    ) : null}
                    {markers.map((m) => (
                      <span key={m} className="assistant-vision-marker">{m}</span>
                    ))}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      ) : raw_description ? (
        <p className="assistant-vision-raw">{raw_description}</p>
      ) : null}
    </div>
  )
}

// ── P5d-2 可编辑确认卡（提取确认门：模型读错，用户在生成前拦住）─────────────────

/** 按点路径取嵌套值（grid_topology.rows → fields["grid_topology"]["rows"]） */
function getNested(fields: Record<string, unknown> | undefined, path: string): unknown {
  let node: unknown = fields
  for (const part of path.split('.')) {
    if (node && typeof node === 'object' && part in (node as Record<string, unknown>)) {
      node = (node as Record<string, unknown>)[part]
    } else {
      return undefined
    }
  }
  return node
}

/** 按点路径写嵌套值；中间缺层用 dict 补齐 */
function setNested(fields: Record<string, unknown>, path: string, value: unknown) {
  const parts = path.split('.')
  let node = fields
  for (const part of parts.slice(0, -1)) {
    const next = node[part]
    if (!next || typeof next !== 'object' || Array.isArray(next)) {
      node[part] = {}
    }
    node = node[part] as Record<string, unknown>
  }
  node[parts[parts.length - 1]] = value
}

/** 数字字段做数字解析：原值是 number → 输入转 number（NaN 保留原值）；其余按字符串 */
function coerceEditedValue(original: unknown, raw: string): unknown {
  if (typeof original === 'number') {
    const n = Number(raw)
    return Number.isFinite(n) ? n : original
  }
  return raw
}

/**
 * 提取确认门（P5d-2）：CREATE 带图提取完成后、生成前弹出的可编辑卡片。
 *
 * 可编辑范围（设计 D4）= schema 的 required + critic_checks 字段（含嵌套点路径，
 * 如 grid_topology.rows）；其余字段只读。数字字段做数字解析；低置信琥珀高亮；
 * critic 修正展示 旧值→新值。确认 → 把编辑后的 extractions 随原消息重发
 * （confirmed_extractions，后端跳过 harness 重建 plans）；取消 → 什么都不生成。
 */
export function ExtractionConfirmCard({
  extractions,
  busy,
  onConfirm,
  onCancel,
}: {
  extractions: VisionExtraction[]
  busy: boolean
  onConfirm: (extractions: VisionExtraction[]) => void
  onCancel: () => void
}) {
  const t = useT()
  // 草稿：`${extIndex}|${点路径}` → 输入框原始字符串；未编辑的路径不入草稿
  const [drafts, setDrafts] = useState<Record<string, string>>({})

  const visible = extractions.filter((e) => !e.skipped)
  if (!visible.length) return null

  // 可编辑范围按每张图各自的 schema 计算（设计 D4）；多图混合 schema 时不能取并集，
  // 否则 A 图的可编辑路径会把 B 图的同名字段也变成输入框
  const editablePathsFor = (ext: VisionExtraction) =>
    new Set<string>([...(ext.required ?? []), ...(ext.critic_checks ?? [])])

  function buildConfirmed(): VisionExtraction[] {
    return visible.map((ext, extIndex) => {
      const fields = JSON.parse(JSON.stringify(ext.fields ?? {})) as Record<string, unknown>
      for (const [key, raw] of Object.entries(drafts)) {
        const [idx, path] = key.split('|')
        if (Number(idx) !== extIndex) continue
        const original = getNested(ext.fields, path)
        setNested(fields, path, coerceEditedValue(original, raw))
      }
      return { ...ext, fields }
    })
  }

  return (
    <div className="assistant-vision-card assistant-vision-card--editable" role="group" aria-label={t('assistant.extraction.title')}>
      <div className="assistant-vision-card-header">
        <strong>{t('assistant.extraction.title')}</strong>
        <em>{t('assistant.extraction.subtitle')}</em>
      </div>
      {visible.map((ext, extIndex) => (
        <EditableExtraction
          key={`${ext.sha256 ?? ''}-${extIndex}`}
          extraction={ext}
          extIndex={extIndex}
          editablePaths={editablePathsFor(ext)}
          drafts={drafts}
          onDraft={setDrafts}
        />
      ))}
      <div className="plan-confirm-actions">
        <button
          type="button"
          className="plan-confirm-approve"
          disabled={busy}
          onClick={() => onConfirm(buildConfirmed())}
        >
          {t('assistant.extraction.confirm')}
        </button>
        <button
          type="button"
          className="plan-confirm-reject"
          disabled={busy}
          onClick={onCancel}
        >
          {t('assistant.extraction.cancel')}
        </button>
      </div>
    </div>
  )
}

function EditableExtraction({
  extraction,
  extIndex,
  editablePaths,
  drafts,
  onDraft,
}: {
  extraction: VisionExtraction
  extIndex: number
  editablePaths: Set<string>
  drafts: Record<string, string>
  onDraft: (updater: (prev: Record<string, string>) => Record<string, string>) => void
}) {
  const t = useT()
  const { schema_name: schemaName, fields, confidence, corrections, degraded, critic_degraded, raw_description, token } = extraction
  const header = token
    ? `${token}${schemaName ? ` · ${schemaName}` : ''}`
    : (schemaName ?? t('vision.extraction.title'))

  // 顶层字段的编辑形态：
  //  - 自身在可编辑集（顶层字段）→ 整字段一个输入框
  //  - 有可编辑嵌套路径（grid_topology.rows）→ 每个嵌套路径一个输入框 + 其余只读 JSON
  //  - 其余 → 只读行（与只读卡片一致）
  const rows: Array<[string, unknown]> = Object.entries(fields ?? {})
  const nestedGroups = rows.filter(
    ([key]) => key.split('.').length === 1 && [...editablePaths].some((p) => p.startsWith(`${key}.`)),
  )
  const nestedPathsFor = (key: string) =>
    [...editablePaths].filter((p) => p.startsWith(`${key}.`)).sort()

  return (
    <div className="assistant-vision-subcard">
      <div className="assistant-vision-card-header">
        <em>{header}</em>
      </div>
      {degraded ? <p className="assistant-vision-degraded">⚠️ {t('vision.extraction.degraded')}</p> : null}
      {critic_degraded ? <p className="assistant-vision-degraded">⚠️ {t('vision.extraction.criticDegraded')}</p> : null}
      {rows.length ? (
        <table className="assistant-vision-fields">
          <tbody>
            {rows.map(([key, value]) => {
              // 嵌套可编辑字段组：grid_topology.rows / grid_topology.cols 输入框 + 其余只读
              if (nestedGroups.some(([k]) => k === key)) {
                const nested = nestedPathsFor(key)
                const lowNested = nested.some((p) => (confidence ?? {})[p] === 'low')
                const markers = fieldMarkers(key, extraction, t('vision.extraction.lowConfidence'))
                return (
                  <tr key={key} className={lowNested ? 'is-low-confidence' : undefined}>
                    <th>{key}</th>
                    <td>
                      {nested.map((path) => {
                        const current = getNested(fields, path)
                        const low = (confidence ?? {})[path] === 'low'
                        return (
                          <label key={path} className="assistant-vision-edit-row">
                            <span className="assistant-vision-edit-path">{path}{low ? `（${t('vision.extraction.lowConfidence')}）` : ''}</span>
                            <input
                              className="assistant-vision-edit-input"
                              aria-label={path}
                              defaultValue={current === undefined || current === null ? '' : String(current)}
                              onChange={(e) =>
                                onDraft((prev) => ({ ...prev, [`${extIndex}|${path}`]: e.target.value }))
                              }
                            />
                          </label>
                        )
                      })}
                      {markers.map((m) => (
                        <span key={m} className="assistant-vision-marker">{m}</span>
                      ))}
                      <span className="assistant-vision-marker">{t('assistant.extraction.keepRest')}</span>
                      <pre className="assistant-vision-raw">{JSON.stringify(value, null, 2)}</pre>
                    </td>
                  </tr>
                )
              }
              const low = (confidence ?? {})[key] === 'low'
              const correction = (corrections ?? []).find((c) => (c.field ?? '') === key)
              const markers = fieldMarkers(key, extraction, t('vision.extraction.lowConfidence'))
              if (editablePaths.has(key)) {
                const current = value
                return (
                  <tr key={key} className={low ? 'is-low-confidence' : undefined}>
                    <th>{key}</th>
                    <td>
                      <label className="assistant-vision-edit-row">
                        <input
                          className="assistant-vision-edit-input"
                          aria-label={key}
                          defaultValue={current === undefined || current === null ? '' : String(current)}
                          onChange={(e) =>
                            onDraft((prev) => ({ ...prev, [`${extIndex}|${key}`]: e.target.value }))
                          }
                        />
                      </label>
                      {correction ? (
                        <span className="assistant-vision-correction" title={correction.evidence ? t('vision.extraction.evidenceHint') : undefined}>
                          {renderValue(correction.old)} → {renderValue(correction.new)}
                        </span>
                      ) : null}
                      {low ? <em className="assistant-vision-low-badge">{t('vision.extraction.lowConfidence')}</em> : null}
                      {markers.map((m) => (
                        <span key={m} className="assistant-vision-marker">{m}</span>
                      ))}
                    </td>
                  </tr>
                )
              }
              return (
                <tr key={key} className={low ? 'is-low-confidence' : undefined}>
                  <th>{key}</th>
                  <td>
                    {correction ? (
                      <span className="assistant-vision-correction" title={correction.evidence ? t('vision.extraction.evidenceHint') : undefined}>
                        {renderValue(correction.old)} → {renderValue(correction.new)}
                      </span>
                    ) : (
                      renderValue(value)
                    )}
                    {low ? <em className="assistant-vision-low-badge">{t('vision.extraction.lowConfidence')}</em> : null}
                    {markers.map((m) => (
                      <span key={m} className="assistant-vision-marker">{m}</span>
                    ))}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      ) : raw_description ? (
        <p className="assistant-vision-raw">{raw_description}</p>
      ) : null}
    </div>
  )
}
