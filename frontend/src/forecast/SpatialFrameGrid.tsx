import { record } from '../history/store';
import { useEffect, useMemo, useRef, useState } from "react";
import { fetchSpatialFrames } from "../services/backend";
import type { SpatialFrame, SpatialFrameSeries } from "../services/backend";
import "./SpatialFrameGrid.css";

/** 数值兜底：缺字段/NaN 时显示 "?"，避免把 NaN 渲染进界面（独立审计 P3）。 */
const num = (value: unknown) => (typeof value === "number" && Number.isFinite(value) ? String(Math.round(value)) : "?");

/**
 * 区域电磁态势序列图：过去 N 帧 → 未来 N 帧，可**拖动时间轴**并可**自动播放**。
 *
 * 为什么这么做：申报书要求「序列态势图」。静态并排只能看到"几张贴图"，
 * 拖时间轴/播放才能看出"恶化区域在扩张"这个过程——这正是"预测"要表达的东西。
 *
 * 数据来自接口16（二维区域场），与频谱预测（频率维度）互补：
 * 频谱回答"哪个频段在恶化"，这里回答"哪个区域在恶化"。
 *
 * 实现要点：
 * - 所有帧**一次性取回**（past+future），滑块只在已取回的帧之间切换 → 零延迟；
 *   "未来帧"由后端按格点外推，不是前端插值，所以拖动看到的仍是真实预测值。
 * - 色阶在全部帧上统一计算，否则每帧自适应会让"哪一帧更严重"看不出来。
 * - canvas 而不是 SVG：26×26=676 格 × 多帧，SVG 会明显卡。
 */

const COLOR_STOPS: [number, [number, number, number]][] = [
  [0.0, [49, 54, 149]],
  [0.35, [116, 173, 209]],
  [0.55, [224, 243, 248]],
  [0.72, [254, 224, 144]],
  [0.86, [253, 174, 97]],
  [1.0, [215, 48, 39]],
];

function colorAt(t: number): string {
  const x = Math.max(0, Math.min(1, t));
  for (let i = 1; i < COLOR_STOPS.length; i += 1) {
    const [p1, c1] = COLOR_STOPS[i];
    const [p0, c0] = COLOR_STOPS[i - 1];
    if (x <= p1) {
      const k = (x - p0) / Math.max(1e-6, p1 - p0);
      return `rgb(${Math.round(c0[0] + (c1[0] - c0[0]) * k)},${Math.round(c0[1] + (c1[1] - c0[1]) * k)},${Math.round(c0[2] + (c1[2] - c0[2]) * k)})`;
    }
  }
  return "rgb(215,48,39)";
}

function FrameCanvas({ frame, min, max, big = false }: {
  frame: SpatialFrame; min: number; max: number; big?: boolean;
}) {
  const ref = useRef<HTMLCanvasElement | null>(null);
  useEffect(() => {
    const canvas = ref.current;
    const rows = frame.values.length;
    const cols = rows ? frame.values[0].length : 0;
    if (!canvas || !rows || !cols) return;
    canvas.width = cols;
    canvas.height = rows;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const span = Math.max(1e-6, max - min);
    for (let r = 0; r < rows; r += 1) {
      for (let c = 0; c < cols; c += 1) {
        const value = frame.values[r][c];
        ctx.fillStyle = value == null || !Number.isFinite(value) ? "#e8edf4" : colorAt((value - min) / span);
        // 画布 y 轴向下、数据 y 轴向上 → 翻转行序，与地图方向一致
        ctx.fillRect(c, rows - 1 - r, 1, 1);
      }
    }
  }, [frame, min, max]);
  return <canvas ref={ref} className={big ? "ain-spatial-main-canvas" : "ain-spatial-cell-canvas"} />;
}

/** 未来帧数固定 6 帧；每帧时长由上层"预测时长"换算而来 */
const FUTURE_FRAMES = 6;

/** 可切换的态势指标（接口15 已支持，只是原先写死 rsrp、没有入口） */
const METRIC_OPTIONS = [
  { id: "rsrp", label: "RSRP（覆盖）" },
  { id: "sinr", label: "SINR（质量）" },
  { id: "throughput", label: "吞吐（速率）" },
];

