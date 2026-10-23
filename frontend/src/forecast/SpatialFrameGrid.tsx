import { useEffect, useMemo, useRef, useState } from "react";
import { fetchSpatialFrames } from "../services/backend";
import type { SpatialFrame, SpatialFrameSeries } from "../services/backend";
import "./SpatialFrameGrid.css";

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
        ctx.fillStyle = value == null ? "#e8edf4" : colorAt((value - min) / span);
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
  scenario, baseTime, pastFrames = 8, metric: initialMetric = "rsrp", horizonMinutes = 60,
}: {
  scenario: string;
  baseTime: string;
  pastFrames?: number;
  /** 初始指标（rsrp / sinr / throughput）；组件内可用「指标」下拉切换 */
  metric?: string;
  /** 预测总时长（分钟），由弹窗的「预测时长」下拉统一控制，本组件不再单独提供开关 */
  horizonMinutes?: number;
}) {
  const [series, setSeries] = useState<SpatialFrameSeries | null>(null);
  const [error, setError] = useState("");
  const [index, setIndex] = useState(-1);       // -1 表示"跟随数据默认值"
  const [playing, setPlaying] = useState(false);
  // 指标可切换：接口15 本来就支持 rsrp / sinr / throughput，切换后色阶、单位、
  // "最低/最高/较上一帧"全部自动跟着变（都由 metric 推导），无需额外处理
  const [metric, setMetric] = useState(initialMetric);

  // 每帧时长按预测总时长反算，让未来帧数保持在 6 帧左右、又不超接口上限：
  //   30分→5分/帧，60→10，120→20，240→40，1440→240（6 帧正好覆盖 24 小时）
  const frameMinutes = Math.max(1, Math.min(240, Math.round(horizonMinutes / FUTURE_FRAMES)));
  // 未来帧数 = 总时长 / 每帧时长（1440/240=6；若有余数则补一帧）
  const futureCount = Math.max(1, Math.min(12, Math.ceil(horizonMinutes / frameMinutes)));
  // 历史帧必须和每帧时长匹配，否则"每帧 60 分钟 + 历史 48 分钟"只能拼出 1 个历史帧
  const historyMinutes = Math.max(6, Math.min(1920, frameMinutes * pastFrames));

  useEffect(() => {
    const controller = new AbortController();
    fetchSpatialFrames(scenario, baseTime, controller.signal,
      { historyMinutes, frameMinutes, pastFrames, futureFrames: futureCount, metric })
      .then((data) => { if (!controller.signal.aborted) { setSeries(data); setError(""); } })
      .catch((reason) => {
        if (controller.signal.aborted) return;
        setError(reason instanceof Error ? reason.message : "区域态势获取失败");
      });
    return () => controller.abort();
  }, [scenario, baseTime, historyMinutes, frameMinutes, pastFrames, futureCount, metric]);

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
      setIndex((prev) => {
        const at = prev < 0 ? lastPastIndex : prev;
        return (at + 1) % frames.length;
      });
    }, 700);
    return () => window.clearInterval(timer);
  }, [playing, frames.length, lastPastIndex]);

  if (!series && !error) return <p className="ain-spatial-note">正在计算区域态势帧序列……</p>;
  if (error) return <p className="ain-forecast-chart-note">区域态势暂不可用：{error}</p>;
  if (!series || !frames.length) return <p className="ain-forecast-chart-note">该时段没有可用的区域态势帧。</p>;

  const all = frames.flatMap((f) => f.values.flat()).filter((v): v is number => typeof v === "number");
  const min = all.length ? Math.min(...all) : 0;
  const max = all.length ? Math.max(...all) : 1;
  const current = frames[active];
  const isFuture = active >= series.past.length;
  const unit = metric === "rsrp" ? " dBm" : metric === "sinr" ? " dB" : "";
  const flat = current.values.flat().filter((v): v is number => typeof v === "number");
  const prev = active > 0 ? frames[active - 1] : null;
  const prevFlat = prev ? prev.values.flat().filter((v): v is number => typeof v === "number") : [];
  const kindLabel = isFuture ? `未来${active - series.past.length + 1}` : `过去${series.past.length - active}`;
  // 简述：本帧最低值与相对上一帧的变化，帮用户判断"这一帧比上一帧更糟还是更好"
  const delta = prevFlat.length && flat.length ? Math.min(...flat) - Math.min(...prevFlat) : null;

  return (
    <section className="ain-spatial-grid" aria-label="区域电磁态势序列">
      <header>
        <h4>区域电磁态势序列</h4>
        <span className="ain-spatial-dim">空间维度 · 网络表</span>
        <label className="ain-spatial-metric">
          指标
          <select aria-label="选择态势指标" value={metric}
            onChange={(event) => { setPlaying(false); setIndex(-1); setMetric(event.target.value); }}>
            {METRIC_OPTIONS.map(o => (
              <option key={o.id} value={o.id}>{o.label}</option>
            ))}
          </select>
        </label>
        <span>
          {metric.toUpperCase()} · 色阶 {min.toFixed(0)}~{max.toFixed(0)}{unit} · 每帧 {series.frameMinutes} 分钟 ·
          <strong className="ain-spatial-cover">
            覆盖 未来 {series.future.length * series.frameMinutes} 分钟
          </strong>
          · 区域 X {series.xRange[0].toFixed(0)}~{series.xRange[1].toFixed(0)} m
        </span>
        {/* 预测时长不在这里单独提供下拉：弹窗顶部那一个才是权威控件（它驱动接口11
            重新预测），本组件只按它换算每帧时长，避免两个下拉各说各话。 */}
      </header>
      {/* 口径说明：这张图和上方预警看的是两个不同指标，必须写清楚，
          否则"图偏蓝却没有预警"会被当成 bug（实测队友与多人第一反应都是这样）。 */}
      <p className="ain-spatial-scope">
        本图是<b>空间维度</b>（网络表插值）：每个格子是该位置用户 KPI 的估计值，用来看<b>哪个区域</b>在变化。
        上方预警是<b>频率维度</b>（频谱表逐频段）：功率 &lt; −95 dBm 或占用率 &gt; 80% 且满足频段数与持续时间才触发。
        两者指标不同，<b>“图偏蓝但未触发预警”属正常现象</b>。
      </p>

      <div className="ain-spatial-stage">
        <figure className={`ain-spatial-main${isFuture ? " is-future" : ""}`}>
          <FrameCanvas frame={current} min={min} max={max} big />
        </figure>

        <div className="ain-spatial-side">
          <button type="button" className="ain-button ain-spatial-play" onClick={() => setPlaying((p) => !p)}
            aria-pressed={playing}>
            {playing ? "⏸ 暂停" : "▶ 播放"}
          </button>
          <input type="range" className="ain-spatial-range" min={0} max={frames.length - 1} value={active}
            aria-label="态势帧时间轴"
            onChange={(event) => { setPlaying(false); setIndex(Number(event.target.value)); }} />
          <span className="ain-spatial-counter">
            第 {active + 1} / {frames.length} 帧（过去 {series.past.length} 帧 + 预测 {series.future.length} 帧）
          </span>
          <dl className="ain-spatial-info">
            <dt>时间</dt>
            <dd>{current.start} — {current.end}</dd>
            <dt>简述</dt>
            <dd>
              {kindLabel} · 最低 {Math.min(...flat).toFixed(1)}{unit} / 最高 {Math.max(...flat).toFixed(1)}{unit}
              {delta !== null && (
                <> · 较上一帧 {delta >= 0 ? "+" : ""}{delta.toFixed(1)} dB</>
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
              onClick={() => { setPlaying(false); setIndex(i); }}>
              <FrameCanvas frame={frame} min={min} max={max} />
              <span>{label}</span>
              <time>{frame.start.slice(11, 16)}</time>
            </button>
          );
        })}
      </div>
      <p className="ain-spatial-note">{series.note}</p>
    </section>
  );
}
