import { toEpoch } from "../services/backend";
import { formatReplayTime } from "../replay/replayState";
import type { ForecastRequest, ForecastResult, SpectrumFrame, ForecastPoint } from "./types";

/** 纯界面示例：不接收、更不读取七天CSV或未到达的记录。 */
export function createForecastDemo(request: ForecastRequest): ForecastResult {
  const base = toEpoch(request.baseTime), begin = toEpoch(request.history.start);
  const end = base + request.horizonMinutes * 60_000, step = 5 * 60_000;
  const historyStep = Math.max(step, Math.ceil((base - begin) / 120 / 60_000) * 60_000);
  const bands = Array.from({ length: 24 }, (_, i) => ({ id: `band-${i}`, label: `频段 ${i + 1}`, lowerMHz: 5180 + i * 5, upperMHz: 5185 + i * 5 }));
  const frames = (start: number, stop: number, predicted: boolean): SpectrumFrame[] => {
    const rows: SpectrumFrame[] = [];
    const frameStep = predicted ? step : historyStep;
    for (let t = start; t < stop; t += frameStep) rows.push({
      start: formatReplayTime(t), end: formatReplayTime(Math.min(stop, t + frameStep)),
      values: bands.map((_, band) => Math.round(Math.min(98, 20 + band * 1.2
        + 14 * Math.sin((t - begin) / step + band / 3) ** 2 + (predicted && band >= 12 && band <= 15 ? 35 + (t - base) / 60_000 : 0)))),
    });
    return rows;
  };
  const points = (start: number, stop: number, predicted: boolean, offset = 0): ForecastPoint[] => {
    const rows: ForecastPoint[] = [];
    for (let t = start; t <= stop; t += predicted ? step : historyStep) {
      const value = predicted ? 20 - (t - base) / 60_000 * 0.5 + offset : 20 + Math.sin((t - begin) / step) + offset;
      rows.push({ time: formatReplayTime(t), value: Math.round(value * 10) / 10,
        ...(predicted ? { lower: value - 2, upper: value + 2 } : {}) });
    }
    return rows;
  };
  // 两类指标均为独立的布局示例；正式数据不能由占用率换算成功率。
  const powerFrames = (rows: SpectrumFrame[], peak: number, predicted: boolean) => rows.map((f, frameIndex) => ({ start: f.start, end: f.end,
    values: bands.map((_, band) => Math.round((-96 + 5 * Math.sin(band / 3 + frameIndex / 4)
      + 10 * Math.exp(-(((band - 4) / 2) ** 2)) + (predicted ? 35 * Math.exp(-(((band - peak) / 1.8) ** 2)) : 0)) * 10) / 10) }));
  const historicalFrames = frames(begin, base, false), predictedFrames = frames(base, end, true);
  const forecastStart = formatReplayTime(base + Math.min(15, request.horizonMinutes / 2) * 60_000);
  return {
    id: `demo-${request.baseTime}`, scenario: request.scenario, baseTime: request.baseTime,
    history: { ...request.history }, forecast: { start: request.baseTime, end: formatReplayTime(end) },
    generatedAt: formatReplayTime(Date.now()), source: "demo", status: "ready", message: "仅用于界面预览，不代表当前网络预测。",
    alerts: [
      { id: "demo-busy", target: "AP-02", title: "信道占用升高风险", summary: "示例：相关频段预计持续繁忙，接收质量可能下降；详细原因仍需结合其他证据研判。",
        severity: "high", start: forecastStart, end: formatReplayTime(end), spectrumId: "demo-spectrum", bandIds: ["band-12", "band-13", "band-14", "band-15"], impactId: "demo-sinr",
        spectrumViews: { powerId: "demo-power-ap02", occupancyId: "demo-spectrum", recommendedView: "occupancy", reason: "这条示例预警关注持续繁忙，优先查看占用态势；也可切换功率变化与频谱细节。" } },
      { id: "demo-quality", target: "UE-03", title: "接收质量下降风险", summary: "示例：该用户的 SINR 可能下降，需关注后续连接质量。",
        severity: "medium", start: forecastStart, end: formatReplayTime(end), spectrumId: null, bandIds: [], impactId: "demo-ue-sinr" },
      { id: "demo-power-rise", target: "AP-01", title: "局部频段功率升高", summary: "示例：某一频段预计出现功率峰值，建议查看频谱细节；功率升高不能单独证明发生干扰。",
        severity: "medium", start: forecastStart, end: formatReplayTime(end), spectrumId: "demo-power-ap01", bandIds: ["band-7"], impactId: null,
        spectrumViews: { powerId: "demo-power-ap01", occupancyId: null, recommendedView: "curve", reason: "这条示例预警关注局部功率峰值，优先展示风险时段的频谱曲线。" } },
    ],
    spectrumViews: { powerId: "demo-power-ap02", occupancyId: "demo-spectrum", recommendedView: "occupancy" },
    spectra: [
      { id: "demo-spectrum", observationId: "AP-02", observationLabel: "AP-02观测点", metric: "occupancy", unit: "%", scale: { min: 0, max: 100 },
        bands, history: historicalFrames, predicted: predictedFrames },
      { id: "demo-power-ap02", observationId: "AP-02", observationLabel: "AP-02观测点", metric: "power", unit: "dBm", scale: { min: -105, max: -45 },
        bands, history: powerFrames(historicalFrames, 13, false), predicted: powerFrames(predictedFrames, 13, true) },
      { id: "demo-power-ap01", observationId: "AP-01", observationLabel: "AP-01观测点", metric: "power", unit: "dBm", scale: { min: -105, max: -45 },
        bands, history: powerFrames(historicalFrames, 7, false), predicted: powerFrames(predictedFrames, 7, true) },
    ],
    impacts: [
      { id: "demo-sinr", targetLabel: "AP-02服务用户", label: "平均SINR", unit: "dB", history: points(begin, base, false), predicted: points(base + step, end, true), threshold: { value: 10, direction: "below" } },
      { id: "demo-ue-sinr", targetLabel: "UE-03", label: "SINR", unit: "dB", history: points(begin, base, false, -3), predicted: points(base + step, end, true, -3), threshold: { value: 8, direction: "below" } },
    ],
  };
}
