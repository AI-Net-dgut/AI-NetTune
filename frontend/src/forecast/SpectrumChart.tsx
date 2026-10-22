import { memo, useId, useMemo } from "react";
import { toEpoch } from "../services/backend";
import { spectrumCells, spectrumColor } from "./charts";
import { spectrumHighlightRanges, spectrumTimeTicks } from './spectrumModel';
import { FORECAST_LIMITS } from './config';
import type { ForecastResult, SpectrumForecast, ForecastAlert } from "./types";

function SpectrumChart({ result, spectrum, alert }: { result: ForecastResult; spectrum: SpectrumForecast; alert?: ForecastAlert }) {
  const gradient = useId(), cells = useMemo(() => spectrumCells(result, spectrum), [result, spectrum]);
  const begin = toEpoch(result.history.start), end = toEpoch(result.forecast.end), base = toEpoch(result.baseTime);
  const minFreq = spectrum.bands[0].lowerMHz, maxFreq = spectrum.bands.at(-1)!.upperMHz;
  const left = 57, right = 700, top = 29, bottom = 183;
  const x = (t: string | number) => left + ((typeof t === "number" ? t : toEpoch(t)) - begin) / (end - begin) * (right - left);
  const y = (f: number) => bottom - (f - minFreq) / (maxFreq - minFreq) * (bottom - top);
  const crossesDay = result.history.start.slice(0, 10) !== result.forecast.end.slice(0, 10);
  const stamp = (n: number) => {
    const value = new Date(n + 8 * 3600_000).toISOString();
    return crossesDay ? value.slice(5, 16).replace('T', ' ') : value.slice(11, 16);
  };
  const ticks = spectrumTimeTicks(begin, base, end, right - left);
  const highlights = spectrumHighlightRanges(spectrum.bands, alert?.bandIds ?? []);
  // 原先这里有个 denseHighlights = highlights.length > 8 的分支，用于"受影响频段太分散时
  // 只画一个风险时段外框"。但后端告警的 bandIds 恒定取 hardest[:5]（最多 5 个频段），
  // spectrumHighlightRanges 合并后最多也只有 5 段，> 8 永远不成立 —— 是个永远不会执行的
  // 死分支（审查报告 F-4）。已删除，保留逐频段标注；若将来 bandIds 上限放宽，
  // 逐段标注仍能正常工作，只是方框会更多。
  return <figure className="ain-forecast-spectrum">
    <figcaption><div><h3>{spectrum.metric === "occupancy" ? "频谱占用态势预测" : "功率时频态势预测"}</h3><p>{spectrum.observationLabel} · {spectrum.metric === "occupancy" ? "信道占用" : "频段功率"}</p></div>
      <span>颜色：{spectrum.metric === "occupancy" ? "占用率" : "功率"}（{spectrum.unit}）{result.source === "demo" ? " · 示例数据" : ""}</span></figcaption>
    <svg viewBox="0 0 724 240" role="img" aria-label={`频谱态势：${result.history.start}至${result.baseTime}为历史观测，之后至${result.forecast.end}为预测；颜色表示${spectrum.metric === "occupancy" ? "占用率" : "功率"}。灰色为缺失值。`}>
      <defs><linearGradient id={gradient}>{Array.from({ length: 6 }, (_, i) => <stop key={i} offset={`${i * 20}%`} stopColor={spectrumColor(spectrum.scale.min + (spectrum.scale.max - spectrum.scale.min) * i / 5, spectrum.scale.min, spectrum.scale.max)} />)}</linearGradient></defs>
      <rect x={left} y={top} width={right - left} height={bottom - top} fill="#f1f5fa" />
      {cells.map((cell, i) => <rect key={i} x={x(cell.start)} y={y(cell.upperMHz)} width={Math.max(0, x(cell.end) - x(cell.start))} height={Math.max(0, y(cell.lowerMHz) - y(cell.upperMHz))} fill={cell.fill}><title>{cell.label}</title></rect>)}
      <rect x={x(base)} y={top} width={right - x(base)} height={bottom - top} fill="none" stroke="#d2dceb" />
      <line x1={x(base)} x2={x(base)} y1={top - 4} y2={bottom} stroke="#fff" strokeWidth="2" strokeDasharray="4 3" />
      <text x={(left + x(base)) / 2} y="18" textAnchor="middle">历史观测</text><text x={(right + x(base)) / 2} y="18" textAnchor="middle" fill="#c17b25">未来预测</text>
      {ticks.map(t => <g key={t}><line x1={x(t)} x2={x(t)} y1={bottom} y2={bottom + 4} stroke="#abc1d9" /><text x={x(t)} y={bottom + 17} textAnchor={t === end ? "end" : t === begin ? "start" : "middle"}>{stamp(t)}</text></g>)}
      {Array.from({ length: 4 }, (_, i) => { const f = minFreq + (maxFreq - minFreq) * i / 3; return <text key={i} x={left - 7} y={y(f) + 3} textAnchor="end">{(f / 1000).toFixed(2)}</text>; })}
      <text x="3" y="18">频率/GHz</text>
      {alert && highlights.map(b => <rect key={`${b.lowerMHz}-${b.upperMHz}`} x={x(alert.start)} y={y(b.upperMHz)} width={x(alert.end) - x(alert.start)} height={y(b.lowerMHz) - y(b.upperMHz)} rx="2" fill="none" stroke="#bc8b35" strokeWidth="1.5" strokeDasharray="5 3" />)}
      <rect x="242" y="217" width="240" height="7" rx="3" fill={`url(#${gradient})`} />
      {[0, 0.5, 1].map(n => <text key={n} x={242 + n * 240} y="237" textAnchor="middle">{Math.round((spectrum.scale.min + n * (spectrum.scale.max - spectrum.scale.min)) * 10) / 10}</text>)}
      <rect x="535" y="217" width="8" height="8" fill="#e8edf4" /><text x="548" y="225">缺失</text>
    </svg>
    {(spectrum.history.length > FORECAST_LIMITS.displayFrames || spectrum.predicted.length > FORECAST_LIMITS.displayFrames) && <p className="ain-forecast-chart-note">长时段热力图按有效时长汇总显示；缺失频段留空，频谱曲线仍可选择原始采样帧。</p>}
    {!spectrum.predicted.length && <p className="ain-forecast-chart-note">暂无未来频谱预测；当前仅展示历史观测。</p>}
    {/* 区域电磁态势序列已移到 ForecastDialog 的弹窗层级（放在频谱三视图之前），
        这样它跳出 tab 体系、首屏即可见；这里不再重复渲染。 */}
  </figure>;
}

// 主时钟每秒更新，频谱数据不变时复用已绘制的网格。
export default memo(SpectrumChart);
