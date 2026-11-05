import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
// 改动点：BrowserRouter（history 模式）→ HashRouter。
// 原因：前端部署在 GitHub Pages 静态托管，history 子路由（如 /workbench）
// 直达或刷新时，服务器按真实文件去找会 404；hash 模式把路由放在 # 后面，
// 服务器只请求 index.html，不再出现子路由 404。App.tsx 路由表与链接均
// 无需改动，HashRouter 对其完全兼容，且不影响工作台 hidden 保活逻辑。
import { HashRouter } from 'react-router-dom'
import App from './App.tsx'
import './index.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <HashRouter>
      <App />
    </HashRouter>
  </StrictMode>,
)