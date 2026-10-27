import { Navigate, Route, Routes } from "react-router-dom";
import AppHeader from "./components/layout/AppHeader";
import HistoryPage from "./history/HistoryPage";
import HistoryNotice from "./history/HistoryNotice";
import HomePage from "./pages/home/HomePage";
import WorkbenchPage from "./pages/workbench/WorkbenchPage";
import "./App.css";
// 工作台左右分栏（数据左 / AI右）；最后导入以统一各组件的布局覆盖。
import "./styles/WorkbenchSplitLayout.css";

export default function App() {
  return (
    <div className="ain-app ain-shell">
      {/* 低调的基站与信号背景，不接收鼠标操作 */}
      <svg
        className="ain-background"
        viewBox="0 0 1600 1000"
        preserveAspectRatio="xMidYMid slice"
        aria-hidden="true"
        focusable="false"
      >
        <defs>
          <g
            id="ain-tower"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
          >
            <path d="M0 0 L-28 150 H28 Z M-20 110 L16 65 M20 110 L-16 65 M-28 150 L20 110 M28 150 L-20 110 M-16 65 H16 M-20 110 H20" />
            <path d="M-9 -8 Q-24 5 -9 18 M9 -8 Q24 5 9 18 M-17 -18 Q-43 5 -17 28 M17 -18 Q43 5 17 28" />
          </g>
        </defs>

        <use
          href="#ain-tower"
          transform="translate(45 700)"
        />
        <use
          href="#ain-tower"
          transform="translate(1560 270) scale(.8)"
        />

        <g
          className="ain-signal-wave"
          fill="none"
          stroke="currentColor"
        >
          <circle cx="45" cy="705" r="100" />
          <circle cx="45" cy="705" r="180" />
          <circle cx="45" cy="705" r="260" />
          <circle cx="1560" cy="275" r="100" />
          <circle cx="1560" cy="275" r="180" />
        </g>
      </svg>

      <AppHeader />
      <HistoryNotice />

      <Routes>
        <Route
          path="/"
          element={<HomePage />}
        />

        <Route
          path="/workbench"
          element={<WorkbenchPage />}
        />

        <Route
          path="/prediction"
          element={<Navigate to="/workbench" replace />}
        />

        <Route
          path="/history"
          element={<HistoryPage />}
        />

        <Route
          path="/dashboard"
          element={<Navigate to="/workbench" replace />}
        />

        <Route
          path="*"
          element={<Navigate to="/" replace />}
        />
      </Routes>

      <footer className="ain-footer">
        <span>
          AI-NetTune · 观察网络，研判原因，比较预计效果
        </span>
        <span>
          数据来源与分析范围以工作台标识为准
        </span>
      </footer>
    </div>
  );
}
