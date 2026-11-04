import { toEpoch } from "../services/backend";
import { API_BASE } from "../services/apiBase";
import { FORECAST_CONFIG, FORECAST_LIMITS } from "./config";
import { createForecastDemo } from "./demo";
import { completeMain4, normalizeMain4 } from "./main4Adapter";
import type { ForecastAnalysis, ForecastConfig, ForecastRequest, ForecastResult, SpectrumForecast, ImpactForecast, SpectrumViews, SpectrumView } from "./types";

type Obj = Record<string, unknown>;
const object = (v: unknown): Obj => { if (!v || typeof v !== "object" || Array.isArray(v)) throw new Error("预测接口对象格式无效"); return v as Obj; };
const text = (v: unknown): string => { if (typeof v !== "string" || !v.trim()) throw new Error("预测接口缺少必要文字字段"); return v; };
const number = (v: unknown): number => { if (typeof v !== "number" || !Number.isFinite(v)) throw new Error("预测数值无效"); return v; };
const array = (v: unknown, limit = 2000): unknown[] => { if (!Array.isArray(v) || v.length > limit) throw new Error("预测接口数组缺失或过大"); return v; };
const nullableNumber = (v: unknown): number | null => v === null ? null : number(v);
const time = (v: unknown): string => { const s = text(v); toEpoch(s); return s; };
const same = (a: unknown, b: string) => { if (a !== b) throw new Error("预测返回与请求的时间或场景不一致"); };
const unique = (items: { id: string }[]) => { if (new Set(items.map(x => x.id)).size !== items.length) throw new Error("预测图表ID重复"); };

