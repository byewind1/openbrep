import type { ReactNode } from 'react'

interface PanelEmptyProps {
  /** 文字符号图标（不引图标库） */
  icon?: string
  title: string
  hint?: string
  /** 附加内容（如 AI 面板的示例提示词 chips） */
  children?: ReactNode
  /** 浮层模式：绝对定位浮在容器上、不拦截指针事件（3D 视口空态用） */
  overlay?: boolean
}

/** 三处空态（3D 预览 / 编辑器舞台 / AI 面板）共用的居中空态层。 */
export function PanelEmpty({ icon, title, hint, children, overlay = false }: PanelEmptyProps) {
  return (
    <div className={overlay ? 'panel-empty panel-empty-overlay' : 'panel-empty'}>
      {icon ? (
        <div className="panel-empty-icon" aria-hidden="true">
          {icon}
        </div>
      ) : null}
      <p className="panel-empty-title">{title}</p>
      {hint ? <p className="panel-empty-hint">{hint}</p> : null}
      {children}
    </div>
  )
}
