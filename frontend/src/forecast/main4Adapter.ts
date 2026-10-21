import { toEpoch } from "../services/backend";
import { formatReplayTime } from "../replay/replayState";
import type { QueryScope } from "../types/backend";
import type { ForecastRequest, ForecastResult } from "./types";
import { FORECAST_LIMITS } from './config';

/** 当前上传后端将时间戳作为一分钟观测帧的起点。main4为沿用的兼容配置名。 */
export function minuteForecastRequest(scope: QueryScope, horizonMinutes: number): ForecastRequest {
  const end = Math.floor(toEpoch(scope.end) / 60_000) * 60_000;
  // 历史窗口限制为12小时；绘图汇总与接口容量限制分别在config中定义。
  const start = Math.min(end, Math.max(Math.ceil(toEpoch(scope.start) / 60_000) * 60_000, end - FORECAST_LIMITS.maxHistoryMinutes * 60_000));
  const baseTime = formatReplayTime(end);
  return { scenario: scope.scenario, baseTime, history: { start: formatReplayTime(start), end: baseTime }, horizonMinutes };
}

type Obj = Record<string, unknown>;
const object = (v: unknown): v is Obj => Boolean(v && typeof v === "object" && !Array.isArray(v));
const rounded = (n: number) => Math.round(n * 100) / 100;
/** Only the explicitly configured main(4) response is normalized. Other validation stays strict. */
export function normalizeMain4(raw: unknown): unknown {
  if (!object(raw) || !Array.isArray(raw.impacts)) return raw;
  return { ...raw, impacts: raw.impacts.map(impact => {
    if (!object(impact) || impact.targetLabel !== "全网" || !["imp-sinr", "imp-thr"].includes(String(impact.id)) || !Array.isArray(impact.predicted)) return impact;
    return { ...impact, predicted: impact.predicted.map(point => {
      if (!object(point) || typeof point.value !== "number" || typeof point.lower !== "number" || typeof point.upper !== "number") return point;
      // The supplied backend adds fixed ±10%; this is not a fitted prediction interval.
      if (Math.abs(point.lower - rounded(point.value * .9)) > .000001 || Math.abs(point.upper - rounded(point.value * 1.1)) > .000001) return point;
      return { time: point.time, value: point.value };
    }) };
  }) };
}

export function completeMain4(result: ForecastResult): ForecastResult {
  // 免责说明必须始终出现：原先只要后端返回 method 就把说明换掉，
  // 而 normalizeMain4 仍在剥离固定 ±10% 的假区间，于是出现
  // "数据处理过、但用户看不到任何说明"的披露缺口。
  // 后端已改为"阻尼趋势外推 + 残差估计区间"，因此这里按实际情况描述方法：
  // 有区间就说明区间来源，没有就如实说明没有。
  const hasInterval = result.impacts.some(i => i.predicted.some(p => p.lower != null && p.upper != null));
  const methodText = result.method
    ? `预测方法：${result.method}。`
    : "当前为简化外推：按历史末段线性趋势做阻尼外推（趋势不显著时保持末值）。";
  const limitations = result.limitations?.length ? `限制：${result.limitations.join('；')}。` : "";
  const disclaimer = (hasInterval
    ? "预测区间由历史拟合残差估计，是经验范围而非统计置信区间；"
    : "本次未返回预测区间；")
    + "预警为趋势与阈值判断的结果，尚未验证提前预警能力。";
  const notice = `${methodText}${limitations}${disclaimer}`;
  if (result.status !== "ready") return { ...result, integrationNotice: notice };
  const observed = result.spectra.some(s => s.history.some(f => f.values.some(v => v !== null)));
  if (!observed) return { ...result, status: "insufficient_data", message: "当前参考窗口没有有效频谱观测，请调整时刻或检查频谱文件。", alerts: [], spectra: [], impacts: [], spectrumViews: undefined, integrationNotice: notice };
  const missingImpact = result.impacts.some(item => !item.history.some(point => point.value !== null));
  return { ...result, integrationNotice: notice + (missingImpact ? ' 部分网络指标缺少有效历史，已隐藏这些指标的预测，不把缺失当作0。' : ''),
    impacts: result.impacts.map(item => item.history.some(point => point.value !== null) ? item : { ...item, predicted: [] }),
    message: result.alerts.length ? result.message : "当前规则未触发持续性风险提示，不表示未来一定没有故障。",
    alerts: result.alerts.map(alert => {
    if (alert.spectrumViews) return alert;
    const primary = result.spectra.find(s => s.id === alert.spectrumId);
    if (!primary?.observationId || primary.observationId !== alert.target) return alert;
    const same = result.spectra.filter(s => s.observationId === primary.observationId);
    const power = same.filter(s => s.metric === "power"), occupancy = same.filter(s => s.metric === "occupancy");
    if (power.length > 1 || occupancy.length > 1) return alert;
    return { ...alert, spectrumViews: { powerId: power[0]?.id ?? null, occupancyId: occupancy[0]?.id ?? null,
      recommendedView: primary.metric === "occupancy" ? "occupancy" : "power-time",
      reason: "依据该预警已关联的观测点，展示同一AP的功率与占用率；可切换查看。" } };
  }) };
}
