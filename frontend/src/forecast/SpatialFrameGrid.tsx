import { useEffect, useRef, useState } from "react";
import { fetchSpatialFrames } from "../services/backend";
import type { SpatialFrame, SpatialFrameSeries } from "../services/backend";
import "./SpatialFrameGrid.css";

/**
 * 区域电磁态势的序列态势图（过去N帧 → 未来N帧）。
 *
 * 与频率条的区别：每帧是一张 grid×grid 的**二维区域场**（接口16），
 * 所以画出来是一块有形状的区域热力图，而不是 50 条横纹。故障发生时能看到
 * 一片深色区域随时间扩张 —— 这正是"预测覆盖区域电磁态势"该有的样子。
 *
 * 用 canvas 而不是 SVG：26×26=676 格，9 帧就是 6084 个矩形，SVG 会明显卡；
 * canvas 一次性画完再靠 CSS 放大（image-rendering: pixelated 保持清晰）。
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

function FrameCanvas({ frame, min, max }: { frame: SpatialFrame; min: number; max: number }) {
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
        // 画布 y 轴向下，数据 y 轴向上 → 翻转行序，让区域方向与地图一致
        ctx.fillRect(c, rows - 1 - r, 1, 1);
      }
    }
  }, [frame, min, max]);

  return <canvas ref={ref} className="ain-spatial-cell-canvas" />;
}

export default function SpatialFrameGrid({ scenario, baseTime, frameMinutes = 6, pastFrames = 6, futureFrames = 3, metric = "rsrp" }: {
  scenario: string;
  baseTime: string;
  frameMinutes?: number;
  pastFrames?: number;
  futureFrames?: number;
  metric?: string;
}) {
  const [series, setSeries] = useState<SpatialFrameSeries | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    fetchSpatialFrames(scenario, baseTime, controller.signal,
      { frameMinutes, pastFrames, futureFrames, metric })
      .then((data) => { if (!controller.signal.aborted) { setSeries(data); setError(""); } })
      .catch((reason) => {
        if (controller.signal.aborted) return;
        setError(reason instanceof Error ? reason.message : "区域态势获取失败");
      });
    return () => controller.abort();
  }, [scenario, baseTime, frameMinutes, pastFrames, futureFrames, metric]);

  // loading 由数据推导，避免在 effect 里 setState（react-hooks/set-state-in-effect）
  const loading = !series && !error;
  if (loading) return <p className="ain-spatial-note">正在计算区域态势帧序列……</p>;
  if (error) return <p className="ain-forecast-chart-note">区域态势暂不可用：{error}</p>;
  if (!series || (!series.past.length && !series.future.length)) {
    return <p className="ain-forecast-chart-note">该时段没有可用的区域态势帧。</p>;
  }

  // 所有帧共用一个色阶，否则各帧之间无法比较（严不严重看不出来）
  const all = [...series.past, ...series.future]
    .flatMap((f) => f.values.flat())
    .filter((v): v is number => typeof v === "number");
  const min = all.length ? Math.min(...all) : 0;
  const max = all.length ? Math.max(...all) : 1;

  const cell = (frame: SpatialFrame, label: string, future: boolean) => (
    <figure className={`ain-spatial-cell${future ? " is-future" : ""}`} key={`${label}-${frame.start}`}>
      <FrameCanvas frame={frame} min={min} max={max} />
      <figcaption>
        <span>{label}</span>
        <time>{frame.start.slice(11, 16)}</time>
      </figcaption>
      <title>{`${frame.start} — ${frame.end}（${series.rows}×${series.cols} 网格）`}</title>
    </figure>
  );

  return (
    <section className="ain-spatial-grid" aria-label="区域电磁态势序列">
      <header>
        <h4>区域电磁态势序列</h4>
        <span>
          {metric.toUpperCase()} · 色阶 {min.toFixed(0)} ~ {max.toFixed(0)}
          {metric === "rsrp" ? " dBm" : ""} · 每帧 {series.frameMinutes} 分钟 ·
          区域 X {series.xRange[0].toFixed(0)}~{series.xRange[1].toFixed(0)} m
        </span>
      </header>
      <div className="ain-spatial-row">
        {series.past.map((f, i) => cell(f, `过去${series.past.length - i}`, false))}
        {series.future.length > 0 && <span className="ain-spatial-sep" aria-hidden="true" />}
        {series.future.map((f, i) => cell(f, `未来${i + 1}`, true))}
      </div>
      <p className="ain-spatial-note">{series.note}</p>
    </section>
  );
}
