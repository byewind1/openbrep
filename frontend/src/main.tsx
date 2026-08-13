import { lazy, StrictMode, Suspense } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './styles/tokens.css'
import './styles.css'

// D4：`?mode=copilot` 时渲染独立 CopilotPage（Archicad 窄面板），否则渲染工作台。
// CopilotPage 不进 workbench store，直接 fetch /api/copilot/*。
const isCopilotMode = new URLSearchParams(window.location.search).get('mode') === 'copilot'
if (isCopilotMode) {
  // 覆盖全局 styles.css 的 min-width:1180px，允许窄面板（320px 可用）
  document.documentElement.classList.add('copilot-mode')
}
const CopilotPage = lazy(() =>
  import('./workbench/copilot/CopilotPage').then((m) => ({ default: m.CopilotPage })),
)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {isCopilotMode ? (
      <Suspense fallback={<div className="copilot-page-loading">加载中…</div>}>
        <CopilotPage />
      </Suspense>
    ) : (
      <App />
    )}
  </StrictMode>,
)
