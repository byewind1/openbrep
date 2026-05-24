interface BottomDrawerProps {
  warnings: string[]
  compileLog: string[]
}

export function BottomDrawer({ warnings, compileLog }: BottomDrawerProps) {
  return (
    <section className="bottom-drawer">
      <div className="drawer-tabs">
        <button className="active">编译日志</button>
        <button>脚本编辑</button>
        <button>paramlist.xml</button>
        <button>Revision</button>
      </div>
      <div className="drawer-content">
        {compileLog.length ? compileLog.map((entry) => <p key={entry}>{entry}</p>) : <p>尚未编译当前 HSF 项目。</p>}
        {warnings.map((warning) => (
          <p key={warning}>Preview warning: {warning}</p>
        ))}
      </div>
    </section>
  )
}