function range(value: unknown) { const v = object(value), start = time(v.start), end = time(v.end); if (start > end) throw new Error("预测时间范围倒序"); return { start, end }; }
function points(value: unknown, begin: string, end: string, predicted: boolean) {
  let previous = "";
  return array(value).map(raw => {
    const p = object(raw), stamp = time(p.time), val = nullableNumber(p.value);
    if (stamp < begin || stamp > end || stamp <= previous) throw new Error("指标时间越界、重复或未排序"); previous = stamp;
    const lower = p.lower === undefined ? undefined : nullableNumber(p.lower), upper = p.upper === undefined ? undefined : nullableNumber(p.upper);
    if (!predicted && (lower !== undefined || upper !== undefined)) throw new Error("历史数据不应包含预测区间");
    if ((lower == null) !== (upper == null) || (lower != null && upper != null && (lower > upper || val === null || val < lower || val > upper))) throw new Error("预测区间无效");
    return { time: stamp, value: val, ...(lower === undefined ? {} : { lower }), ...(upper === undefined ? {} : { upper }) };
  });
}
export function parseForecast(raw: unknown, request: ForecastRequest): ForecastResult {
  const v = object(raw); same(v.scenario, request.scenario); same(v.baseTime, request.baseTime);
  const history = range(v.history), forecast = range(v.forecast);
  same(history.start, request.history.start); same(history.end, request.history.end); same(forecast.start, request.baseTime);
  // 无数据时后端可能给"预测窗口内暂无可用数据"这类说明，且不保证 forecast.end 恰好等于
  // base + horizon（复查 P3-7：页面因此报"预测结束时间与请求不一致"，把"没数据"说成"契约错误"）。
  // 先看 status，再对 ready 的响应做严格时间校验。
  const status = text(v.status);
  if (status === "insufficient_data" || status === "unavailable") {
    return {
      scenario: request.scenario,
      baseTime: text(v.baseTime) || request.baseTime,
      history: { start: history.start, end: history.end },
      forecast: { start: forecast.start, end: forecast.end },
      source: v.source === "measurement" ? "measurement" : "simulation",
      status: status as ForecastResult["status"],
      message: text(v.message) || "该场景暂无可用数据，无法给出预测",
      alerts: [], spectra: [], impacts: [],
      generatedAt: text(v.generatedAt) || "",
      id: text(v.id) || "",
    } as ForecastResult;
  }
  if (toEpoch(forecast.end) !== toEpoch(request.baseTime) + request.horizonMinutes * 60_000) throw new Error("预测结束时间与请求不一致");
  if (v.source !== "simulation" && v.source !== "measurement") throw new Error("真实接口来源不能标为示例demo");
  if (!["ready", "insufficient_data", "unavailable"].includes(text(v.status))) throw new Error("预测状态无效");
  const spectra: SpectrumForecast[] = array(v.spectra, 32).map(rawSpectrum => {
    const s = object(rawSpectrum), scale = object(s.scale), min = number(scale.min), max = number(scale.max);
    if (!(min < max) || !((s.metric === "occupancy" && s.unit === "%" && min === 0 && max === 100) || (s.metric === "power" && s.unit === "dBm"))) throw new Error("频谱指标、单位或色标不一致");
    let last = -Infinity;
    const bands = array(s.bands, 128).map(rawBand => {
      const b = object(rawBand), lowerMHz = number(b.lowerMHz), upperMHz = number(b.upperMHz);
      if (lowerMHz <= 0 || lowerMHz < last || lowerMHz >= upperMHz) throw new Error("频段边界无效或重叠"); last = upperMHz;
      return { id: text(b.id), label: text(b.label), lowerMHz, upperMHz };
    });
    if (!bands.length) throw new Error("频谱缺少频段"); unique(bands);
    const frames = (rows: unknown, begin: string, end: string) => {
      let previous = begin;
      const list = array(rows, FORECAST_LIMITS.maxFrames).map(rawFrame => {
        const f = object(rawFrame), frame = range(f);
        if (frame.start < begin || frame.end > end || frame.start < previous || frame.start === frame.end) throw new Error("频谱帧时间越界或重叠"); previous = frame.end;
        const values = array(f.values, 128).map(nullableNumber);
        if (values.length !== bands.length) throw new Error("频谱帧数值个数与频段不一致");
        if (values.some(n => n !== null && s.metric === "occupancy" && (n < 0 || n > 100))) throw new Error("后端返回的占用率超出0%—100%，请检查预测输出");
        return { ...frame, values };
      });
      if (list.length * bands.length > FORECAST_LIMITS.maxValuesPerSpectrumPeriod) throw new Error("频谱网格过大，请后端汇总");
      return list;
    };
    return { id: text(s.id), observationLabel: text(s.observationLabel), ...(s.observationId === undefined ? {} : { observationId: text(s.observationId) }), metric: s.metric as SpectrumForecast["metric"], unit: s.unit as SpectrumForecast["unit"],
      scale: { min, max }, bands, history: frames(s.history, history.start, history.end), predicted: frames(s.predicted, forecast.start, forecast.end) };
  });
  const impacts: ImpactForecast[] = array(v.impacts, 64).map(rawImpact => {
    const p = object(rawImpact), threshold = p.threshold === undefined ? undefined : object(p.threshold);
    if (threshold && threshold.direction !== "above" && threshold.direction !== "below") throw new Error("阈值判断方向无效");
    return { id: text(p.id), targetLabel: text(p.targetLabel), label: text(p.label), unit: text(p.unit),
      history: points(p.history, history.start, history.end, false), predicted: points(p.predicted, forecast.start, forecast.end, true),
      ...(threshold ? { threshold: { value: number(threshold.value), direction: threshold.direction as "above" | "below" } } : {}) };
  });
  unique(spectra); unique(impacts);
  function parseViews(rawViews: unknown): SpectrumViews | undefined {
    if (rawViews == null) return undefined;
    const g = object(rawViews), powerId = g.powerId === null ? null : text(g.powerId), occupancyId = g.occupancyId === null ? null : text(g.occupancyId);
    const power = powerId ? spectra.find(s => s.id === powerId) : undefined, occupancy = occupancyId ? spectra.find(s => s.id === occupancyId) : undefined;
    if ((powerId && power?.metric !== "power") || (occupancyId && occupancy?.metric !== "occupancy")) throw new Error("频谱视图关联的指标类型不一致或数据不存在");
    const referenced = [power, occupancy].filter((s): s is SpectrumForecast => Boolean(s));
    if (referenced.some(s => !s.observationId) || new Set(referenced.map(s => s.observationId)).size > 1) throw new Error("同一视图组必须来自同一观测点observationId");
    if (g.recommendedView !== undefined && !["curve", "power-time", "occupancy"].includes(text(g.recommendedView))) throw new Error("推荐频谱视图无效");
    return { powerId, occupancyId, ...(g.recommendedView === undefined ? {} : { recommendedView: g.recommendedView as SpectrumView }), ...(g.reason === undefined ? {} : { reason: text(g.reason) }) };
  }
  const overviewViews = parseViews(v.spectrumViews);
  const alerts = array(v.alerts, 200).map(rawAlert => {
    const a = object(rawAlert), span = range(a);
    if (span.start < forecast.start || span.end > forecast.end || !["high", "medium", "low"].includes(text(a.severity))) throw new Error("预警时段或等级无效");
    const spectrumId = a.spectrumId === null ? null : text(a.spectrumId), impactId = a.impactId === null ? null : text(a.impactId);
    const spectrumViews = parseViews(a.spectrumViews);
    if (spectrumViews && spectrumId && spectrumId !== spectrumViews.powerId && spectrumId !== spectrumViews.occupancyId) throw new Error("预警主频谱必须属于本风险视图组");
    const bandIds = array(a.bandIds, 128).map(text), spectrum = spectra.find(s => s.id === spectrumId)
      ?? spectra.find(s => s.id === (spectrumViews?.occupancyId ?? spectrumViews?.powerId));
    if ((spectrumId && !spectra.some(s => s.id === spectrumId)) || (impactId && !impacts.some(p => p.id === impactId)) || bandIds.some(id => !spectrum?.bands.some(b => b.id === id))) throw new Error("预警关联图表或频段不存在");
    return { id: text(a.id), target: text(a.target), title: text(a.title), summary: text(a.summary), severity: a.severity as "high" | "medium" | "low", ...span, spectrumId, impactId, bandIds, ...(spectrumViews ? { spectrumViews } : {}) };
  });
  if (v.status !== "ready" && (alerts.length || spectra.length || impacts.length)) throw new Error("不可用状态不应附带预测结论");
  return { id: text(v.id), scenario: request.scenario, baseTime: request.baseTime, history, forecast, generatedAt: time(v.generatedAt),
    source: v.source, status: v.status as ForecastResult["status"], message: typeof v.message === "string" ? v.message : "",
    alerts: [...new Map(alerts.map(a => [a.id, a])).values()], spectra, impacts, ...(overviewViews ? { spectrumViews: overviewViews } : {}),
    ...(v.method === undefined ? {} : { method: text(v.method) }),
    ...(v.limitations === undefined ? {} : { limitations: array(v.limitations, 30).map(text) }) };
}

