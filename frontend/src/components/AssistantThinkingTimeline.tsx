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

/** 卡05：等待提示阈值（秒）——无新事件超过该时长显示等待原因 */
const WAIT_NOTICE_SECONDS = 15

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

function elapsedText(startedAt: number, now: number): string {
  const seconds = Math.max(0, Math.floor((now - startedAt) / 1000))
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

/** 卡05：等待计时器——顶层显示当前阶段与已耗时；超过阈值提示等待原因 */
function useWaitTicker(busy: boolean, startedAt: number | undefined) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!busy || !startedAt) return
    setNow(Date.now())
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [busy, startedAt])
  if (!busy || !startedAt) return null
  const elapsedSeconds = Math.max(0, Math.floor((Date.now() - startedAt) / 1000))
  const waiting = elapsedSeconds >= WAIT_NOTICE_SECONDS
  return { elapsed: elapsedText(startedAt, now), waiting }
}

export function AssistantThinkingTimeline({ steps, busy, interrupted, startedAt, stale }: AssistantThinkingTimelineProps) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  // 卡05：真正的“查看全部”——展开全部历史步骤（不只是标记可见 12 步）
  const [showAll, setShowAll] = useState(false)
  const isBusy = Boolean(busy && !interrupted)
  // hooks 必须在条件返回之前调用
  const wait = useWaitTicker(isBusy, startedAt)
  if (!steps.length && !busy && !stale) return null

  const visibleSteps = showAll ? steps : steps.slice(-12)
  const hiddenCount = steps.length - visibleSteps.length
  const lastStep = steps.at(-1)

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
      {busy && !interrupted && lastStep ? (
        <div className="timeline-current" data-testid="timeline-current">
          <span>当前：{stepLabel(lastStep)}</span>
          {wait ? (
            <span className={wait.waiting ? 'timeline-waiting' : 'timeline-elapsed'}>
              {wait.waiting ? '⏳ 等待模型响应/工具仍在运行 · ' : '· 已进行 '}
              {wait.elapsed}
            </span>
          ) : null}
        </div>
      ) : null}
      {hiddenCount > 0 && (
        <button
          type="button"
          className="timeline-more"
          onClick={() => setShowAll(true)}
        >
          …还有 {hiddenCount} 步
        </button>
      )}
      {showAll && steps.length > 12 && (
        <button
          type="button"
          className="timeline-less"
          onClick={() => setShowAll(false)}
        >
          收起，只看最近 12 步
        </button>
      )}
      <ul className="timeline-list">
        {visibleSteps.map((step, i) => {
          const globalIndex = (showAll ? 0 : steps.length - visibleSteps.length) + i
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
