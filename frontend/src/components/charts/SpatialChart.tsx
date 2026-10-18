/* eslint-disable react-refresh/only-export-components -- 本文件沿用共用SVG图元与坐标工具的统一导出接口。 */
import type { ReactNode } from "react";
import type { DashboardData, NetworkNode } from "../../types/backend";
import "./SpatialCharts.css";

// 两张图共用横向画布；X/Y 均为原始米制坐标，各自映射到对应显示轴。
// area 由场景提供；回放使用固定部署边界，不能根据每帧 UE 或估计点位重新缩放。
// 图框尽量撑满画布，四周只留刻度标签所需的最小空间，两张图完全同尺寸对齐。
const SCENE_FRAME = { width: 640, height: 300, left: 56, right: 24, top: 42, bottom: 48 };
export const SCENE_VIEWBOX = `0 0 ${SCENE_FRAME.width} ${SCENE_FRAME.height}`;

export function createProjection(area: DashboardData["area"]) {
  const { xMin, yMin, xMax, yMax } = area;
  const width = xMax - xMin;
  const height = yMax - yMin;
  if (
    !Number.isFinite(width) ||
    !Number.isFinite(height) ||
    width <= 0 ||
    height <= 0
  )
    return null;
  // 仅改变像素显示范围；数据范围、越界判定和坐标值保持原样。
  const maxW = SCENE_FRAME.width - SCENE_FRAME.left - SCENE_FRAME.right;
  const maxH = SCENE_FRAME.height - SCENE_FRAME.top - SCENE_FRAME.bottom;
  const scaleX = maxW / width;
  const scaleY = maxH / height;
  const left = SCENE_FRAME.left;
  const top = SCENE_FRAME.top;
  return {
    left,
    top,
    width: width * scaleX,
    height: height * scaleY,
    x: (value: number) => left + (value - xMin) * scaleX,
    y: (value: number) => top + (yMax - value) * scaleY,
    contains: (point: { x: number; y: number }) =>
      Number.isFinite(point.x) &&
      Number.isFinite(point.y) &&
      point.x >= xMin &&
      point.x <= xMax &&
      point.y >= yMin &&
      point.y <= yMax,
  };
}
export type Projection = NonNullable<ReturnType<typeof createProjection>>;

export const SIGNAL_BANDS = [
  { label: "< −90", color: "#67768a" },
  { label: "−90～−80", color: "#2e69d2" },
  { label: "−80～−70", color: "#36b7f3" },
  { label: "−70～−60", color: "#27d7d2" },
  { label: "≥ −60", color: "#58edc6" },
];
export function signalColor(value: number) {
  const index =
    value < -90 ? 0 : value < -80 ? 1 : value < -70 ? 2 : value < -60 ? 3 : 4;
  return SIGNAL_BANDS[index].color;
}

// 圆整步长刻度：只取 1/2/5 × 10^n 的步长，刻度值整齐（如 100/200/300）
function niceStep(rough: number): number {
  const pow = Math.pow(10, Math.floor(Math.log10(rough)));
  const n = rough / pow;
  return (n < 1.5 ? 1 : n < 3 ? 2 : n < 7 ? 5 : 10) * pow;
}
function buildTicks(min: number, max: number, count = 4): number[] {
  const span = max - min;
  if (span <= 0) return [min];
  const step = niceStep(span / count);
  const first = Math.ceil(min / step) * step;
  const last = Math.floor(max / step) * step;
  const ticks: number[] = [];
  for (let v = first; v <= last + 1e-9; v += step) ticks.push(v);
  return ticks.length ? ticks : [min];
}

export function SceneAxes({
  area,
  projection: p,
}: {
  area: DashboardData["area"];
  projection: Projection;
}) {
  const xTicks = buildTicks(area.xMin, area.xMax);
  const yTicks = buildTicks(area.yMin, area.yMax);
  return (
    <g className="ain-scene-axes" aria-hidden="true">
      <rect
        x={p.left}
        y={p.top}
        width={p.width}
        height={p.height}
        fill="none"
        stroke="#526479"
      />
      {xTicks.map((value) => (
        <g key={`x${value}`}>
          <line
            x1={p.x(value)}
            y1={p.top}
            x2={p.x(value)}
            y2={p.top + p.height}
          />
          <text x={p.x(value)} y={p.top + p.height + 22} textAnchor="middle">
            {value}
          </text>
        </g>
      ))}
      {yTicks.map((value) => (
        <g key={`y${value}`}>
          <line
            x1={p.left}
            y1={p.y(value)}
            x2={p.left + p.width}
            y2={p.y(value)}
          />
          <text x={p.left - 9} y={p.y(value) + 4} textAnchor="end">
            {value}
          </text>
        </g>
      ))}
      <text x={p.left + p.width} y={p.top + p.height + 43} textAnchor="end">
        X / m
      </text>
      <text x={p.left - 8} y={p.top - 8} textAnchor="end">
        Y / m
      </text>
    </g>
  );
}

export function SceneNode({
  node,
  projection,
  selected,
  highlighted = false,
  onSelect,
}: {
  node: NetworkNode;
  projection: Projection;
  selected: boolean;
  highlighted?: boolean;
  onSelect: (node: NetworkNode) => void;
}) {
  const ap = node.type === "ap";
  const color = selected ? "#ffbb4a" : node.missingSample ? "#8b9caf" : ap ? "#ff5657" : "#22dfb6";
  return (
    <g
      className={`ain-scene-node${selected ? " is-selected" : ""}${node.missingSample ? " is-missing-sample" : ""}`}
      transform={`translate(${projection.x(node.x)} ${projection.y(node.y)})`}
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      aria-label={ap ? `关注 ${node.id} 的服务连接` : `选择 ${node.id}`}
      onClick={() => onSelect(node)}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onSelect(node);
        }
      }}
    >
      <title>{`${node.id} · (${Number(node.x.toFixed(2))}, ${Number(node.y.toFixed(2))}) m${node.positionNote ? ` · ${node.positionNote}` : ""}${node.lastSeen ? ` · 最近采样：${node.lastSeen}` : ""}`}</title>
      <circle className="ain-node-hit-area" r={23} fill="transparent" />
      {ap ? (
        <polygon
          points="0,-12 10,10 -10,10"
          fill={color}
          stroke="#102036"
          strokeWidth={2}
        />
      ) : (
        <circle r={8} fill={color} stroke="#102036" strokeWidth={2} />
      )}
      {node.missingSample && <circle r={15} fill="none" stroke="#8b9caf" strokeWidth={1.5} strokeDasharray="3 3" />}
      {(selected || highlighted) && (
        <circle
          r={22}
          fill="none"
          stroke={selected ? "#ffbb4a" : "#65d5f5"}
          strokeWidth={1.5}
        />
      )}
      <text y={-27} textAnchor="middle" fill={color}>
        {node.id}
      </text>
    </g>
  );
}

export default function SpatialChart({
  title,
  description,
  className,
  children,
  footer,
}: {
  title: string;
  description: string;
  className: string;
  children: ReactNode;
  footer: ReactNode;
}) {
  return (
    <section className={`ain-chart-slot ain-spatial-chart ${className}`}>
      <div className="ain-slot-heading">
        <h2>{title}</h2>
        <p>{description}</p>
      </div>
      <div className="ain-spatial-stage">{children}</div>
      <div className="ain-spatial-footer">{footer}</div>
    </section>
  );
}