async function post(path: string, body: unknown, signal: AbortSignal | undefined, config: ForecastConfig, fetcher: typeof fetch) {
  if (signal?.aborted) throw new DOMException("请求已取消", "AbortError");
  const controller = new AbortController(), abort = () => controller.abort();
  signal?.addEventListener("abort", abort, { once: true }); const timer = setTimeout(abort, config.timeoutMs);
  try {
    const response = await fetcher(`${API_BASE}${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: controller.signal });
    if (!response.ok) throw new Error(response.status === 404 ? "预测接口尚未接入（HTTP 404）" : `预测请求失败（HTTP ${response.status}）`);
    let json: unknown; try { json = await response.json(); } catch (cause) { throw new Error("预测接口未返回JSON，请检查接口路径及代理", { cause }); }
    const envelope = object(json); if (envelope.code !== 0) throw new Error(typeof envelope.message === "string" ? envelope.message : "预测接口业务错误");
    return envelope.data;
  } catch (error) {
    if (controller.signal.aborted) {
      if (signal?.aborted) throw new DOMException("请求已取消", "AbortError");
      throw new Error("预测请求超时，请稍后重试", { cause: error });
    }
    throw error instanceof Error ? error : new Error("无法连接预测服务");
  } finally { clearTimeout(timer); signal?.removeEventListener("abort", abort); }
}
export async function loadForecast(request: ForecastRequest, signal?: AbortSignal, config = FORECAST_CONFIG, fetcher: typeof fetch = fetch): Promise<ForecastResult> {
  if (signal?.aborted) throw new DOMException("请求已取消", "AbortError");
  if (request.history.end !== request.baseTime || toEpoch(request.history.start) > toEpoch(request.baseTime)
    || !request.scenario.trim() || !Number.isInteger(request.horizonMinutes) || request.horizonMinutes <= 0 || request.horizonMinutes > FORECAST_LIMITS.maxHorizonMinutes) throw new Error("预测请求范围无效");
  if (request.targetWindow) {
    const start = toEpoch(request.targetWindow.start), end = toEpoch(request.targetWindow.end), base = toEpoch(request.baseTime);
    if (start < base || end < start || end > base + request.horizonMinutes * 60_000) throw new Error("关注时段必须位于本轮预测范围内");
  }
  if (config.mode === "demo") return createForecastDemo(request);
  const raw = await post(config.previewPath, request, signal, config, fetcher);
  const result = parseForecast(config.responseProfile === "main4" ? normalizeMain4(raw) : raw, request);
  return config.responseProfile === "main4" ? completeMain4(result) : result;
}
export async function analyzeForecast(result: ForecastResult, alertId: string | null, signal?: AbortSignal, config = FORECAST_CONFIG, fetcher: typeof fetch = fetch,
  context?: Pick<ForecastRequest, 'question' | 'targetWindow'>): Promise<ForecastAnalysis> {
  if (signal?.aborted) throw new DOMException("请求已取消", "AbortError");
  if (result.status !== "ready" || (alertId && !result.alerts.some(a => a.id === alertId))) throw new Error("当前预测不能发起研判");
  if (result.source === "demo") return { forecastId: result.id, baseTime: result.baseTime, alertId, source: "demo",
    reply: "这是界面示例，未调用大模型。\n\n正式接入后，助手将在这里解释所选预警的可能原因、支持与反对证据、影响范围，以及预防方案和预计效果。占用率升高本身不能直接证明干扰，需要结合SINR、业务需求和连接状态综合判断。" };
  const raw = object(await post(config.analysisPath, { forecastId: result.id, scenario: result.scenario, baseTime: result.baseTime,
    alertId, history: result.history, forecast: result.forecast,
    ...(context?.question ? { question: context.question } : {}), ...(context?.targetWindow ? { targetWindow: context.targetWindow } : {}),
  }, signal, config, fetcher));
  if (raw.forecastId !== result.id || raw.baseTime !== result.baseTime || raw.alertId !== alertId) throw new Error("预测研判返回与本次预测不一致");
  if (raw.source !== "llm" && raw.source !== "rule") throw new Error("研判结果来源无效");
  return { forecastId: result.id, baseTime: result.baseTime, alertId, source: raw.source, reply: text(raw.reply) };
}
