import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// 说明：
//  * 下面的 proxy 只对 `npm run dev` 生效；`vite preview` 和构建产物都不会读它。
//  * 构建产物部署到静态服务器时，用 VITE_API_BASE 指定后端地址（见 .env.example / DEPLOY.md）。
//  * host: true 让局域网内其他设备也能访问 dev server（答辩多机联调需要）。
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const proxyTarget =
    env.VITE_PROXY_TARGET || env.API_PROXY_TARGET || "http://127.0.0.1:8000";

  return {
    plugins: [react()],
    server: {
      host: true,
      proxy: {
        "/api": { target: proxyTarget, changeOrigin: true },
      },
    },
  };
});
