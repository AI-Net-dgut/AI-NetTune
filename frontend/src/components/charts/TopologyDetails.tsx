import type { DashboardData, MetricKey, NetworkLink } from "../../types/backend";
import { METRIC_META } from "../../types/backend";
import { displayMetric } from "../../services/metrics";
import { getAPDetails, getLinkDetails, getUEDetails } from "../../services/topology";
import "./TopologyInteraction.css";

interface Props {
  data: DashboardData;
  ueId: string;
  focusedApId: string | null;
  selectedLinkId: string | null;
  onSelectUE: (id: string) => void;
  onFocusAP: (id: string) => void;
  onSelectLink: (link: NetworkLink) => void;
}
function value(number: number | null | undefined) { return displayMetric(number ?? undefined); }

function Connections({ links, onSelectLink }: { links: NetworkLink[]; onSelectLink: Props["onSelectLink"] }) {
  return links.length ? <div className="ain-topology-table-scroll"><table className="ain-topology-table">
    <caption>后端返回的窗口服务连接与均值指标</caption>
    <thead><tr><th>连接</th><th>RSRP / dBm</th><th>SINR / dB</th><th>吞吐量 / Mbps</th><th>详情</th></tr></thead>
    <tbody>{links.map(link => <tr key={link.id}>
      <td>{link.source} → {link.target}{link.primary === true && <small>主连接</small>}</td>
      <td>{value(link.rsrp)}</td><td>{value(link.sinr)}</td><td>{value(link.throughput)}</td>
      <td><button type="button" className="ain-button" onClick={() => onSelectLink(link)}>查看链路</button></td>
    </tr>)}</tbody>
  </table></div> : <p className="ain-topology-note">当前范围没有对应服务连接。</p>;
}
function Metrics({ metrics, title }: { metrics: Partial<Record<MetricKey, number | null>>; title: string }) {
  return <div className="ain-topology-metrics" aria-label={title}>
    {(Object.keys(METRIC_META) as MetricKey[]).map(key => <div key={key}>
      <span>{METRIC_META[key].label}</span><strong>{value(metrics[key])}<small>{METRIC_META[key].unit}</small></strong>
    </div>)}
  </div>;
}

export default function TopologyDetails({ data, ueId, focusedApId, selectedLinkId, onSelectUE, onFocusAP, onSelectLink }: Props) {
  const link = data.links.find(item => item.id === selectedLinkId);
  let content;
  let title;
  if (link) {
    const details = getLinkDetails(data, link);
    title = `链路详情 · ${link.source} → ${link.target}`;
    content = <>
      <div className="ain-topology-detail-summary">
        <span>连接类型：{link.primary === true ? "窗口主服务连接" : link.primary === false ? "非主服务连接" : "接口未标明主连接类型"}</span>
        <span>关联记录：{details.recordCount} / 该 UE 共 {details.ueRecordCount} 条</span>
        <span>记录占比：{details.recordShare === null ? "—" : `${(details.recordShare * 100).toFixed(1)}%`}</span>
      </div>
      <Connections links={[link]} onSelectLink={onSelectLink} />
      <p className="ain-topology-note">本连接首条采样：{details.firstTime ?? "暂无"}；末条采样：{details.lastTime ?? "暂无"}（北京时间）。记录占比不等于连接时长占比。</p>
      <div className="ain-topology-detail-actions">
        <button type="button" className="ain-button" onClick={() => onSelectUE(link.target)}>查看 {link.target} 指标</button>
        <button type="button" className="ain-button" onClick={() => onFocusAP(link.source)}>查看 {link.source} 详情</button>
      </div>
    </>;
  } else if (focusedApId) {
    const details = getAPDetails(data, focusedApId);
    title = `AP 详情 · ${focusedApId}`;
    content = details.node ? <>
      <div className="ain-topology-detail-summary">
        <span>位置：({details.node.x}, {details.node.y}) m</span>
        <span>窗口服务连接：{details.links.length} 条</span>
        <span>最新帧服务 UE：{details.latestServingUEs.length} 个</span>
        <span>关联采样：{details.recordCount} 条</span>
      </div>
      <p className="ain-topology-note">以下为连接到此 AP 的终端采样指标均值，按所选窗口内记录统计。</p>
      <Metrics metrics={details.means} title="AP关联终端的窗口采样均值" />
      <Connections links={details.links} onSelectLink={onSelectLink} />
      <div className="ain-topology-latest-ues">
        <span>最新帧（{details.latestTime ?? "暂无数据"}）：</span>
        {details.latestServingUEs.length ? details.latestServingUEs.map(record =>
          <button type="button" key={record.ueId} className="ain-button" onClick={() => onSelectUE(record.ueId)}>{record.ueId}</button>)
          : <span>此 AP 在最新帧没有终端采样记录。</span>}
      </div>
    </> : <p className="ain-topology-note">当前范围找不到此 AP。</p>;
  } else {
    const details = getUEDetails(data, ueId);
    title = `UE 详情 · ${ueId || "未选择"}`;
    content = details.node ? <>
      <div className="ain-topology-detail-summary">
        <span>最新位置：({details.node.x}, {details.node.y}) m</span>
        <span>最新采样服务 AP：{details.latest?.apId ?? "暂无"}</span>
        <span>窗口主服务 AP：{details.links.filter(item => item.primary !== false).map(item => item.source).join("、") || "接口未提供"}</span>
        <span>窗口记录：{details.recordCount} 条</span>
      </div>
      <p className="ain-topology-note">以下为该 UE 的最新采样指标：{details.latest?.backendTime ?? "暂无数据"}（北京时间）。</p>
      <Metrics metrics={details.latest ?? {}} title="UE最新采样指标" />
      {details.latest?.status && <p className="ain-topology-note">数据状态字段：{details.latest.status}</p>}
      <Connections links={details.links} onSelectLink={onSelectLink} />
    </> : <p className="ain-topology-note">点击拓扑中的 AP、UE 或链路查看详情。</p>;
  }
  return <section className="ain-chart-slot ain-topology-details" aria-label="拓扑对象详情">
    <div className="ain-slot-heading"><h2>{title}</h2><p>范围：{data.scope.scenario} · {data.scope.start} — {data.scope.end}</p></div>
    <div className="ain-topology-detail-body">{content}</div>
  </section>;
}
