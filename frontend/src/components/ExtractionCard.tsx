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
