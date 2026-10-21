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

export const FORECAST_HORIZONS = [30, 60, 120, 240, 360, 720, 1440] as const;
export const FORECAST_LIMITS = {
  maxHorizonMinutes: 1440,
  maxHistoryMinutes: 720,
  followStepMinutes: 5,
  maxFrames: 2000,
  maxValuesPerSpectrumPeriod: 256_000,
  displayFrames: 240,
} as const;
