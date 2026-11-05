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
const MAX_ROWS = 5000;

// DATASET_BOUNDS / CASE_WINDOWS 已删除（审查报告 B-4）：
//   - DATASET_BOUNDS 是写死的时间范围，与实际加载的数据无关，且 0 引用；
//   - CASE_WINDOWS 是三个手写的"案例起点"时间戳，0 引用（案例按钮从未上线），
//     改数据即失效。现在改为由后端 GET /api/data/events 读取 data/fault_events.csv，
//     事件清单是单一事实来源，见 fetchFaultEvents()。

/**
 * 兜底窗口（审查报告 B-4 的尾巴）。
 *
 * 这不是"数据集范围"，而是一个**演示用例**的固定窗口：对应 fault_events.csv 的
 * EV-001（AP-02 弱覆盖，2026-01-01 10:00–12:00），与 replay/config.ts 的回放区间
 * 以及后端 DEFAULT_SCOPE 口径保持一致。
 *
 * 只在调用方没给出窗口时作为兜底。换数据或换演示事件时**必须同步这里**，
 * 否则会查到空结果。彻底的数据驱动方案：用 fetchFaultEvents() 取事件起止时间
 * （见 CaseEventButtons 的做法），不要再往这里加第二个手写时间戳。
 */
export const DEFAULT_SCOPE: QueryScope = {
  scenario: "校园",
  start: "2026-01-01 10:00:00",
  end: "2026-01-01 11:59:59",
};

/** 区域态势帧序列（接口16）：每帧是一张 grid×grid 的二维场 */
export interface SpatialFrame {
  start: string;
  end: string;
  values: (number | null)[][];
}
export interface SpatialFrameSeries {
  scenario: string;
  metric: string;
  frameMinutes: number;
  xRange: [number, number];
  yRange: [number, number];
  rows: number;
  cols: number;
  past: SpatialFrame[];
  future: SpatialFrame[];
  note: string;
}

/**
 * 读取区域电磁态势帧序列（过去N帧 + 未来N帧）。
 *
 * 与 fetchFaultEvents 同样的理由：态势图的参考形态是"过去帧 → 未来帧"的二维区域图，
 * 而接口11 的频谱预测是频率维度的一条带，只能画成横纹。帧序列由接口16 提供。
 */
export async function fetchSpatialFrames(
  scenario: string, baseTime: string, signal?: AbortSignal,
  options: { historyMinutes?: number; frameMinutes?: number; pastFrames?: number; futureFrames?: number; metric?: string } = {},
): Promise<SpatialFrameSeries> {
  // 注意：request() 的规则是"有 body 就 POST"，而这些是 GET 接口，
  // 传 body 会变成 POST → 405。所以参数必须拼进查询字符串。
  const query = new URLSearchParams({
    scenario,
    baseTime,
    historyMinutes: String(options.historyMinutes ?? 48),
    frameMinutes: String(options.frameMinutes ?? 6),
    pastFrames: String(options.pastFrames ?? 6),
    futureFrames: String(options.futureFrames ?? 3),
    metric: options.metric ?? "rsrp",
  }).toString();
  const data = await request(`/forecast/spatial_frames?${query}`, signal);
  const shape = obj(data.shape ?? {});
  const toFrame = (v: unknown): SpatialFrame => {
    const f = obj(v);
    return {
      start: str(f.start),
      end: str(f.end),
      values: Array.isArray(f.values)
        ? f.values.map((row) => (Array.isArray(row) ? row.map((x) => (x == null ? null : numeric(x))) : []))
        : [],
    };
  };
  const pick = (v: unknown) => (Array.isArray(v) ? v.map(toFrame) : []);
  return {
    scenario: str(data.scenario),
    metric: str(data.metric),
    frameMinutes: numeric(data.frame_minutes) || 6,
    xRange: [(numeric((data.x_range as unknown[])?.[0])), (numeric((data.x_range as unknown[])?.[1]))],
    yRange: [(numeric((data.y_range as unknown[])?.[0])), (numeric((data.y_range as unknown[])?.[1]))],
    rows: numeric(shape.rows) || 0,
    cols: numeric(shape.cols) || 0,
    past: pick(data.past),
    future: pick(data.future),
    note: str(data.note),
  };
}


