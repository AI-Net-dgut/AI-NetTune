import { useState } from "react";
import type { DashboardData, NetworkLink, NetworkNode } from "../../types/backend";
import { displayMetric } from "../../services/metrics";
import { LINK_METRICS, linkMetricColor, linkMetricRange } from "../../services/topology";
import type { LinkMetric } from "../../services/topology";
import "./TopologyInteraction.css";
import SpatialChart, {
  createProjection,
  SCENE_VIEWBOX,
  SceneAxes,
  SceneNode,
} from "./SpatialChart";

interface Props {
  data: DashboardData;
  ueId: string;
  focusedApId: string | null;
  selectedLinkId: string | null;
  onSelectUE: (id: string) => void;
  onFocusAP: (id: string) => void;
  onSelectLink: (link: NetworkLink) => void;
}
export default function TopologyChart({
  data,
  ueId,
  focusedApId,
  selectedLinkId,
  onSelectUE,
  onFocusAP,
  onSelectLink,
}: Props) {
  const [metric, setMetric] = useState<LinkMetric>("rsrp");
  const meta = LINK_METRICS[metric];
  const projection = createProjection(data.area);
  const nodes = data.nodes.filter((node) => projection?.contains(node));
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const links = data.links.filter(
    (link) =>
      byId.get(link.source)?.type === "ap" &&
      byId.get(link.target)?.type === "ue",
  );
  const highlighted = new Set(
    links
      .filter((link) =>
        selectedLinkId ? link.id === selectedLinkId : focusedApId ? link.source === focusedApId : link.target === ueId,
      )
      .flatMap((link) => [link.source, link.target]),
  );
  const range = linkMetricRange(links, metric);
  const selectNode = (node: NetworkNode) => {
    if (node.type === "ue") onSelectUE(node.id);
    else onFocusAP(node.id);
  };
  return (
    <SpatialChart
      title="网络拓扑"
      className="ain-topology-chart"
      description={`${nodes.length} 个有效节点 / ${links.length} 条服务连接`}
      footer={
        <>
          <div className="ain-spatial-key">
            <span>
              <i className="ain-key-ap" />
              AP
            </span>
            <span>
              <i className="ain-key-ue" />
              UE
            </span>
            <span>
              <i className="ain-key-selected" />
              当前选择
            </span>
          </div>
          {range && (range.min === range.max ? <p>当前有效链路 {meta.label} 均为 {displayMetric(range.min)} {meta.unit}。</p>
            : <div className="ain-topology-range"><span>{displayMetric(range.min)} {meta.unit}</span><i aria-hidden="true" /><span>{displayMetric(range.max)} {meta.unit}</span></div>)}
          <p>连线颜色表示当前窗口内的 {meta.label} 相对大小；金色外圈表示关注连接，不作为故障判定。</p>
          <p>
            {selectedLinkId ? "已选择一条链路，详情显示在地图下方。" : focusedApId
              ? `关注 ${focusedApId}：高亮其服务连接。`
              : `分析 ${ueId || "未选择对象"}：高亮其服务连接。`}
          </p>
          <p>点击 AP、UE 或连线查看详情。节点位置取最新采样；连线指标为窗口内连接均值。最新采样：{data.metrics.at(-1)?.backendTime}（北京时间）。</p>
          {links.length < data.links.length && (
            <p>
              {data.links.length - links.length}{" "}
              条连接因端点缺失、类型无效或越界未绘制。
            </p>
          )}
          {nodes.length < data.nodes.length && <p>无效或越界节点未绘制。</p>}
        </>
      }
    >
      <div className="ain-topology-controls">
        <label>链路着色指标<select value={metric} onChange={event => setMetric(event.target.value as LinkMetric)}>
          {(Object.keys(LINK_METRICS) as LinkMetric[]).map(key => <option key={key} value={key}>{LINK_METRICS[key].label} / {LINK_METRICS[key].unit}</option>)}
        </select></label>
      </div>
      {projection ? (
        <svg
          className="ain-spatial-svg"
          viewBox={SCENE_VIEWBOX}
          role="group"
          aria-label="AP 与 UE 服务连接拓扑图"
        >
          <SceneAxes area={data.area} projection={projection} />
          {links.map((link) => {
            const source = byId.get(link.source)!;
            const target = byId.get(link.target)!;
            const active = selectedLinkId ? link.id === selectedLinkId : focusedApId
              ? link.source === focusedApId
              : link.target === ueId;
            const midX = (projection.x(source.x) + projection.x(target.x)) / 2;
            const midY = (projection.y(source.y) + projection.y(target.y)) / 2;
            return (
              <g key={link.id} className={`ain-topology-link-button${active ? " is-active" : ""}`}
                role="button" tabIndex={0} aria-label={`查看链路 ${source.id} → ${target.id}`}
                aria-pressed={link.id === selectedLinkId}
                onClick={() => onSelectLink(link)} onKeyDown={event => {
                  if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onSelectLink(link); }
                }}>
                <title>{`${source.id} → ${target.id}：RSRP ${displayMetric(link.rsrp)} dBm / SINR ${displayMetric(link.sinr)} dB / 吞吐量 ${displayMetric(link.throughput)} Mbps（窗口连接均值）`}</title>
                {active && <line className="ain-topology-link-halo" x1={projection.x(source.x)} y1={projection.y(source.y)} x2={projection.x(target.x)} y2={projection.y(target.y)} />}
                <line
                  className={`ain-topology-link${active ? " is-active" : ""}`}
                  style={{ stroke: linkMetricColor(link[metric], range) }}
                  x1={projection.x(source.x)}
                  y1={projection.y(source.y)}
                  x2={projection.x(target.x)}
                  y2={projection.y(target.y)}
                >
                  <title>{`${meta.label} ${displayMetric(link[metric])} ${meta.unit}`}</title>
                </line>
                <line className="ain-topology-link-hit" x1={projection.x(source.x)} y1={projection.y(source.y)} x2={projection.x(target.x)} y2={projection.y(target.y)} />
                <text
                  className="ain-topology-weight"
                  x={midX}
                  y={midY - 3}
                  textAnchor="middle"
                >
                  {displayMetric(link[metric])}
                </text>
              </g>
            );
          })}
          {nodes.map((node) => (
            <SceneNode
              key={node.id}
              node={node}
              projection={projection}
              selected={
                node.type === "ue" ? node.id === ueId : node.id === focusedApId
              }
              highlighted={highlighted.has(node.id)}
              onSelect={selectNode}
            />
          ))}
          {!nodes.length && (
            <text
              x={256}
              y={180}
              textAnchor="middle"
              fill="#b5c7d5"
              fontSize={15}
            >
              暂无网络节点
            </text>
          )}
        </svg>
      ) : (
        <p className="ain-spatial-empty">场景尺寸无效，无法绘制拓扑图。</p>
      )}
    </SpatialChart>
  );
}
