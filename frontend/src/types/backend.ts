import type { UserAssessment } from './plan';
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
export interface AnomalySpan {
  apId: string; type: string; start: string; end: string; minutes: number;
}
export interface PerformanceHint {
  apId: string; metric: string; value: number; threshold: number;
  cause: string; suggestion: string;
}
export interface DiagnosisFocus {
  kind: 'overview' | 'priority' | 'coverage' | 'congestion' | 'connections';
  label: string;
  status: 'confirmed' | 'not_confirmed' | 'insufficient' | 'no_fault';
  planEligible: boolean;
  targetAps: string[];
  observedAps: string[];
  sections: { title: string; text: string }[];
}
export interface Diagnosis {
  scope: QueryScope; state: "anomaly" | "normal" | "empty";
  /**
   * 状态语义（后端 current_state）。默认分析是"过去 N 分钟"，
   * 窗口里出现过异常 ≠ 此刻仍在故障，必须分开表达：
   *   fault_active 当前仍在故障 / recovered_in_window 窗口内发生过但已恢复
   *   performance_warning 指标偏低但未达故障判据 / normal 正常
   */
  currentState: "fault_active" | "recovered_in_window" | "performance_warning" | "normal";
  lastAnomalyAt: string;
  performanceHints: PerformanceHint[];
  /** 异常范围：window=整窗越阈 / hour=仅个别小时(片段)越阈、整窗均值正常 / none=正常 */
  anomalyScope: "window" | "hour" | "none";
  /** 分钟级异常区间（申报书要求"定位异常的起止时间"），平稳窗口为空数组 */
  anomalySpans: AnomalySpan[];
  anomalyType: string; level: string; score: number; cause: string; suggestion: string;
  /** 大模型基于已验证指标写的方案解读；source 如实标明 llm / template。
   *  之前前端完全没消费这两个字段，导致"大模型在做事但界面看不见"。 */
  planNarrative?: string;
  planNarrativeSource?: "llm" | "template";
  /** 无方案时的原因说明（判定为平稳 / 异常类型无参数方案），避免用户以为功能坏了 */
  planAbsentReason?: string;
  /** 由大模型写的**整段回答**（经 answer_llm 校验器逐条核对后才放行）；
   *  为空表示未通过校验或未启用 → 界面继续用规则文案。 */
  answerFocus?: DiagnosisFocus;
  answerLlm?: string;
  answerCheck?: { ok: boolean; violations: string[]; source: string; attempts?: number };
  affectedAps: string[];
  evidence: { metric: string; value: number; threshold: number; status: string }[];
  candidates: Candidate[]; apCandidates: { id: string; candidates: Candidate[] }[];
  comparison: ApComparison[];
  alarms: { time: string; ap: string; type: string; level: string; cause: string }[];
}
export interface ChainStep {
  tool: string;
  title?: string;
  status?: string;
  summary?: string;
  window?: { start?: string; end?: string };
  horizonMinutes?: number;
  baseTime?: string;
}
export interface ChatResult {
  userAssessment?: UserAssessment;
  reply: string; intent: string; scope: QueryScope; diagnosis: Diagnosis | null;
  plans?: TreatmentPlan[];
  chart: { type: string; option: Record<string, unknown> } | null;
  /** 多步编排（核查清单第 5 条）："找对象 → 查证据 → 看未来"的每一步及结果，
   *  不能只回一句"已执行两步"。 */
  chain?: ChainStep[];
  worstAp?: string;
  horizonMinutes?: number;
  /** 工具回复的 AI 版本（KPI/拓扑/预测等路径）：模型写、校验通过才有值 */
  answerLlm?: string;
  answerCheck?: { ok: boolean; violations: string[]; source: string; attempts?: number };
  /** 用户级分析的主体（问"UE-03"时）：方案落在其主服务 AP，界面必须交代这层关系 */
  subject?: { type: "user"; id: string; servingAps: string[]; primaryAp?: string; samples: number; apConstraint?: string };
  /** 复合编排里预测步骤的摘要（目标 AP、时长、风险条数、首条风险标题）——
   *  复查 P1-8：不能只给"已执行两步"，要能看出预测的是哪个 AP、看到了几条风险。 */
  chainForecast?: {
    baseTime?: string; horizonMinutes?: number; alertCount: number;
    firstTitle?: string; apId?: string;
  };
  /** 意图识别来源：llm=大模型识别 / rule=规则命中 / fallback=规则未命中兜底。
   *  界面必须如实展示，不能让"规则兜底"看起来像"大模型回答"（核查清单第 4 条）。 */
  intentSource?: "llm" | "rule" | "fallback";
  /** 未走大模型的具体原因（模型未配置/调用失败/输出不可解析） */
  intentDegradeReason?: string;
  /** 本句之前该会话是否为空（新会话或被后端淘汰）→ 用于提示"上文已失效" */
  sessionIsNew?: boolean;
}
export type ForecastMetric = "rsrp" | "sinr" | "throughput";
export interface ForecastSeriesItem {
  true_mean: number | null; pred_mean: number | null;
  /** 有量纲的误差指标（dBm / dB / Mbps），对外展示以这两个为主 */
  mae: number | null; rmse: number | null;
  /** 逐点 MAPE；对 dBm 这类负值大绝对值的指标意义有限，仅作参考 */
  mape: number | null;
  bins: string[]; true: number[]; predicted: number[];
  /** 实际切分（后端按 max(int(n*0.8), n-horizon) 计算，不是固定 80/20）。
   *  界面必须按这些值展示，例如"168 点 → 144 训练 / 24 测试（训练占 85.7%）"。 */
  total_points?: number; train_points?: number; test_points?: number; split_points?: number;
}
export interface ForecastResult {
  scenario: string; window_start: string; window_end: string; method: string;
  bin_size_min: number; metrics: ForecastMetric[]; ues: string[];
  series: Record<string, Record<ForecastMetric, ForecastSeriesItem>>;
  /** 降级原因等限制说明（如 Prophet 超时改用移动平均） */
  limitations?: string[];
}
