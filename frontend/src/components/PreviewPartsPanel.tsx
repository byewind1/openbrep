import { useEffect, useRef } from 'react'
import { useT } from '../i18n'
import type { PartViewModel } from './previewParts'

interface PreviewPartsPanelProps {
  parts: PartViewModel[]
  selectedIndex: number | null
  onSelect: (meshIndex: number) => void
  onJump: (meshIndex: number) => void
  onToggleHidden: (meshIndex: number) => void
}

function EyeIcon({ open }: { open: boolean }) {
  return open ? (
    <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  ) : (
    <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24" />
      <line x1="1" y1="1" x2="23" y2="23" />
    </svg>
  )
}

function JumpIcon() {
  return (
    <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <line x1="7" y1="17" x2="17" y2="7" />
      <polyline points="7 7 17 7 17 17" />
    </svg>
  )
}

/**
 * Blender outliner 式部件面板（P1d）：颜色 chip + mesh 名 + 源码行。
 * 点击行 = 选中（与 P1a 拾取共用 selection 状态）；眼睛 = 隐藏/显示；
 * 双击行或跳转图标 = 跳源码。
 */
export function PreviewPartsPanel({
  parts,
  selectedIndex,
  onSelect,
  onJump,
  onToggleHidden,
}: PreviewPartsPanelProps) {
  const t = useT()
  const listRef = useRef<HTMLUListElement | null>(null)

  // 3D 点选 → 面板行滚动到可见（block:'nearest' 只在需要时滚）
  useEffect(() => {
    if (selectedIndex === null) return
    const row = listRef.current?.querySelector<HTMLElement>(`[data-mesh-index="${selectedIndex}"]`)
    if (row && typeof row.scrollIntoView === 'function') {
      row.scrollIntoView({ block: 'nearest' })
    }
  }, [selectedIndex])

  return (
    <div className="viewport-parts-panel" role="list" aria-label={t('preview.parts.title')}>
      <div className="parts-panel-header">{t('preview.parts.title')}</div>
      <ul className="parts-panel-list" ref={listRef}>
        {parts.map((part) => (
          <li
            key={part.meshIndex}
            data-mesh-index={part.meshIndex}
            role="listitem"
            className={`parts-row${selectedIndex === part.meshIndex ? ' selected' : ''}${part.visible ? '' : ' hidden'}`}
            onClick={() => onSelect(part.meshIndex)}
            onDoubleClick={() => onJump(part.meshIndex)}
            title={part.sourceLine ?? part.meshName}
          >
            <span className="parts-chip" style={{ background: part.color }} aria-hidden="true" />
            <span className="parts-name">{part.meshName}</span>
            <span className="parts-source">{part.sourceLine ?? ''}</span>
            <button
              type="button"
              className="parts-action"
              onClick={(event) => {
                event.stopPropagation()
                onToggleHidden(part.meshIndex)
              }}
              title={part.visible ? t('preview.parts.hide') : t('preview.parts.show')}
              aria-label={part.visible ? t('preview.parts.hide') : t('preview.parts.show')}
            >
              <EyeIcon open={part.visible} />
            </button>
            {part.sourceLine ? (
              <button
                type="button"
                className="parts-action"
                onClick={(event) => {
                  event.stopPropagation()
                  onJump(part.meshIndex)
                }}
                title={t('preview.pick.jumpToSource')}
                aria-label={t('preview.pick.jumpToSource')}
              >
                <JumpIcon />
              </button>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  )
}
