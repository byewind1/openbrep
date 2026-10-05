import { useEffect, useState } from 'react'
import type { AssistantThinkingStep, ThinkingStage } from '../api/types'

interface AssistantThinkingTimelineProps {
  steps: AssistantThinkingStep[]
  busy?: boolean
  interrupted?: boolean
  /** 卡05：等待计时起点（pending 消息 createdAt） */
  startedAt?: number
  /** 卡05：任务类旧记录没有过程数据（不编造历史） */
  stale?: boolean
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

/** 卡05：等待提示阈值（毫秒）——按最后有效事件计算，而非任务总耗时 */
const WAIT_NOTICE_MS = 15_000
/** RF05：长记录分页大小 */
const PAGE_SIZE = 50
const DEFAULT_VISIBLE = 12

function stepIcon(step: AssistantThinkingStep): string {
  if (step.type === 'plan') return STAGE_ICON.plan
  // 卡05：三态——成功 ✅ / 失败 ❌ / 运行中 ⟳（未返回的工具不显示为失败）
  if (step.type === 'tool_call') {
    if (step.ok === true) return '✅'
    if (step.ok === false) return '❌'
    return '⟳'
  }
  return STAGE_ICON[step.stage ?? 'think'] ?? '•'
}

function stepLabel(step: AssistantThinkingStep): string {
  if (step.type === 'tool_call') return step.message
  if (step.type === 'plan') return step.message
  return STAGE_LABEL[step.stage ?? 'think'] ?? step.message
}

function durationSuffix(step: AssistantThinkingStep): string {
  if (step.durationMs === undefined || step.durationMs === null) return ''
  const seconds = Math.round(step.durationMs / 100) / 10
  return `（耗时 ${seconds}s）`
}

function elapsedText(ms: number): string {
  const seconds = Math.max(0, Math.floor(ms / 1000))
  if (seconds < 60) return `${seconds} 秒`
  const minutes = Math.floor(seconds / 60)
  return `${minutes} 分 ${seconds % 60} 秒`
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

/** RF05：忙时每秒走表的时钟（等待计时与耗时显示共用） */
function useTimelineClock(active: boolean): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    setNow(Date.now())
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [active])
  return now
}

export function AssistantThinkingTimeline({ steps, busy, interrupted, startedAt, stale }: AssistantThinkingTimelineProps) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  // RF05：分页加载——默认最近 12 步，"还有 N 步"每页 50 条，全部可达
  const [extraVisible, setExtraVisible] = useState(0)
  const [showAll, setShowAll] = useState(false)
  const isBusy = Boolean(busy && !interrupted)
  // hooks 必须在条件返回之前调用
  const now = useTimelineClock(isBusy)
  if (!steps.length && !busy && !stale) return null

  const visibleCount = showAll ? steps.length : Math.min(steps.length, DEFAULT_VISIBLE + extraVisible)
  const visibleSteps = steps.slice(Math.max(0, steps.length - visibleCount))
  const hiddenCount = steps.length - visibleSteps.length
  const lastStep = steps.at(-1)

  // RF05：无活动时长按最后有效事件计算（无 at 的旧记录退回 startedAt）；
  // 总耗时与无活动分开显示；工具运行与等模型分别提示。
  const lastAt = lastStep?.at ?? startedAt
  const idleMs = lastAt !== undefined ? Math.max(0, now - lastAt) : 0
  const waiting = isBusy && idleMs >= WAIT_NOTICE_MS
  const lastToolRunning = lastStep?.type === 'tool_call' && lastStep.ok === undefined

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
      {stale && !steps.length ? (
        <div className="timeline-stale">旧记录未保存执行过程</div>
      ) : null}
      {isBusy && lastStep ? (
        <div className="timeline-current" data-testid="timeline-current">
          <span>
            当前：{stepLabel(lastStep)}
            {lastStep.type === 'tool_call' && lastStep.ok === undefined && lastStep.at
              ? `（已运行 ${elapsedText(now - lastStep.at)}）`
              : ''}
          </span>
          {startedAt ? <span className="timeline-elapsed">· 已进行 {elapsedText(now - startedAt)}</span> : null}
          {waiting ? (
            <span className="timeline-waiting">
              {lastToolRunning ? '⏳ 工具仍在运行 · ' : '⏳ 等待模型响应 · '}
              {elapsedText(idleMs)}
            </span>
          ) : null}
        </div>
      ) : null}
      {hiddenCount > 0 && !showAll && (
        <button
          type="button"
          className="timeline-more"
          onClick={() => setExtraVisible((v) => Math.min(v + PAGE_SIZE, steps.length))}
        >
          …还有 {hiddenCount} 步
        </button>
      )}
      {steps.length > DEFAULT_VISIBLE && visibleCount >= steps.length && (
        <button
          type="button"
          className="timeline-less"
          onClick={() => { setExtraVisible(0); setShowAll(false) }}
        >
          收起，只看最近 {DEFAULT_VISIBLE} 步
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
                  <span className="timeline-label">{stepLabel(step)}{step.type === 'tool_call' ? durationSuffix(step) : ''}</span>
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
        {isBusy && (
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
