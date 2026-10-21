/** 本模块所有时间均使用 YYYY-MM-DD HH:mm:ss，北京时间。 */
export interface ForecastRequest {
  scenario: string;
  baseTime: string;
  history: { start: string; end: string };
  horizonMinutes: number;
  /** 可选扩展；关注范围在整轮预测内，旧后端可以忽略，但不得改变baseTime。 */
  targetWindow?: { start: string; end: string };
  question?: string;
}
export interface SpectrumBand { id: string; label: string; lowerMHz: number; upperMHz: number }
/** start/end 是时间格子的实际边界，避免不规则采样被画成等间隔。 */
export interface SpectrumFrame { start: string; end: string; values: (number | null)[]; sampleCount?: number }
export interface SpectrumForecast {
  id: string; observationLabel: string;
  observationId?: string;
  metric: "occupancy" | "power"; unit: "%" | "dBm";
  scale: { min: number; max: number };
  bands: SpectrumBand[]; history: SpectrumFrame[]; predicted: SpectrumFrame[];
}
export type SpectrumView = "curve" | "power-time" | "occupancy";
/** 同一观测点的两类数据。曲线与功率时频图共用powerId，不需要第三份预测。 */
export interface SpectrumViews {
  powerId: string | null; occupancyId: string | null;
  recommendedView?: SpectrumView; reason?: string;
}
export interface ForecastPoint { time: string; value: number | null; lower?: number | null; upper?: number | null }
export interface ImpactForecast {
  id: string; targetLabel: string; label: string; unit: string;
  history: ForecastPoint[]; predicted: ForecastPoint[];
  threshold?: { value: number; direction: "above" | "below" };
}
export interface ForecastAlert {
  id: string; target: string; title: string; summary: string;
  severity: "high" | "medium" | "low"; start: string; end: string;
  spectrumId: string | null; bandIds: string[]; impactId: string | null;
  spectrumViews?: SpectrumViews;
}
export interface ForecastResult {
  /** Frontend explanation for the explicitly selected backend compatibility profile. */
  integrationNotice?: string;
  /** 后续模型接口可提供方法与限制，页面按返回值展示。 */
  method?: string;
  limitations?: string[];
  id: string; scenario: string; baseTime: string;
  history: { start: string; end: string }; forecast: { start: string; end: string };
  generatedAt: string; source: "demo" | "simulation" | "measurement";
  status: "ready" | "insufficient_data" | "unavailable"; message: string;
  alerts: ForecastAlert[]; spectra: SpectrumForecast[]; impacts: ImpactForecast[];
  /** 无风险时可指定概览数据；不指定则兼容旧版首张单图。 */
  spectrumViews?: SpectrumViews;
}
export interface ForecastAnalysis {
  forecastId: string; baseTime: string; alertId: string | null;
  source: "demo" | "llm" | "rule"; reply: string;
}
export interface ForecastConfig {
  responseProfile?: "main4" | "contract";
  mode: "demo" | "api"; previewPath: string; analysisPath: string;
  horizonMinutes: number; timeoutMs: number;
}
export interface ForecastHandoff {
  result: ForecastResult; alertId: string | null;
  question?: string; targetWindow?: ForecastRequest['targetWindow'];
}
