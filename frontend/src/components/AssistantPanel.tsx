export function AssistantPanel() {
  return (
    <aside className="assistant-panel">
      <div className="panel-heading">
        <h2>AI 助手</h2>
        <span>POC</span>
      </div>
      <div className="chat-placeholder">
        <p>这里会承接生成、解释、修复和参数化建议。</p>
        <button>生成修改建议</button>
      </div>
    </aside>
  )
}
