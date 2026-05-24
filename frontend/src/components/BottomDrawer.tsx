interface BottomDrawerProps {
  warnings: string[]
}

export function BottomDrawer({ warnings }: BottomDrawerProps) {
  return (
    <section className="bottom-drawer">
      <div className="drawer-tabs">
        <button className="active">编译日志</button>
        <button>脚本编辑</button>
        <button>paramlist.xml</button>
        <button>Revision</button>
      </div>
      <div className="drawer-content">
        {warnings.length ? warnings.map((warning) => <p key={warning}>{warning}</p>) : <p>当前预览没有 warning。</p>}
      </div>
    </section>
  )
}
