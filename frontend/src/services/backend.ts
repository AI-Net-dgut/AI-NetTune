import { parsePlans, parseSimulation } from "./planContract";
import type { SimulationResult, TreatmentPlan } from "../types/plan";
import type {
  QueryScope,
  MetricRecord,
  DashboardData,
  Diagnosis,
  Candidate,
  ChatResult,
  HeatCell,
  NetworkNode,
  ForecastResult,
  ForecastMetric,
  SpatialMapData,
} from "../types/backend";
import { parseSpatialMap } from "./spatialMap";
import { API_BASE } from "./apiBase";

type Obj = Record<string, unknown>;
type Fetcher = typeof fetch;
const MAX_ROWS = 1000;

export const DATASET_BOUNDS = {
  min: "2026-01-01T00:00:00",
  max: "2026-01-30T23:59:59",
};

export const DEFAULT_SCOPE: QueryScope = {
  scenario: "校园",
  start: "2026-01-01 10:00:00",
  end: "2026-01-01 11:59:59",
};

export const CASE_WINDOWS = [
  {
    label: "覆盖案例",
    start: "2026-01-01 10:00:00",
    end: "2026-01-01 11:59:59",
  },
  {
    label: "拥塞案例",
    start: "2026-01-01 20:00:00",
    end: "2026-01-01 21:59:59",
  },
  {
    label: "边缘案例",
    start: "2026-01-20 01:00:00",
    end: "2026-01-20 02:59:59",
  },
];

