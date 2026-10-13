import type { DashboardData, NetworkNode, SpatialMapData } from "../../types/backend";
import { getSpatialArea } from "../../services/spatialMap";
import SpatialChart, { createProjection, SCENE_VIEWBOX, SceneAxes, SceneNode } from "./SpatialChart";
import "./RadioMapSpatial.css";

const BANDS = [
  { label: "< −105", color: "#67768a" },
  { label: "−105～−95", color: "#2e69d2" },
  { label: "−95～−85", color: "#36b7f3" },
  { label: "−85～−75", color: "#27d7d2" },
  { label: "≥ −75", color: "#58edc6" },
];
const VARIANCE_COLORS = ["#36b7f3", "#27d7d2", "#d7df81", "#f3b45b", "#ef7769"];
function signalColor(value: number) {
  return BANDS[value < -105 ? 0 : value < -95 ? 1 : value < -85 ? 2 : value < -75 ? 3 : 4].color;
}
interface Props {
  data: DashboardData;
  spatial: SpatialMapData | null;
  pending: boolean;
  error: string;
  ueId: string;
  focusedApId: string | null;
  onSelectUE: (id: string) => void;
  onFocusAP: (id: string) => void;
  onRetry: () => void;
}

function MapLayer({ props, variance = false }: { props: Props; variance?: boolean }) {
  const { data, spatial, ueId, focusedApId, onSelectUE, onFocusAP } = props;
  const area = spatial ? getSpatialArea(spatial, data.area) : data.area;
  const projection = createProjection(area);
  if (!projection) return <p className="ain-spatial-empty">坐标范围无效，无法绘制地图。</p>;
  // 前端采样网格使用左下角坐标；空间接口点位按网格中心绘制。
  const cells = spatial ? spatial.points.flatMap(point => {
    const value = variance ? point.variance : point.value;
    if (value === null) return [];
    return [{ x: point.x - spatial.cellSize.x / 2, y: point.y - spatial.cellSize.y / 2,
      width: spatial.cellSize.x, height: spatial.cellSize.y, value,
      detail: `中心 (${point.x}, ${point.y}) m：${variance ? `方差 ${value.toFixed(2)} dB²` : `RSRP ${value.toFixed(1)} dBm`}` }];
  }) : data.cells.map(point => ({ x: point.x, y: point.y, width: data.gridSize, height: data.gridSize,
    value: point.value, detail: `X ${point.x}–${point.x + data.gridSize}m / Y ${point.y}–${point.y + data.gridSize}m：RSRP ${point.value.toFixed(1)} dBm；${point.count} 次采样` }));
  const values = cells.map(cell => cell.value);
  const min = Math.min(...values), max = Math.max(...values);
  function fill(value: number) {
    if (!variance) return signalColor(value);
    const fraction = max === min ? 0 : (value - min) / (max - min);
    return VARIANCE_COLORS[Math.min(4, Math.floor(fraction * 5))];
  }
  function select(node: NetworkNode) {
    if (node.type === "ap") onFocusAP(node.id); else onSelectUE(node.id);
  }
  return <svg className="ain-spatial-svg" viewBox={SCENE_VIEWBOX} role="group"
    aria-label={variance ? "Kriging 空间估计方差图" : spatial?.method === "kriging" ? "Kriging RSRP 空间估计图" : "RSRP 采样网格图"}>
    {cells.map(cell => <rect key={`${cell.x},${cell.y}`} x={projection.x(cell.x)} y={projection.y(cell.y + cell.height)}
      width={projection.x(cell.x + cell.width) - projection.x(cell.x)}
      height={projection.y(cell.y) - projection.y(cell.y + cell.height)}
      fill={fill(cell.value)} opacity={0.92} stroke="#1c2c45" strokeWidth={0.35}>
      <title>{cell.detail}</title>
    </rect>)}
    <SceneAxes area={area} projection={projection} />
    {data.nodes.map(node => <SceneNode key={node.id} node={node} projection={projection}
      selected={node.type === "ue" ? node.id === ueId : node.id === focusedApId} onSelect={select} />)}
  </svg>;
}

export default function RadioMapChart(props: Props) {
  const { data, spatial, pending, error, onRetry } = props;
  const method = spatial?.method;
  const title = method === "kriging" ? "Radio Map / 空间估计" : "Radio Map / 采样网格";
  const description = spatial
    ? `${method === "kriging" ? "Kriging" : "后端网格均值"} · ${spatial.gridCount} 个点位 · ${spatial.samplePoints} 个模型输入采样点`
    : `${data.cells.length} 个采样网格 · RSRP 均值 · ${data.gridSize}m 网格`;
  return <SpatialChart title={title} className="ain-radio-map" description={description}
    footer={<>
      <div className="ain-spatial-key">{BANDS.map(band => <span key={band.label}><i style={{ background: band.color }} />{band.label} dBm</span>)}</div>
      {method === "kriging" ? <p>所选历史时段的空间估计，可估计未采样位置；不表示未来时间的预测，也不是直接测量值。</p>
        : <p>所选历史时段的采样网格均值。空白表示无采样，不代表信号为零。</p>}
      <p>节点为所选范围内最新位置；背景为整个时段的统计或估计结果。</p>
      {spatial?.varianceNotice && method === "kriging" && <p className="ain-spatial-notice">{spatial.varianceNotice}</p>}
    </>}>
    {pending && <p className="ain-spatial-progress" role="status">正在加载空间估计，暂显示前端采样网格…</p>}
    {error && <div className="ain-spatial-request-error" role="alert">
      <p>空间估计加载失败：{error}。当前显示前端采样网格。</p>
      <button type="button" className="ain-button" onClick={onRetry}>重试空间估计</button>
    </div>}
    {spatial && !spatial.points.length ? <p className="ain-spatial-empty">空间接口返回空数据，当前范围没有可绘制点位。</p>
      : <MapLayer props={props} />}
  </SpatialChart>;
}

export function RadioMapVarianceChart(props: Props) {
  const { spatial, pending, error } = props;
  const values = spatial?.points.flatMap(point => point.variance === null ? [] : [point.variance]) || [];
  const hasVariance = spatial?.method === "kriging" && values.length > 0;
  const min = hasVariance ? Math.min(...values) : 0;
  const max = hasVariance ? Math.max(...values) : 0;
  const description = hasVariance
    ? `${values.length} 个有效方差点 · 与 RSRP 地图使用同一坐标范围`
    : pending ? "空间估计计算中" : "当前没有可用的估计方差";
  return <SpatialChart title="空间不确定性 / Kriging 方差" description={description} className="ain-radio-variance"
    footer={<>
      {hasVariance && min === max && <p>所有有效点的方差均为 {min.toFixed(2)} dB²，使用单一颜色展示。</p>}
      {hasVariance && min !== max && <div className="ain-variance-legend">
        <span>{min.toFixed(2)} dB²</span><i aria-hidden="true" /><span>{max.toFixed(2)} dB²</span>
      </div>}
      <p>方差越大，模型估计越不确定；不代表信号强弱，也不是置信百分比。</p>
      {hasVariance && <p>色标按当前时段的方差范围生成，跨时段比较请查看具体数值。空白点表示未返回方差。</p>}
    </>}>
    {hasVariance ? <MapLayer props={props} variance /> : <p className="ain-spatial-empty" role="status">
      {pending ? "正在等待空间估计结果…" : error ? "空间估计加载失败，暂无法展示方差。"
        : spatial?.varianceNotice || "空间接口尚未提供可绘制的方差。"}
    </p>}
  </SpatialChart>;
}
