import type { TreatmentPlan } from "./plan";
export interface QueryScope { scenario: string; start: string; end: string }
export const METRIC_META = {
  rsrpDbm: { label: "RSRP", unit: "dBm" },
  sinrDb: { label: "SINR", unit: "dB" },
  throughputMbps: { label: "吞吐量", unit: "Mbps" },
  latencyMs: { label: "时延", unit: "ms" },
  packetLossPct: { label: "丢包率", unit: "%" },
  retransmissions: { label: "重传次数", unit: "次" },
  demandMbps: { label: "业务需求", unit: "Mbps" },
  resourceUtilizationPct: { label: "PRB 占用率", unit: "%" },
};
export type MetricKey = keyof typeof METRIC_META;
export type MetricRecord = Record<MetricKey, number> & {
  ueId: string; apId: string; timestamp: string; backendTime: string;
  x: number; y: number; apX: number; apY: number; status: string;
};
export interface NetworkNode {
  id: string; type: "ap" | "ue"; x: number; y: number;
  /** 仅保留已到达的最近采样，不代表本帧/本窗口仍有采样。 */
  lastSeen?: string;
  /** 以下两个字段由图层按所选口径计算，不改变原始采样。 */
  missingSample?: boolean;
  positionNote?: string;
}
export interface NetworkLink {
  id: string; source: string; target: string;
  rsrp: number; sinr: number; throughput: number;
  primary?: boolean;
}
export interface HeatCell { x: number; y: number; value: number; count: number }
export interface SpatialPoint {
  x: number; y: number; value: number; variance: number | null;
}
export interface SpatialMapData {
  scope: QueryScope;
  metric: "rsrp";
  method: "kriging" | "grid_mean";
  gridCount: number;
  samplePoints: number;
  points: SpatialPoint[];
  cellSize: { x: number; y: number };
  varianceNotice: string;
}
export interface DashboardData {
  scope: QueryScope; generatedAt: string; metrics: MetricRecord[];
  /** 回放采样热力图使用最新帧；旧工作台未设置时按历史窗口解释。 */
  sampledFrameTime?: string | null;
  area: { xMin: number; yMin: number; xMax: number; yMax: number };
  nodes: NetworkNode[]; links: NetworkLink[];
  gridSize: number; cells: HeatCell[]; apMeans: [number, number, number][];
}
export interface Candidate {
  rank: number; cause: string; score: number;
  support: string[]; against: string[]; checks: string[];
}
export interface ApComparison {
  id: string; type: string; level: string;
  rsrp: number; sinr: number; throughput: number; prb: number;
}
export interface Diagnosis {
  scope: QueryScope; state: "anomaly" | "normal" | "empty";
  anomalyType: string; level: string; score: number; cause: string; suggestion: string;
  affectedAps: string[];
  evidence: { metric: string; value: number; threshold: number; status: string }[];
  candidates: Candidate[]; apCandidates: { id: string; candidates: Candidate[] }[];
  comparison: ApComparison[];
  alarms: { time: string; ap: string; type: string; level: string; cause: string }[];
}
export interface ChatResult {
  reply: string; intent: string; scope: QueryScope; diagnosis: Diagnosis | null;
  plans?: TreatmentPlan[];
  chart: { type: string; option: Record<string, unknown> } | null;
}
export type ForecastMetric = "rsrp" | "sinr" | "throughput";
export interface ForecastSeriesItem {
  true_mean: number | null; pred_mean: number | null;
  /** 有量纲的误差指标（dBm / dB / Mbps），对外展示以这两个为主 */
  mae: number | null; rmse: number | null;
  /** 逐点 MAPE；对 dBm 这类负值大绝对值的指标意义有限，仅作参考 */
  mape: number | null;
  bins: string[]; true: number[]; predicted: number[];
}
export interface ForecastResult {
  scenario: string; window_start: string; window_end: string; method: string;
  bin_size_min: number; metrics: ForecastMetric[]; ues: string[];
  series: Record<string, Record<ForecastMetric, ForecastSeriesItem>>;
  /** 降级原因等限制说明（如 Prophet 超时改用移动平均） */
  limitations?: string[];
}
