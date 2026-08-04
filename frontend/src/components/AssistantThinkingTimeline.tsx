import { useState } from 'react'
import type { AssistantThinkingStep, ThinkingStage } from '../api/types'

interface AssistantThinkingTimelineProps {
  steps: AssistantThinkingStep[]
  busy?: boolean
  interrupted?: boolean
}

const STAGE_ICON: Record<ThinkingStage, string> = {
  understand: '🤔',
  think: '🧠',
  locate: '🎯',
  plan: '📝',
  modify: '✏️',
  compile: '🔨',
  preview: '📐',
  verify: '🔍',
  retry: '🧩',
  budget: '⚠️',
  cancel: '⏹',
  done: '✅',
}

const STAGE_LABEL: Record<ThinkingStage, string> = {
  understand: '理解意图',
  think: '思考',
  locate: '定位',
  plan: '制定方案',
  modify: '修改',
  compile: '编译验证',
  preview: '预览核对',
  verify: '完成检查',
  retry: '继续修复',
  budget: '预算耗尽',
  cancel: '已取消',
  done: '完成',
}

function stepIcon(step: AssistantThinkingStep): string {
  if (step.type === 'plan') return STAGE_ICON.plan
  if (step.type === 'tool_call') return step.ok ? '✅' : '❌'
  if (step.type === 'assistant_delta') return '💭'
  return STAGE_ICON[step.stage ?? 'think'] ?? '•'
}

function stepLabel(step: AssistantThinkingStep): string {
  if (step.type === 'tool_call') return step.message
  if (step.type === 'assistant_delta') return 'AI 思考中'
  if (step.type === 'plan') return step.message
  return STAGE_LABEL[step.stage ?? 'think'] ?? step.message
}

function PlanDetail({ step }: { step: AssistantThinkingStep }) {
  if (step.type !== 'plan') return null
  const files = step.affectedFiles ?? []
  const params = step.parameterChanges ?? []
  return (
    <div className="timeline-plan-detail">
      {step.strategy ? <p className="timeline-plan-strategy">{step.strategy}</p> : null}
      {files.length ? (
        <div className="timeline-plan-section">
          <strong>影响文件</strong>
          <ul>
            {files.map((f) => (
              <li key={f}>{f}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {params.length ? (
        <div className="timeline-plan-section">
          <strong>参数变更</strong>
          <ul>
            {params.map((p, i) => (
              <li key={i}>
                {p.name}
                {p.from !== undefined || p.to !== undefined
                  ? `：${p.from ?? '?'} → ${p.to ?? '?'}`
                  : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  )
}

export function AssistantThinkingTimeline({ steps, busy, interrupted }: AssistantThinkingTimelineProps) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  if (!steps.length && !busy) return null

  const visibleSteps = steps.slice(-12)
  const hasMore = steps.length > visibleSteps.length

  function toggle(index: number) {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(index)) next.delete(index)
      else next.add(index)
      return next
    })
  }

  return (
    <div className="assistant-thinking-timeline">
      {hasMore && (
        <button
          type="button"
          className="timeline-more"
          onClick={() => setExpanded(new Set(visibleSteps.map((_, i) => i)))}
        >
          …还有 {steps.length - visibleSteps.length} 步
        </button>
      )}
      <ul className="timeline-list">
        {visibleSteps.map((step, i) => {
          const globalIndex = steps.length - visibleSteps.length + i
          const isExpanded = expanded.has(globalIndex)
          const hasDetail = Boolean(step.detail) || step.type === 'plan'
          return (
            <li key={`${step.type}-${globalIndex}`} className={`timeline-step type-${step.type}`}>
              <span className="timeline-icon">{stepIcon(step)}</span>
              <div className="timeline-body">
                <button
                  type="button"
                  className="timeline-summary"
                  onClick={() => toggle(globalIndex)}
                  aria-expanded={isExpanded}
                >
                  <span className="timeline-label">{stepLabel(step)}</span>
                  {hasDetail && <span className="timeline-chevron">{isExpanded ? '▾' : '▸'}</span>}
                </button>
                {isExpanded && hasDetail ? (
                  <div className="timeline-detail">
                    {step.type === 'plan' ? <PlanDetail step={step} /> : <pre>{step.detail}</pre>}
                  </div>
                ) : null}
              </div>
            </li>
          )
        })}
        {busy && !interrupted && (
          <li className="timeline-step is-pending">
            <span className="timeline-icon">⟳</span>
            <span className="timeline-label">进行中…</span>
          </li>
        )}
        {interrupted && (
          <li className="timeline-step is-interrupted">
            <span className="timeline-icon">⏹</span>
            <span className="timeline-label">已中断</span>
          </li>
        )}
      </ul>
    </div>
  )
}
