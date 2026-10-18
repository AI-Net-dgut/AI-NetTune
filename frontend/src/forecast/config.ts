import type { ForecastConfig } from "./types";

/** 新预测接口尚未交付：默认仅预览布局。对接后将 mode 改为 api。 */
export const FORECAST_CONFIG: ForecastConfig = {
  mode: "demo",
  previewPath: "/api/forecast/preview",
  analysisPath: "/api/forecast/analyze",
  horizonMinutes: 30,
  timeoutMs: 30_000,
};