/** 故障事件（来自 data/fault_events.csv，单一事实来源） */
export interface FaultEvent {
  eventId: string;
  scenario: string;
  apId: string;
  faultType: string;
  start: string;
  end: string;
  rampMinutes: number;
  strength: number;
  labeledFromExpect: string;
  /** 真实首次越阈时刻（yyyy-MM-dd HH:mm）；注入开始 ≠ 真的越阈，注入通常早十几分钟 */
  crossingAt: string;
  crossingBands: number | null;
  crossingMetric: string;
}

/**
 * 读取故障事件清单。
 *
 * 为什么不在前端写死案例窗口：审查报告 B-4 指出 CASE_WINDOWS 是手写时间戳，
 * 数据一换就失效；而 data_generator.py 每次运行都会同步输出 fault_events.csv，
 * 由后端读它、前端消费它，"事件"才有唯一来源。
 */
export async function fetchFaultEvents(scenario?: string, signal?: AbortSignal): Promise<FaultEvent[]> {
  // 同 fetchSpatialFrames：GET 接口不能传 body，否则会被 request() 当成 POST → 405
  const query = scenario ? `?${new URLSearchParams({ scenario }).toString()}` : "";
  const data = await request(`/data/events${query}`, signal);
  const list = Array.isArray(data.events) ? data.events : [];
  return list.map((v) => {
    const e = obj(v);
    return {
      eventId: str(e.event_id),
      scenario: str(e.scenario),
      apId: str(e.ap_id),
      faultType: str(e.fault_type),
      start: str(e.start_time),
      end: str(e.end_time),
      rampMinutes: numeric(e.ramp_minutes),
      strength: numeric(e.strength),
      labeledFromExpect: str(e.labeled_from_expect),
      // 真实首次越阈（≥40/50 且连续 3 分钟）；注入时刻通常比它早十几分钟
      crossingAt: e.crossingAt ? str(e.crossingAt) : "",
      crossingBands: e.crossingBands == null ? null : numeric(e.crossingBands),
      crossingMetric: e.crossingMetric ? str(e.crossingMetric) : "",
    };
  });
}

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
  return { rank: numeric(c.rank) || i + 1, cause: str(c.cause), score: numeric(c.rule_score ?? c.confidence),
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
    // 状态语义：优先用后端 current_state；后端未给时按是否异常兜底
    currentState: (str(d.current_state) || (d.is_anomaly ? "fault_active" : "normal")) as Diagnosis["currentState"],
    lastAnomalyAt: str(d.last_anomaly_at),
    performanceHints: arr(d.performance_hints).map((v) => {
      const h = obj(v);
      return { apId: str(h.ap_id), metric: str(h.metric), value: numeric(h.value),
        threshold: numeric(h.threshold), cause: str(h.cause), suggestion: str(h.suggestion) };
    }),
    anomalyScope: (str(d.anomaly_scope) || "none") as Diagnosis["anomalyScope"],
    anomalySpans: arr(d.anomaly_spans).map((v) => {
      const s = obj(v);
      return { apId: str(s.ap_id), type: str(s.type), start: str(s.start),
        end: str(s.end), minutes: numeric(s.minutes) };
    }),
    anomalyType: str(d.anomaly_type), level: str(d.level), score: numeric(d.rule_score ?? d.confidence),
    cause: str(d.cause) || str(d.reason), suggestion: str(d.suggestion), affectedAps: strings(d.affected_aps),
    // 大模型方案解读（含来源）：界面要能显示"这段是模型写的还是模板写的"
    planNarrative: str(d.plan_narrative) || undefined,
    planNarrativeSource: (str(d.plan_narrative_source) || undefined) as Diagnosis["planNarrativeSource"],
    answerFocus: d.answer_focus ? (() => {
      const f = obj(d.answer_focus);
      const kind = str(f.kind), status = str(f.status);
      if (!['overview','priority','coverage','congestion','connections'].includes(kind)
        || !['confirmed','not_confirmed','insufficient','no_fault'].includes(status)) return undefined;
      return { kind: kind as NonNullable<Diagnosis['answerFocus']>['kind'], label: str(f.label),
        status: status as NonNullable<Diagnosis['answerFocus']>['status'], planEligible: f.plan_eligible === true,
        targetAps: strings(f.target_aps), observedAps: strings(f.observed_aps),
        sections: arr(f.sections).map(v => { const s = obj(v); return {title: str(s.title), text: str(s.text)}; })
          .filter(s => s.title && s.text) };
    })() : undefined,
    answerLlm: str(d.answer_llm) || undefined,
    answerCheck: d.answer_check ? (() => {
      const c = obj(d.answer_check);
      return {
        ok: c.ok === true,
        violations: strings(c.violations),
        source: str(c.source) || "rule",
        attempts: c.attempts == null ? undefined : numeric(c.attempts),
      };
    })() : undefined,
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
/** 预测风险 → 处置方案（审计第 6 条）。
 *
 * 与"现存问题方案"的区别由返回值里的 origin/basis/disclaimer 标明：
 * 方案只来自该轮预测的风险，参数基线与效应评估都在**预测基准前的实测窗口**，
 * 不代表未来实测结果。前端必须把这个 disclaimer 显示出来。 */
export interface ForecastPlansPayload {
  plans: TreatmentPlan[];
  risk: { ap: string; type: string; start: string; end: string; title: string; severity?: string } | null;
  basis?: unknown;
  basisWindow?: { start: string; end: string };
  disclaimer?: string;
  message?: string;
}
export async function fetchForecastPlans(
  ctx: { forecastId: string; scenario: string; baseTime: string; history: { start: string; end: string };
    forecast: { start: string; end: string }; alertId?: string | null;
    targetWindow?: { start: string; end: string } },
  signal?: AbortSignal): Promise<ForecastPlansPayload> {
  const data = await request("/forecast/plans", signal, ctx);
  const plans = Array.isArray(data.plans) ? data.plans as TreatmentPlan[] : [];
  return {
    plans,
    risk: (data.risk && typeof data.risk === "object" ? data.risk : null) as ForecastPlansPayload["risk"],
    // 必须把 basis 解出来：归档记录要用它写 forecastId/basisWindow
    // （独立审计 P3：之前声明了字段却从不赋值，归档只剩 risk）。
    basis: data.basis ?? undefined,
    basisWindow: (data.basisWindow && typeof data.basisWindow === "object" ? data.basisWindow : undefined) as ForecastPlansPayload["basisWindow"],
    disclaimer: typeof data.disclaimer === "string" ? data.disclaimer : undefined,
    message: typeof data.message === "string" ? data.message : undefined,
  };
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
/**
 * 会话 ID：后端按这个值保存历史轮次与 AP 槽位（供"它/这个/继续"指代）。
 * 之前固定发 "frontend-p0"，同一后端上任何浏览器/标签页都共用一个会话，
 * 会出现"A 页面问 AP-01，B 页面说'那给它出个方案'却继承了 AP-01"（核查清单第 2 条）。
 *
 * 为什么**不**用 sessionStorage：带 opener 打开的新标签（复制标签页 / Ctrl+点击 /
 * window.open）会**复制** opener 的 sessionStorage，两页拿到同一个 id → 串会话复发
 * （复审 P2-3，MDN 明确 sessionStorage 在 opener 场景是初始拷贝）。
 * 因此改成"每次页面加载生成一次、只存内存"：宁可刷新后丢上下文，也不跨页串话。
 * 用户侧可通过响应里的 multi_turn.session_new 得知"这是新会话"。
 */
const CHAT_SESSION_ID = `s-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
function chatSessionId(): string {
  return CHAT_SESSION_ID;
}

export async function chatNetwork(message: string, scenario: string, window?: QueryScope, signal?: AbortSignal, currentTime?: string): Promise<ChatResult> {
  const data = await request("/agent/chat", signal, {
    message, scenario, session_id: chatSessionId(),
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
  const debug = data.intent_debug ? obj(data.intent_debug) : {};
  const multi = data.multi_turn ? obj(data.multi_turn) : {};
  const rawSource = str(debug.source);
  return {
    reply: requiredString(data.reply, "reply"), intent, scope,
    // 来源必须原样透出：界面据此显示"规则识别/模型未启用"，不再一律写"AI 回答"
    intentSource: rawSource === "llm" || rawSource === "rule" || rawSource === "fallback" ? rawSource : undefined,
    intentDegradeReason: str(debug.degrade_reason) || undefined,
    sessionIsNew: multi.session_new === true ? true : multi.session_new === false ? false : undefined,
    // 多步编排的步骤必须透传（核查清单第 5 条：不能只返回"已执行两步"的文案）
    chain: Array.isArray(data.chain)
      ? data.chain.map(rawStep => {
        const step = obj(rawStep);
        // 不是每个步骤都有 window（预测步骤只有 baseTime/horizonMinutes），
        // 无条件 obj(undefined) 会抛"接口数据结构不正确"——实测把整条复合问法打成红框。
        const win = step.window ? obj(step.window) : {};
        return {
          tool: str(step.tool),
          title: str(step.title) || undefined,
          status: str(step.status) || undefined,
          summary: str(step.summary) || undefined,
          window: step.window ? { start: str(win.start) || undefined, end: str(win.end) || undefined } : undefined,
          horizonMinutes: step.horizonMinutes == null ? undefined : numeric(step.horizonMinutes),
          baseTime: str(step.baseTime) || undefined,
        };
      })
      : undefined,
    worstAp: str(data.worst_ap) || undefined,
    horizonMinutes: data.horizon_minutes == null ? undefined : numeric(data.horizon_minutes),
    plans: parsePlans(data.plans, scope),
    // 复合编排也要解析诊断（复查 P1-8）：原来只在 intent=fault_diagnosis 时解析 detail，
    // 于是"哪个 AP 最差，未来 3 小时会怎样"只显示了步骤摘要，证据与方案全丢。
    diagnosis: data.detail && (intent === "fault_diagnosis" || data.kind === "chain")
      ? parseDiagnosis(data.diagnosis ?? data.detail, scope)
      : null,
    chainForecast: data.forecast ? (() => {
      const fc = obj(data.forecast);
      // 后端的预测载荷是 camelCase（baseTime / alerts），历史字段是 snake_case，两种都读
      const alerts = Array.isArray(fc.alerts) ? fc.alerts : [];
      const first = alerts.length ? obj(alerts[0]) : null;
      return {
        baseTime: str(fc.baseTime ?? fc.base_time) || undefined,
        horizonMinutes: (fc.horizonMinutes ?? fc.horizon_minutes) == null
          ? undefined : numeric(fc.horizonMinutes ?? fc.horizon_minutes),
        alertCount: alerts.length,
        firstTitle: first ? (str(first.title) || str(first.summary) || undefined) : undefined,
        apId: str(fc.apId ?? fc.ap_id) || undefined,
      };
    })() : undefined,
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
        // 实际切分必须透传（复审 P1）：这里是**逐字段白名单重建**，
        // 漏一个键就等于前端永远读不到 —— 面板"本次：168 点 → 144 训练 / 24 测试"依赖它。
        total_points: item.total_points == null ? undefined : numeric(item.total_points),
        train_points: item.train_points == null ? undefined : numeric(item.train_points),
        test_points: item.test_points == null ? undefined : numeric(item.test_points),
        split_points: item.split_points == null ? undefined : numeric(item.split_points),
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

// ---------------------------------------------------------------- 仿真内闭环（申报书创新点④）
export interface SimConfigVersion {
  id: string; scenario: string; apIds: string[];
  // capacity 是枚举（none/one_carrier），其余为数值 —— 用 number | string 承载，禁止统一 Number()
  params: Record<string, number | string>; effectiveFrom: string; appliedAt: string;
  note?: string; planId?: string | null;
}
export interface SimConfigStatus {
  active: SimConfigVersion | null;
  versions: number;
  /** 配置修订号：每次应用/回滚 +1，用于缓存失效与"丢弃过期响应" */
  revision: number;
}

function parseSimStatus(raw: unknown): SimConfigStatus {
  const data = obj(raw);
  const active = data.active ? obj(data.active) : null;
  const params = obj(active?.params);
  const metric = (value: unknown): number | string => {
    // 枚举参数（capacity）必须保留字符串，不能被 numeric() 变成数字
    if (typeof value === "string" && !/^-?\d+(\.\d+)?$/.test(value)) return value;
    return numeric(value);
  };
  return {
    active: active
      ? {
        id: str(active.id), scenario: str(active.scenario),
        apIds: strings(active.apIds),
        params: Object.fromEntries(Object.entries(params).map(([k, v]) => [k, metric(v)])),
        effectiveFrom: str(active.effectiveFrom), appliedAt: str(active.appliedAt),
        note: str(active.note) || undefined, planId: str(active.planId) || undefined,
      }
      : null,
    versions: numeric(data.versions) || 0,
    revision: numeric(data.revision) || 0,
  };
}

export async function fetchSimConfig(signal?: AbortSignal): Promise<SimConfigStatus> {
  return parseSimStatus(await request("/sim/config", signal));
}

/** 版本历史（含已回滚/被替代）。只读展示用，失败时返回空数组，不抛给界面。 */
export async function fetchSimVersions(signal?: AbortSignal): Promise<SimConfigVersion[]> {
  const data = obj(await request("/sim/versions", signal));
  const list = Array.isArray(data.versions) ? data.versions : [];
  return list.map(raw => {
    const v = obj(raw);
    const params = obj(v.params);
    return {
      id: str(v.id), scenario: str(v.scenario), apIds: strings(v.apIds),
      params: Object.fromEntries(Object.entries(params).map(([k, val]) => [k, numeric(val)])),
      effectiveFrom: str(v.effectiveFrom), appliedAt: str(v.appliedAt),
      note: str(v.note) || undefined, planId: str(v.planId) || undefined,
    };
  });
}

export async function applySimConfig(payload: {
  scenario: string; apIds: string[];
  params: Record<string, number | string>; baseline: Record<string, number | string>;
  effectiveFrom: string; note?: string; planId?: string;
}, signal?: AbortSignal): Promise<{ version: SimConfigVersion | null; status: SimConfigStatus; notice: string }> {
  const data = obj(await request("/sim/apply", signal, payload));
  const status = parseSimStatus(data.status);
  return { version: status.active, status, notice: str(data.notice) };
}

export async function rollbackSimConfig(versionId?: string, signal?: AbortSignal): Promise<{ status: SimConfigStatus; notice: string }> {
  const path = versionId ? `/sim/rollback?versionId=${encodeURIComponent(versionId)}` : "/sim/rollback";
  const data = obj(await request(path, signal, {}));
  return { status: parseSimStatus(data.status), notice: str(data.notice) };
}

export interface SimCompareResult {
  apId: string; versionId: string; effectiveFrom: string;
  before: Record<string, number>; after: Record<string, number>; delta: Record<string, number>;
  rows: number; coveredRows: number; notice: string;
}

/** 同一时段"原始历史 vs 生效后"对比。失败抛错由调用方决定是否展示（界面上选择静默隐藏）。 */
export async function fetchSimCompare(params: {
  scenario: string; apId: string; windowStart: string; windowEnd: string;
}, signal?: AbortSignal): Promise<SimCompareResult> {
  const query = new URLSearchParams({
    scenario: params.scenario, ap_id: params.apId,
    window_start: params.windowStart, window_end: params.windowEnd,
  });
  const data = obj(await request(`/sim/compare?${query.toString()}`, signal));
  const means = (raw: unknown) => {
    const m = obj(obj(raw).means);
    return Object.fromEntries(Object.entries(m).map(([k, v]) => [k, numeric(v)]));
  };
  return {
    apId: str(data.apId), versionId: str(data.versionId), effectiveFrom: str(data.effectiveFrom),
    before: means(data.before), after: means(data.after),
    delta: Object.fromEntries(Object.entries(obj(data.delta)).map(([k, v]) => [k, numeric(v)])),
    rows: numeric(data.rows), coveredRows: numeric(data.coveredRows),
    notice: str(data.notice),
  };
}