const obj = (v: unknown): Obj => {
  if (!v || typeof v !== "object" || Array.isArray(v)) throw new Error("接口数据结构不正确");
  return v as Obj;
};
const arr = (v: unknown): unknown[] => Array.isArray(v) ? v : [];
const str = (v: unknown) => typeof v === "string" ? v : "";
const strings = (v: unknown) => arr(v).filter((s): s is string => typeof s === "string");
// 缺失指标保留为 NaN，图表转换为缺口；不能把空字符串变成 0。
export function numeric(v: unknown): number {
  if (typeof v !== "number" && typeof v !== "string") return NaN;
  if (typeof v === "string" && !v.trim()) return NaN;
  const n = Number(v); return Number.isFinite(n) ? n : NaN;
}
function requiredString(v: unknown, name: string) {
  const s = str(v); if (!s) throw new Error(`接口缺少 ${name}`); return s;
}
function coordinate(v: unknown) {
  const n = numeric(v); if (!Number.isFinite(n) || n < 0) throw new Error("接口坐标无效");
  return n;
}
export function toEpoch(value: string) {
  if (!/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(value)) throw new Error("时间格式应为 YYYY-MM-DD HH:mm:ss");
  const n = Date.parse(value.replace(" ", "T") + "+08:00");
  if (!Number.isFinite(n) || backendTime(n) !== value) throw new Error("时间值无效");
  return n;
}
function backendTime(n: number) {
  return new Date(n + 8 * 3600_000).toISOString().slice(0, 19).replace("T", " ");
}
export function scopeLabel(scope: QueryScope) {
  return `${scope.scenario} · ${scope.start} — ${scope.end}（北京时间）`;
}
function validateScope(scope: QueryScope) {
  const start = toEpoch(scope.start), end = toEpoch(scope.end);
  if (!scope.scenario.trim() || end < start) throw new Error("请选择场景，结束时间不早于开始时间");
  return { start, end };
}
async function request(path: string, signal?: AbortSignal, body?: unknown, fetcher: Fetcher = fetch): Promise<Obj> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  if (signal?.aborted) abort();
  signal?.addEventListener("abort", abort, { once: true });
  const timer = setTimeout(abort, 45_000);
  try {
    const res = await fetcher(`${API_BASE}/api${path}`, {
      method: body ? "POST" : "GET", signal: controller.signal,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) throw new Error(`后端请求失败（HTTP ${res.status}）`);
    let json: unknown;
    try { json = await res.json(); } catch (cause) { throw new Error("接口未返回 JSON，请检查 Vite 代理和后端端口", { cause }); }
    const envelope = obj(json);
    if (envelope.code !== 0) throw new Error(str(envelope.message) || "后端业务错误");
    return obj(envelope.data);
  } catch (error) {
    if (controller.signal.aborted) {
      if (signal?.aborted) throw new DOMException("请求已取消", "AbortError");
      throw new Error("请求超时，请检查后端及模型调用状态", { cause: error });
    }
    if (error instanceof TypeError) throw new Error("无法连接后端，请检查API服务及代理配置", { cause: error });
    throw error;
  } finally { clearTimeout(timer); signal?.removeEventListener("abort", abort); }
}
export function parseRecord(value: unknown): MetricRecord {
  const r = obj(value), time = requiredString(r.timestamp, "timestamp"); toEpoch(time);
  return {
    ueId: requiredString(r.user_id, "user_id"), apId: requiredString(r.ap_id, "ap_id"),
    timestamp: time.replace(" ", "T") + "+08:00", backendTime: time,
    x: coordinate(r.ue_x), y: coordinate(r.ue_y), apX: coordinate(r.ap_x), apY: coordinate(r.ap_y),
    rsrpDbm: numeric(r.rsrp), sinrDb: numeric(r.sinr), throughputMbps: numeric(r.throughput),
    latencyMs: numeric(r.latency), packetLossPct: numeric(r.packet_loss),
    resourceUtilizationPct: numeric(r.prb_usage), retransmissions: numeric(r.retransmission_count),
    demandMbps: numeric(r.traffic_demand), status: str(r.network_status),
  };
}
export async function fetchRecords(scope: QueryScope, signal?: AbortSignal, fetcher: Fetcher = fetch): Promise<MetricRecord[]> {
  const { start, end } = validateScope(scope);
  async function part(a: number, b: number): Promise<MetricRecord[]> {
    const params = new URLSearchParams({ scenario: scope.scenario, start_time: backendTime(a), end_time: backendTime(b), limit: String(MAX_ROWS) });
    const data = await request(`/data/query?${params}`, signal, undefined, fetcher);
    if (!Array.isArray(data.records)) throw new Error("查询接口缺少 records 数组");
    if (data.records.length >= MAX_ROWS) {
      if (a === b) throw new Error("单秒数据达到接口上限，存在截断，请队友增加分页接口");
      const mid = a + Math.floor((b - a) / 2000) * 1000;
      return [...await part(a, mid), ...await part(mid + 1000, b)];
    }
    return data.records.map(parseRecord);
  }
  const rows: MetricRecord[] = [];
  for (let a = start; a <= end; a += 2 * 3600_000) {
    if (signal?.aborted) throw new DOMException("请求已取消", "AbortError");
    rows.push(...await part(a, Math.min(end, a + 2 * 3600_000 - 1000)));
  }
  return rows.sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp));
}
export function aggregateCells(records: MetricRecord[], size: number): HeatCell[] {
  const cells = new Map<string, { x: number; y: number; sum: number; count: number }>();
  for (const r of records) {
    if (!Number.isFinite(r.rsrpDbm)) continue;
    const x = Math.floor(r.x / size) * size, y = Math.floor(r.y / size) * size;
    const key = `${x},${y}`, c = cells.get(key) || { x, y, sum: 0, count: 0 };
    c.sum += r.rsrpDbm; c.count++; cells.set(key, c);
  }
  return [...cells.values()].map(c => ({ x: c.x, y: c.y, value: c.sum / c.count, count: c.count }));
}
export async function loadDashboard(scope: QueryScope, signal?: AbortSignal, fetcher: Fetcher = fetch): Promise<DashboardData | null> {
  const metrics = await fetchRecords(scope, signal, fetcher);
  if (!metrics.length) return null;
  const latest = metrics.at(-1)!;
  // 不传 timestamp：后端对 /network/topology/basic 是按整个窗口聚合的
  // （窗口内主服务 AP + 连接均值），传单帧时刻并不会改变返回结果。
  // 原实现还额外校验"拓扑时刻必须等于最新采样时刻"，一旦不一致就整体 throw，
  // 使 AP 静态部署直接失败、真实原因又被上层吞成一句提示。
  const params = new URLSearchParams({ scenario: scope.scenario, start_time: scope.start, end_time: scope.end });
  // 空间估计由 fetchSpatialMap 独立加载，避免阻塞基础数据和拓扑。
  const topology = await request(`/network/topology/basic?${params}`, signal, undefined, fetcher);
  if (!Array.isArray(topology.nodes) || !Array.isArray(topology.links)) throw new Error("拓扑接口返回结构无效（缺少 nodes 或 links 数组）");
  
    // 1. 获取当前查询窗口内每个 UE 的最新位置
  const latestUePosition = new Map<
    string,
    { x: number; y: number }
   >();

    // metrics 已按时间升序排列
   for (const r of metrics) {
     latestUePosition.set(r.ueId, {
       x: r.x,
       y: r.y,
     });
   }

    // 2. 构建拓扑节点
   const nodes = topology.nodes.map((v): NetworkNode => {
     const n = obj(v);
     const type = n.type;

     if (type !== "ap" && type !== "ue") {
       throw new Error("拓扑节点类型无效");
     }

     const id = requiredString(n.id, "node.id");

      // UE 使用最新采样坐标，AP 保留后端坐标
     const position =
       type === "ue" ? latestUePosition.get(id) : undefined;

     return {
       id,
       type,
       x: position?.x ?? coordinate(n.x),
       y: position?.y ?? coordinate(n.y),
     };
   });

  const links = topology.links.map(v => {
    const l = obj(v), source = requiredString(l.source, "link.source"), target = requiredString(l.target, "link.target");
    return { id: `${source}->${target}`, source, target, rsrp: numeric(l.rsrp), sinr: numeric(l.sinr), throughput: numeric(l.throughput),
      primary: typeof l.primary === "boolean" ? l.primary : undefined };
  });
  // 保留旧类型字段以兼容已有调用；主地图使用独立的空间结果。
  const apMeans: [number, number, number][] = [];
  const gridSize = 20;
  const allX = metrics.map(r => r.x).concat(nodes.map(n => n.x));
  const allY = metrics.map(r => r.y).concat(nodes.map(n => n.y));
  return {
    scope: { ...scope }, generatedAt: latest.timestamp, metrics, nodes, links,
    area: {
      xMin: Math.floor(Math.min(...allX) / gridSize) * gridSize,
      yMin: Math.floor(Math.min(...allY) / gridSize) * gridSize,
      xMax: Math.ceil(Math.max(...allX) / gridSize) * gridSize,
      yMax: Math.ceil(Math.max(...allY) / gridSize) * gridSize,
    }, gridSize, cells: aggregateCells(metrics, gridSize), apMeans,
  };
}
export async function fetchSpatialMap(scope: QueryScope, signal?: AbortSignal, fetcher: Fetcher = fetch): Promise<SpatialMapData> {
  validateScope(scope);
  const params = new URLSearchParams({ scenario: scope.scenario, start_time: scope.start, end_time: scope.end, metric: "rsrp" });
  const data = await request(`/data/heatmap?${params}`, signal, undefined, fetcher);
  return parseSpatialMap(data, scope);
}
function candidate(value: unknown, i: number): Candidate {
  const c = obj(value);
  return { rank: numeric(c.rank) || i + 1, cause: str(c.cause), score: numeric(c.confidence),
    support: strings(c.support_evidence), against: strings(c.against_evidence), checks: strings(c.pending_checks) };
}
export function parseDiagnosis(value: unknown, scope: QueryScope): Diagnosis {
  const d = obj(value);
  if (typeof d.is_anomaly !== "boolean") throw new Error("诊断接口缺少 is_anomaly");
  const comparison = arr(d.ap_comparison).map(v => {
    const a = obj(v); return { id: str(a.ap_id), type: str(a.anomaly_type), level: str(a.level),
      rsrp: numeric(a.rsrp_avg), sinr: numeric(a.sinr_avg), throughput: numeric(a.thr_avg), prb: numeric(a.prb_avg) };
  });
  return {
    scope: { ...scope }, state: d.is_anomaly ? "anomaly" : comparison.length ? "normal" : "empty",
    anomalyType: str(d.anomaly_type), level: str(d.level), score: numeric(d.confidence),
    cause: str(d.cause) || str(d.reason), suggestion: str(d.suggestion), affectedAps: strings(d.affected_aps),
    evidence: arr(d.kpi_evidence).map(v => { const e = obj(v); return { metric: str(e.metric), value: numeric(e.avg_value), threshold: numeric(e.threshold), status: str(e.status) }; }),
    candidates: arr(d.candidates).map(candidate),
    apCandidates: arr(d.ap_candidates).map(v => { const a = obj(v); return { id: str(a.ap_id), candidates: arr(a.candidates).map(candidate) }; }),
    comparison, alarms: arr(d.alarm_history).map(v => { const a = obj(v); return { time: str(a.time), ap: str(a.ap_id), type: str(a.anomaly_type), level: str(a.level), cause: str(a.cause) }; }),
  };
}
export async function analyzeNetwork(scope: QueryScope, signal?: AbortSignal): Promise<Diagnosis> {
  validateScope(scope);
  const data = await request("/analyze/anomaly", signal, { scenario: scope.scenario, window_start: scope.start, window_end: scope.end, metrics: ["rsrp", "sinr", "throughput", "prb_usage", "packet_loss"] });
  return parseDiagnosis(data, scope);
}
/** 用用户修改后的参数重算"原配置 vs 方案"的预计效果（接口14）。 */
export async function simulatePlan(scope: QueryScope, plan: TreatmentPlan, values: Record<string, string>,
  apId: string, signal?: AbortSignal): Promise<SimulationResult | null> {
  validateScope(scope);
  const data = await request("/plan/simulate", signal, {
    plan_id: plan.id, scenario: scope.scenario,
    window_start: scope.start, window_end: scope.end,
    ap_id: apId, parameter_values: values,
  });
  return parseSimulation(data, scope);
}
/** 生成分析报告（接口4）。返回可直接打开的下载地址与报告编号。 */
export async function exportReport(scope: QueryScope, format: "md" | "html" = "html",
  signal?: AbortSignal): Promise<{ url: string; reportId: string; format: string; bytes: number }> {
  validateScope(scope);
  const data = await request("/export/report", signal, {
    scenario: scope.scenario, start_time: scope.start, end_time: scope.end,
    format, include_chart: true,
  });
  const path = requiredString(data.file_url, "file_url");
  return {
    // file_url 形如 /api/download/reports/xxx，与 API_BASE 拼接后开发/生产都可用
    url: `${API_BASE}${path}`, reportId: str(data.report_id) || "report",
    format: str(data.format) || format, bytes: numeric(data.bytes) || 0,
  };
}
export async function chatNetwork(message: string, scenario: string, window?: QueryScope, signal?: AbortSignal, currentTime?: string): Promise<ChatResult> {
  const data = await request("/agent/chat", signal, {
    message, scenario, session_id: "frontend-p0",
    // 第4周：把当前展示窗口传给后端工具，保证"对话口径=页面口径"
    window_start: window?.start, window_end: window?.end, current_time: currentTime,
  });
  const ref = data.query_ref ? obj(data.query_ref) : {};
  // 工具返回的实际窗口（后端透传）；工作台始终显式传入窗口，避免后端末尾默认范围
  const scope = {
    scenario: str(ref.scenario) || scenario,  
    start: str(ref.start_time) || window?.start || DEFAULT_SCOPE.start,
    end: str(ref.end_time) || window?.end || DEFAULT_SCOPE.end,
  };
  validateScope(scope);
  const intent = str(data.intent), rawChart = data.chart ? obj(data.chart) : null;
  return {
    reply: requiredString(data.reply, "reply"), intent, scope,
    plans: parsePlans(data.plans, scope),
    diagnosis: intent === "fault_diagnosis" && data.detail ? parseDiagnosis(data.detail, scope) : null,
    chart: rawChart?.option ? { type: str(rawChart.type), option: obj(rawChart.option) } : null,
  };
}
export async function fetchForecast(scope: QueryScope, signal?: AbortSignal): Promise<ForecastResult> {
  validateScope(scope);
  const data = await request("/analyze/forecast", signal, {
    scenario: scope.scenario, window_start: scope.start, window_end: scope.end,
  });
  const ues = Array.isArray(data.ues) ? data.ues.map(String) : [];
  const metrics = Array.isArray(data.metrics) ? data.metrics.map(String) : [];
  const raw = obj(data.series);
  const series: ForecastResult["series"] = {};
  for (const ue of ues) {
    const byMetric = obj(raw[ue]);
    series[ue] = {} as ForecastResult["series"][string];
    for (const m of metrics) {
      const item = obj(byMetric[m]);
      series[ue][m as ForecastMetric] = {
        true_mean: item.true_mean == null ? null : numeric(item.true_mean),
        pred_mean: item.pred_mean == null ? null : numeric(item.pred_mean),
        mae: item.mae == null ? null : numeric(item.mae),
        rmse: item.rmse == null ? null : numeric(item.rmse),
        mape: item.mape == null ? null : numeric(item.mape),
        bins: Array.isArray(item.bins) ? item.bins.map(String) : [],
        true: Array.isArray(item.true) ? item.true.map(v => numeric(v)) : [],
        predicted: Array.isArray(item.predicted) ? item.predicted.map(v => numeric(v)) : [],
      };
    }
  }
  return {
    scenario: str(data.scenario), window_start: str(data.window_start), window_end: str(data.window_end),
    method: str(data.method), bin_size_min: numeric(data.bin_size_min) || 10,
    metrics: metrics as ForecastMetric[], ues, series,
    limitations: strings(data.limitations),
  };
}
