/// <reference types="vite/client" />

// 让 import.meta.env 的自定义变量有明确类型，避免 any 到处流传。
// 注意：VITE_ 前缀的变量会在构建时被内联进产物，**只能放非机密信息**。
interface ImportMetaEnv {
  /** 后端 API 基地址。留空 = 同源相对路径 /api（走反向代理或 vite proxy）。 */
  readonly VITE_API_BASE?: string;
  /** vite dev server 的代理目标，仅影响 `npm run dev`。 */
  readonly VITE_PROXY_TARGET?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
