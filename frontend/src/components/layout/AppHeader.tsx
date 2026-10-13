import { NavLink } from "react-router-dom";
import "./AppHeader.css";

const navigation = [
  { to: "/", label: "首页" },
  { to: "/workbench", label: "网络工作台" },
  { to: "/history", label: "分析记录" },
];

export default function AppHeader() {
  return (
    <header className="ain-header">
      <div className="ain-header-inner">
        <NavLink
          to="/"
          className="ain-brand"
          aria-label="AI-NetTune 首页"
        >
          <svg
            className="ain-brand-icon"
            viewBox="0 0 40 40"
            fill="none"
            aria-hidden="true"
          >
            <path
              d="M20 15 L12 33 H28 Z M16 25 H24 M15 28 L23 21 M25 28 L17 21"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinejoin="round"
            />

            <circle
              cx="20"
              cy="12"
              r="2.5"
              fill="currentColor"
            />

            <path
              d="M13 6 Q7 12 13 18 M27 6 Q33 12 27 18 M8 2 Q-1 12 8 23 M32 2 Q41 12 32 23"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
            />
          </svg>

          <span>
            <strong>AI-NetTune</strong>
            <small>无线网络诊断与仿真工作台</small>
          </span>
        </NavLink>

        <nav className="ain-nav" aria-label="主导航">
          {navigation.map(item => (
            <NavLink key={item.to} to={item.to} end>
              {item.label}
            </NavLink>
          ))}
        </nav>

        <span className="ain-header-status">
          <i aria-hidden="true" />
          智能交互 · 多维可视化
        </span>
      </div>
    </header>
  );
}
