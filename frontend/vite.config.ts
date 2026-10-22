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
    build: {
      // 拆包：原先 ECharts + antd + 业务代码全进一个 1.1MB 的包（gzip 366kB），
      // 会触发 Vite 的 "chunk 大于 500kB" 警告，也让首屏要等整个包下载完。
      // 这里只按"第三方库 / 业务代码"切两刀，不动任何业务组件的引入方式：
      //   · vendor-echarts：图表库，只在预测/热力图等页面用到，体积最大
      //   · vendor-ui     ：antd 等 UI 库
      //   · index         ：业务代码，改动频繁，独立成包可长期缓存
      // 说明：拆包只影响加载顺序与缓存命中，不改变功能；本地 dev 不生效。
      rollupOptions: {
        output: {
          manualChunks: (id: string) => {
            if (!id.includes("node_modules")) return undefined;
            if (id.includes("echarts") || id.includes("zrender")) return "vendor-echarts";
            if (id.includes("antd") || id.includes("rc-") || id.includes("@ant-design")) return "vendor-ui";
            return "vendor-misc";
          },
        },
      },
      chunkSizeWarningLimit: 600,
    },
  };
});