export default function SpatialFrameGrid({
  scenario, baseTime, pastFrames = 8, metric: initialMetric = "rsrp", horizonMinutes = 60, archiveParentId, savedSeries,
}: {
  scenario: string;
  baseTime: string;
  pastFrames?: number;
  /** 初始指标（rsrp / sinr / throughput）；组件内可用「指标」下拉切换 */
  metric?: string;
  /** 预测总时长（分钟），由弹窗的「预测时长」下拉统一控制，本组件不再单独提供开关 */
  horizonMinutes?: number; archiveParentId?: string; savedSeries?: SpatialFrameSeries;
}) {
  const [loaded, setLoaded] = useState<{ key: string; series?: SpatialFrameSeries; error?: string }>({ key: "" });
  const [attempt, setAttempt] = useState(0);
  const [playback, setPlayback] = useState({ key: "", index: -1, playing: false });
  const [selectedMetric, setMetric] = useState(initialMetric);
  // 保存的快照使用其自身指标；在线请求的指标由选择器决定。
  const metric = savedSeries?.metric ?? selectedMetric;

  // 每帧时长按预测总时长反算，让未来帧数保持在 6 帧左右、又不超接口上限：
  //   30分→5分/帧，60→10，120→20，240→40，1440→240（6 帧正好覆盖 24 小时）
  const frameMinutes = Math.max(1, Math.min(240, Math.round(horizonMinutes / FUTURE_FRAMES)));
  // 未来帧数 = 总时长 / 每帧时长（1440/240=6；若有余数则补一帧）
  const futureCount = Math.max(1, Math.min(12, Math.ceil(horizonMinutes / frameMinutes)));
  // 历史帧必须和每帧时长匹配，否则"每帧 60 分钟 + 历史 48 分钟"只能拼出 1 个历史帧
  const historyMinutes = Math.max(6, Math.min(1920, frameMinutes * pastFrames));

  // 请求上下文一变，旧结果立即失效；不用等effect执行，避免一次旧数值配新标签的渲染。
  const requestKey = JSON.stringify([scenario, baseTime, historyMinutes, frameMinutes, pastFrames, futureCount, metric, attempt]);
  const series = savedSeries ?? (loaded.key === requestKey ? loaded.series ?? null : null);
  const error = savedSeries ? "" : loaded.key === requestKey ? loaded.error ?? "" : "";
  const index = playback.key === requestKey ? playback.index : -1;
  const playing = playback.key === requestKey && playback.playing;

  useEffect(() => {
    if (savedSeries) return;
    const controller = new AbortController();
    fetchSpatialFrames(scenario, baseTime, controller.signal,
      { historyMinutes, frameMinutes, pastFrames, futureFrames: futureCount, metric })
      .then((data) => {
        if (controller.signal.aborted) return;
        if (data.metric !== metric) throw new Error("返回指标与本次选择不一致，请重试。");
        setLoaded({ key: requestKey, series: data });
        record({kind:'spatial',parentId:archiveParentId,question:`${metric.toUpperCase()} 空间态势序列`,scope:{scenario,start:data.past[0]?.start??baseTime,end:baseTime},analysisTime:baseTime,status:'completed',payload:{series:data}});
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setLoaded({ key: requestKey, error: reason instanceof Error ? reason.message : "区域态势获取失败" });
      });
    return () => controller.abort();
  }, [scenario, baseTime, historyMinutes, frameMinutes, pastFrames, futureCount, metric, requestKey, archiveParentId, savedSeries]);

  const frames = useMemo(
    () => (series ? [...series.past, ...series.future] : []),
    [series],
  );
  const lastPastIndex = series ? Math.max(0, series.past.length - 1) : 0;
  const active = index < 0 ? lastPastIndex : Math.min(index, Math.max(0, frames.length - 1));

  // 自动播放：在已取回的帧之间循环推进（无需重新请求接口）
  useEffect(() => {
    if (!playing || frames.length < 2) return;
    const timer = window.setInterval(() => {
      setPlayback(prev => {
        const at = prev.key !== requestKey || prev.index < 0 ? lastPastIndex : prev.index;
        return { key: requestKey, index: (at + 1) % frames.length, playing: true };
      });
    }, 700);
    return () => window.clearInterval(timer);
  }, [playing, frames.length, lastPastIndex, requestKey]);

  const metricLabel = metric === "throughput" ? "吞吐量" : metric.toUpperCase();
  const metricControl = <label className="ain-spatial-metric">指标
    <select aria-label="选择态势指标" value={metric} disabled={Boolean(savedSeries)}
      onChange={event => setMetric(event.target.value)}>
      {METRIC_OPTIONS.map(o => <option key={o.id} value={o.id}>{o.label}</option>)}
    </select>
  </label>;
  if (!series || error) return <section className="ain-spatial-grid" aria-label="区域电磁态势序列" aria-busy={!error}>
    <header><h4>区域电磁态势序列</h4>{metricControl}</header>
    <div className="ain-spatial-load-state" role={error ? "alert" : "status"}>
      <p>{error ? `区域态势暂不可用：${error}` : `正在计算${metricLabel}区域态势帧序列……`}</p>
      <p>基准：{baseTime}（北京时间）</p>
      {error && <button type="button" className="ain-button" onClick={() => setAttempt(n => n + 1)}>重试加载</button>}
    </div>
  </section>;
  if (!frames.length) return <p className="ain-forecast-chart-note">该时段没有可用的区域态势帧。</p>;

  const all = frames.flatMap((f) => f.values.flat()).filter((v): v is number => typeof v === "number" && Number.isFinite(v));
  const min = all.length ? Math.min(...all) : 0;
  const max = all.length ? Math.max(...all) : 1;
  const current = frames[active];
  const isFuture = active >= series.past.length;
  const unit = metric === "rsrp" ? " dBm" : metric === "sinr" ? " dB" : metric === "throughput" ? " Mbps" : "";
  const deltaUnit = metric === "throughput" ? " Mbps" : " dB";
  const flat = current.values.flat().filter((v): v is number => typeof v === "number" && Number.isFinite(v));
  const prev = active > 0 ? frames[active - 1] : null;
  const prevFlat = prev ? prev.values.flat().filter((v): v is number => typeof v === "number" && Number.isFinite(v)) : [];
  const kindLabel = isFuture ? `未来${active - series.past.length + 1}` : `过去${series.past.length - active}`;
  // 简述：本帧最低值与相对上一帧的变化，帮用户判断"这一帧比上一帧更糟还是更好"
  const delta = prevFlat.length && flat.length ? Math.min(...flat) - Math.min(...prevFlat) : null;

  return (
    <section className="ain-spatial-grid" aria-label="区域电磁态势序列">
      <header>
        <h4>区域电磁态势序列</h4>
        <span className="ain-spatial-dim">空间维度 · 网络表</span>
        {metricControl}
        <span>
          {/* 缺字段时 numeric() 会给出 NaN，直接 toFixed 会在界面上渲染出
              "每帧 NaN 分钟""区域 X NaN~NaN m"（独立审计 P3）。这里统一兜底成"?"。 */}
          {metricLabel} · {all.length ? `色阶 ${min.toFixed(0)}~${max.toFixed(0)}${unit}` : "暂无有效数值"} ·
          每帧 {num(series.frameMinutes)} 分钟 ·
          <strong className="ain-spatial-cover">
            覆盖 未来 {num(series.future.length * series.frameMinutes)} 分钟
          </strong>
          · 区域 X {num(series.xRange[0])}~{num(series.xRange[1])} m
        </span>
        {/* 预测时长不在这里单独提供下拉：弹窗顶部那一个才是权威控件（它驱动接口11
            重新预测），本组件只按它换算每帧时长，避免两个下拉各说各话。 */}
      </header>
      <div className="ain-spatial-caption">
        <span>空间 KPI 估计（当前配置下的网络 KPI 视图）；频谱预警另属**原始频谱参考风险**，未模拟配置影响。</span>
        <details className="ain-spatial-help">
          <summary>图表说明</summary>
          <p>每个格子为该位置网络 KPI 的插值估计，用于观察区域变化；上方预警按频谱逐频段数值判定，不能仅凭此图颜色判断是否触发告警。</p>
          <p>每帧 {num(series.frameMinutes)} 分钟；本图历史范围：{series.past[0]?.start ?? "无历史帧"} — {series.past.at(-1)?.end ?? baseTime}。
            调整查看未来范围会改变每帧时长和历史取样范围。本图与弹窗顶部的参考历史是两套窗口。</p>
          {/* 采样与插值必须说清（核查清单第 8 条）：26×26 网格不等于 676 个位置都真有测量，
              数据来自 3 个移动 UE 的有限轨迹 + IDW 插值 + 逐格外推。 */}
          <p><strong>颜色怎么读</strong>：{metricLabel} 的色阶在**全部帧上统一计算**，所以可直接跨帧比颜色。
            以 RSRP 为例：<strong>黄＝覆盖好</strong>（−80 dBm 以上）、<strong>蓝＝覆盖差</strong>（−110 dBm 以下）；
            因此"从黄到蓝"表示该区域覆盖在变差（例如故障开始的那几帧）。</p>
          <p><strong>采样与插值</strong>：原始数据只有 3 个移动 UE 的轨迹采样，网格由 IDW 插值生成，
            格点数量不代表该位置有实测；离采样点越远的格子越不可靠。未来帧是趋势外推，不是预测的实测值。</p>
          {series.note && <p>{series.note}</p>}
        </details>
      </div>

      <div className="ain-forecast-spatial-stage">
        <figure className={`ain-spatial-main${isFuture ? " is-future" : ""}`}>
          {flat.length ? <FrameCanvas frame={current} min={min} max={max} big /> : <p className="ain-spatial-empty-frame">本帧暂无有效采样</p>}
        </figure>

        <div className="ain-spatial-side">
          <button type="button" className="ain-button ain-spatial-play" onClick={() => setPlayback({ key: requestKey, index, playing: !playing })}
            aria-pressed={playing}>
            {playing ? "⏸ 暂停" : "▶ 播放"}
          </button>
          <input type="range" className="ain-spatial-range" min={0} max={frames.length - 1} value={active}
            aria-label="态势帧时间轴"
            onChange={(event) => { setPlayback({ key: requestKey, index: Number(event.target.value), playing: false }); }} />
          <span className="ain-spatial-counter">
            第 {active + 1} / {frames.length} 帧（过去 {series.past.length} 帧 + 预测 {series.future.length} 帧）
          </span>
          <dl className="ain-spatial-info">
            <dt>时间</dt>
            <dd>{current.start} — {current.end}</dd>
            <dt>简述</dt>
            <dd>
              {kindLabel} · {flat.length ? `最低 ${Math.min(...flat).toFixed(1)}${unit} / 最高 ${Math.max(...flat).toFixed(1)}${unit}` : "本帧暂无有效采样"}
              {delta !== null && (
                <> · 较上一帧 {delta >= 0 ? "+" : ""}{delta.toFixed(1)}{deltaUnit}</>
              )}
            </dd>
          </dl>
        </div>
      </div>

      <div className="ain-spatial-row" aria-label="全部帧">
        {frames.map((frame, i) => {
          const future = i >= series.past.length;
          const label = future ? `未来${i - series.past.length + 1}` : `过去${series.past.length - i}`;
          return (
            <button key={`${label}-${frame.start}`} type="button"
              className={`ain-spatial-cell${future ? " is-future" : ""}${i === active ? " is-active" : ""}`}
              title={`${frame.start} — ${frame.end}`}
              onClick={() => { setPlayback({ key: requestKey, index: i, playing: false }); }}>
              <FrameCanvas frame={frame} min={min} max={max} />
              <span>{label}</span>
              <time>{frame.start.slice(11, 16)}</time>
            </button>
          );
        })}
      </div>
    </section>
  );
}
