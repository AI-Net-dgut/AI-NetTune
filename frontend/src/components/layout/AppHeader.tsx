import { NavLink } from "react-router-dom";
import logoUrl from "../../assets/logo.svg";
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
          {/* 品牌图标改用用户提供的图形（三节点拓扑 + 辐射弧），与浏览器标签页 favicon 同源 */}
          <img className="ain-brand-icon" src={logoUrl} alt="" aria-hidden="true" />

          <span>
            <strong>AI-NetTune</strong>
            {/* 申报书原文项目名：按 13 + 13 字强制两行，视觉对齐（用户要求） */}
            <small>基于LLM意图驱动的多模态<br />无线网络智能分析与管控平台</small>
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
