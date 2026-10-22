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

/** 预测时长档位：内部换算成"每帧分钟数"，帧数固定 6 帧 */
const HORIZON_OPTIONS = [
  { label: "30 分钟", minutes: 5 },
  { label: "1 小时", minutes: 10 },
  { label: "2 小时", minutes: 20 },
  { label: "4 小时", minutes: 40 },
];
const FUTURE_FRAMES = 6;

export default function SpatialFrameGrid({
  scenario, baseTime, frameMinutes = 10, pastFrames = 8, futureFrames = FUTURE_FRAMES, metric = "rsrp",
}: {
  scenario: string;
  baseTime: string;
  frameMinutes?: number;
  pastFrames?: number;
  futureFrames?: number;
  metric?: string;
}) {
  const [series, setSeries] = useState<SpatialFrameSeries | null>(null);
  const [error, setError] = useState("");
  const [index, setIndex] = useState(-1);       // -1 表示"跟随数据默认值"
  const [playing, setPlaying] = useState(false);
  const [horizon, setHorizon] = useState(frameMinutes);   // 每帧分钟数，由"预测时长"决定

  useEffect(() => {
    const controller = new AbortController();
    fetchSpatialFrames(scenario, baseTime, controller.signal,
      { frameMinutes: horizon, pastFrames, futureFrames, metric })
      .then((data) => { if (!controller.signal.aborted) { setSeries(data); setError(""); } })
      .catch((reason) => {
        if (controller.signal.aborted) return;
        setError(reason instanceof Error ? reason.message : "区域态势获取失败");
      });
    return () => controller.abort();
  }, [scenario, baseTime, horizon, pastFrames, futureFrames, metric]);

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

  return (
    <section className="ain-spatial-grid" aria-label="区域电磁态势序列">
      <header>
        <h4>区域电磁态势序列</h4>
        <span>
          {metric.toUpperCase()} · 色阶 {min.toFixed(0)}~{max.toFixed(0)}{unit} · 每帧 {series.frameMinutes} 分钟 ·
          区域 X {series.xRange[0].toFixed(0)}~{series.xRange[1].toFixed(0)} m
        </span>
        <label className="ain-spatial-horizon">
          预测时长
          <select aria-label="选择预测时长" value={horizon}
            onChange={(event) => { setPlaying(false); setIndex(-1); setHorizon(Number(event.target.value)); }}>
            {HORIZON_OPTIONS.map(o => (
              <option key={o.minutes} value={o.minutes}>{o.label}</option>
            ))}
          </select>
        </label>
      </header>

      <div className="ain-spatial-stage">
        <figure className={`ain-spatial-main${isFuture ? " is-future" : ""}`}>
          <FrameCanvas frame={current} min={min} max={max} big />
          <figcaption>
            <strong>{isFuture ? `未来${active - series.past.length + 1}` : `过去${series.past.length - active}`}</strong>
            <span>{current.start} → {current.end}</span>
            <span>最低 {Math.min(...flat).toFixed(1)}{unit} · 最高 {Math.max(...flat).toFixed(1)}{unit}</span>
          </figcaption>
        </figure>

        <div className="ain-spatial-controls">
          <button type="button" className="ain-button" onClick={() => setPlaying((p) => !p)}
            aria-pressed={playing}>
            {playing ? "⏸ 暂停" : "▶ 播放"}
          </button>
          <input type="range" min={0} max={frames.length - 1} value={active}
            aria-label="态势帧时间轴"
            onChange={(event) => { setPlaying(false); setIndex(Number(event.target.value)); }} />
          <span className="ain-spatial-hint">
            第 {active + 1} / {frames.length} 帧（过去 {series.past.length} 帧 + 预测 {series.future.length} 帧）
          </span>
        </div>
      </div>

      <div className="ain-spatial-row">
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
