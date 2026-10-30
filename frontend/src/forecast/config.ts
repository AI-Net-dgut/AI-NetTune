import type { ForecastConfig } from "./types";

/** main4为当前简化后端的兼容协议；正式模型接入后改用contract。 */
export const FORECAST_CONFIG: ForecastConfig = {
  mode: "api",
  responseProfile: "main4",
  previewPath: "/api/forecast/preview",
  analysisPath: "/api/forecast/analyze",
  horizonMinutes: 120,
  timeoutMs: 45_000,
};

// 主界面只保留**已验证过**的短范围（10-30 清单第 6 点：6 小时以上先隐藏，
// 或明确标为"长期趋势外推，仅供参考"）。6h/12h/24h 的预测没有回测支撑，
// 摆在下拉框里会让人以为"选 24 小时就能提前 24 小时发现故障"。
// 需要长时距做研究时，直接调接口传 horizonMinutes 即可（上限仍是 maxHorizonMinutes）。
export const FORECAST_HORIZONS = [30, 60, 120, 240, 360] as const;
/** 长时距（>6 小时）：接口仍支持，但主界面隐藏，只在需要时显式提示。 */
export const FORECAST_LONG_HORIZONS = [720, 1440] as const;
export const FORECAST_LIMITS = {
  maxHorizonMinutes: 1440,
  maxHistoryMinutes: 720,
  followStepMinutes: 5,
  maxFrames: 2000,
  maxValuesPerSpectrumPeriod: 256_000,
  displayFrames: 240,
} as const;
