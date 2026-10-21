import type { ForecastAlert, ForecastResult, SpectrumBand, SpectrumForecast, SpectrumFrame, SpectrumView } from "./types";

export const SPECTRUM_VIEWS: { id: SpectrumView; label: string; explanation: string }[] = [
  { id: "curve", label: "频谱曲线", explanation: "查看一个采样时段内，各频段的接收功率分布。" },
  { id: "power-time", label: "功率时频", explanation: "查看各频段接收功率随时间的变化；颜色单位为dBm。" },
  { id: "occupancy", label: "占用态势", explanation: "查看各频段的繁忙程度随时间的变化；颜色为占用率%。" },
];
const hasValues = (s: SpectrumForecast | undefined) => Boolean(s && [...s.history, ...s.predicted].some(f => f.values.some(v => v !== null)));
export function resolveSpectrumViews(result: ForecastResult, alert?: ForecastAlert) {
  const group = alert ? alert.spectrumViews : result.spectrumViews;
  const legacy = alert ? result.spectra.find(s => s.id === alert.spectrumId) : result.spectra[0];
  const power = group ? result.spectra.find(s => s.id === group.powerId && s.metric === "power") : legacy?.metric === "power" ? legacy : undefined;
  const occupancy = group ? result.spectra.find(s => s.id === group.occupancyId && s.metric === "occupancy") : legacy?.metric === "occupancy" ? legacy : undefined;
  const views = SPECTRUM_VIEWS.map(v => {
    const data = v.id === "occupancy" ? occupancy : power;
    return { ...v, available: hasValues(data), unavailableReason: data ? "暂无有效数值" : v.id === "occupancy" ? "暂无占用率数据" : "暂无功率数据" };
  });
  const requested = group?.recommendedView ?? (occupancy ? "occupancy" : "power-time");
  const recommendedAvailable = views.some(v => v.id === requested && v.available);
  const defaultView = recommendedAvailable ? requested : (["occupancy", "power-time", "curve"] as SpectrumView[]).find(id => views.some(v => v.id === id && v.available)) ?? "occupancy";
  const reason = recommendedAvailable ? group?.reason || (defaultView === "occupancy" ? "优先查看相关频段的繁忙程度。" : "优先查看相关频段的功率变化。")
    : views.some(v => v.available) ? "推荐视图暂无可用数据，已展示其他可用视图。" : "该风险暂未提供关联频谱或有效数值。";
  return { power, occupancy, views, defaultView, reason };
}
export type TaggedSpectrumFrame = SpectrumFrame & { kind: "history" | "predicted" };
export function spectrumFrames(spectrum: SpectrumForecast): TaggedSpectrumFrame[] {
  return [...spectrum.history.map(f => ({ ...f, kind: "history" as const })), ...spectrum.predicted.map(f => ({ ...f, kind: "predicted" as const }))];
}
export function defaultSpectrumFrame(spectrum: SpectrumForecast, alert?: ForecastAlert, focusTime?: string): number {
  const frames = spectrumFrames(spectrum);
  if (focusTime) {
    const match = frames.findIndex(f => f.kind === 'predicted' && f.start <= focusTime && f.end > focusTime);
    if (match >= 0) return match;
    if (frames.at(-1)?.end === focusTime) return frames.length - 1;
  }
  if (alert) {
    const match = frames.findIndex(f => f.kind === "predicted" && f.start <= alert.start && f.end > alert.start);
    if (match >= 0) return match;
    const after = frames.findIndex(f => f.kind === "predicted" && f.start >= alert.start);
    if (after >= 0) return after;
  }
  const predicted = frames.findIndex(f => f.kind === "predicted");
  return predicted >= 0 ? predicted : Math.max(0, frames.length - 1);
}

/** 合并相邻受影响频段；不覆盖频率空隙，也不逐频段堆叠边框。 */
export function spectrumHighlightRanges(bands: SpectrumBand[], ids: string[]) {
  const selected = new Set(ids);
  const ranges: { lowerMHz: number; upperMHz: number }[] = [];
  for (const band of bands.filter(b => selected.has(b.id)).sort((a, b) => a.lowerMHz - b.lowerMHz)) {
    const last = ranges.at(-1);
    if (last && band.lowerMHz <= last.upperMHz) last.upperMHz = Math.max(last.upperMHz, band.upperMHz);
    else ranges.push({ lowerMHz: band.lowerMHz, upperMHz: band.upperMHz });
  }
  return ranges;
}

/** 只在绘制层汇总，保留原始帧供曲线逐时段选择；缺口不会被连接成连续观测。 */
export function summarizeSpectrumFrames<T extends SpectrumFrame>(frames: T[], limit = 240): T[] {
  if (frames.length <= limit) return frames;
  const size = Math.ceil(frames.length / Math.max(1, limit));
  const result: T[] = [];
  const millis = (value: string) => Date.parse(value.includes(' ') ? value.replace(' ', 'T') + '+08:00' : value);
  let group: T[] = [];
  function flush() {
    if (!group.length) return;
    const first = group[0], last = group.at(-1)!;
    const values = first.values.map((_value, i) => {
      let total = 0, duration = 0;
      for (const frame of group) {
        const value = frame.values[i], weight = millis(frame.end) - millis(frame.start);
        if (value !== null && Number.isFinite(value) && weight > 0) { total += value * weight; duration += weight; }
      }
      return duration ? total / duration : null;
    });
    result.push({ ...first, end: last.end, values, sampleCount: group.reduce((sum, f) => sum + (f.sampleCount ?? 1), 0) });
    group = [];
  }
  for (const frame of frames) {
    if (group.length && (group.length >= size || group.at(-1)!.end !== frame.start)) flush();
    group.push(frame);
  }
  flush();
  return result;
}
/** 两类数据可采用不同频率网格，高亮按频率交集映射，不按下标猜测。 */
export function alertForSpectrum(result: ForecastResult, alert: ForecastAlert | undefined, target: SpectrumForecast): ForecastAlert | undefined {
  if (!alert) return undefined;
  const source = result.spectra.find(s => s.id === alert.spectrumId)
    ?? result.spectra.find(s => s.id === (alert.spectrumViews?.occupancyId ?? alert.spectrumViews?.powerId));
  const bands = source?.bands.filter(b => alert.bandIds.includes(b.id)) ?? [];
  return { ...alert, bandIds: target.bands.filter(b => bands.some(s => Math.max(b.lowerMHz, s.lowerMHz) < Math.min(b.upperMHz, s.upperMHz))).map(b => b.id) };
}

/** 时轴优先保留预测基准和终点，长预测配短历史时避免左侧多个刻度重叠。 */
export function spectrumTimeTicks(begin: number, base: number, end: number, width: number): number[] {
  const crossesDay = new Date(begin + 8 * 3600_000).toISOString().slice(0, 10) !== new Date(end + 8 * 3600_000).toISOString().slice(0, 10);
  const gap = crossesDay ? 88 : 52;
  const selected: number[] = [];
  for (const stamp of [base, end, begin, (base + end) / 2, (begin + base) / 2]) {
    if (!selected.some(other => Math.abs(other - stamp) / (end - begin) * width < gap)) selected.push(stamp);
  }
  return selected.sort((a, b) => a - b);
}
