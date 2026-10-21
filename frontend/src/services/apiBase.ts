// 后端 API 基地址（单一来源）。
//
// 现状问题：原先所有请求都写死相对路径 `/api/...`，只在 `vite dev` 的
// `server.proxy` 下能通；`vite build` 产物部署到静态服务器后没有代理，
// 全部接口 404，且源码里没有任何运行时覆写入口。
//
// 解法：
//   * 开发（npm run dev）：.env 不填 → API_BASE 为空 → 相对路径 → 走 vite proxy；
//   * 生产（静态部署）：构建时设置 VITE_API_BASE（如 https://api.example.com）
//     → 请求直接打到后端，无需反向代理；
//   * 生产（同源反向代理）：保持为空即可，由 nginx/网关把 /api 转到 8000。
//
// 不要把 API Key 之类的机密写进 VITE_*：它们会被内联进 JS 产物。
const raw = import.meta.env.VITE_API_BASE ?? "";

/** 末尾不留斜杠，便于统一拼接 `${API_BASE}/api/...` */
export const API_BASE = raw.trim().replace(/\/+$/, "");
