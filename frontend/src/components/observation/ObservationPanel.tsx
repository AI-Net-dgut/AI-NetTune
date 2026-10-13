import { useMemo } from 'react';
import { METRIC_META } from '../../types/backend';
import type { DashboardData, MetricKey, NetworkLink } from '../../types/backend';
import type { ObservationPeriod, ObservationTarget } from '../../services/dataObservation';
import { observationSnapshot, observationMeans, targetLabel, uniqueObservationRecords } from '../../services/dataObservation';
import { displayMetric } from '../../services/metrics';

const CORE: MetricKey[] = ['rsrpDbm', 'sinrDb', 'throughputMbps', 'latencyMs'];
const EXTRA: MetricKey[] = ['packetLossPct', 'retransmissions', 'demandMbps', 'resourceUtilizationPct'];
interface Props {
  data: DashboardData; target: ObservationTarget; period: ObservationPeriod;
  onPeriod: (period: ObservationPeriod) => void; onSelect: (target: ObservationTarget) => void;
  selectedLink: NetworkLink | null;
}
export default function ObservationPanel({ data, target, period, onPeriod, onSelect, selectedLink }: Props) {
  const snapshot = useMemo(() => observationSnapshot(data.metrics, target, period), [data.metrics, target, period]);
  const unique = useMemo(() => uniqueObservationRecords(data.metrics), [data.metrics]);
  const selectedNode = target.kind === 'all' ? null : data.nodes.find(node => node.id === target.id);
  const currentUser = snapshot.records.at(-1);
  const latestUE = target.kind === 'ue' ? observationSnapshot(data.metrics, target, 'latest').records[0] : null;
  const windowLinks = useMemo(() => {
    const groups = new Map<string, { source: string; target: string; count: number }>();
    for (const record of snapshot.history) {
      const key = `${record.apId}->${record.ueId}`;
      const group = groups.get(key) ?? { source: record.apId, target: record.ueId, count: 0 };
      group.count++; groups.set(key, group);
    }
    return [...groups.values()];
  }, [snapshot.history]);
  const linkRows = selectedLink ? unique.filter(record => record.apId === selectedLink.source && record.ueId === selectedLink.target) : [];
  const linkMeans = observationMeans(linkRows);
  const cards = target.kind === 'all' ? [
    { label: period === 'latest' ? '最新帧采样 UE' : '窗口采样 UE', value: snapshot.userIds.length, unit: '个' },
    { label: period === 'latest' ? '最新帧服务 AP' : '窗口服务 AP', value: snapshot.apIds.length, unit: '个' },
    { label: '用户平均吞吐量', value: snapshot.means.throughputMbps, unit: 'Mbps' },
    { label: '用户平均时延', value: snapshot.means.latencyMs, unit: 'ms' },
  ] : CORE.map(key => ({ label: `${target.kind === 'ap' ? '服务用户' : ''}${METRIC_META[key].label}`, value: snapshot.means[key], unit: METRIC_META[key].unit }));
  const relatedIds = target.kind === 'ap' ? [...new Set(snapshot.history.map(record => record.ueId))]
    : target.kind === 'ue' ? [...new Set(snapshot.history.map(record => record.apId))] : [];
  const rsrp = snapshot.records.map(record => record.rsrpDbm).filter(Number.isFinite);
  return <aside className="ain-observation-panel" aria-labelledby="ain-observation-panel-title">
    <header><span className="ain-observation-eyebrow">网络概况</span><h2 id="ain-observation-panel-title">{targetLabel(target)} · {target.kind === 'all' ? '网络概览' : target.kind === 'ap' ? '服务情况' : '用户体验'}</h2>
      <div className="ain-observation-tabs" aria-label="指标统计口径">
        <button type="button" aria-pressed={period === 'latest'} onClick={() => onPeriod('latest')}>最新帧</button>
        <button type="button" aria-pressed={period === 'window'} onClick={() => onPeriod('window')}>窗口均值</button>
      </div>
      <p className="ain-observation-note">{period === 'latest' ? `最新帧：${snapshot.frameTime || '无'}（北京时间）` : `窗口：${data.scope.start} — ${data.scope.end}（北京时间）`}</p>

    </header>
    <div className="ain-observation-kpis">{cards.map(card => <div className="ain-observation-kpi" key={card.label}><span>{card.label}</span>
      <strong>{displayMetric(card.value ?? undefined)}<small>{card.unit}</small></strong></div>)}</div>
    {!snapshot.records.length && <p className="ain-observation-empty" role="status">{period === 'latest' ? '此对象在最新帧无有效服务采样。' : '此对象在窗口内暂无采样。'}
      {snapshot.lastSeen && ` 窗口内最后采样：${snapshot.lastSeen}。`}</p>}
    <div className="ain-observation-context">
      {target.kind === 'all' ? <>
        <p>已加载：{data.nodes.filter(node => node.type === 'ap').length} 个 AP / {data.nodes.filter(node => node.type === 'ue').length} 个 UE</p>
        <p>采样 RSRP 范围：{rsrp.length ? `${displayMetric(Math.min(...rsrp))}～${displayMetric(Math.max(...rsrp))} dBm` : '暂无有效数据'}</p>
      </> : <>
        {selectedNode && <p>最新位置：({displayMetric(selectedNode.x)}, {displayMetric(selectedNode.y)}) m</p>}
        {target.kind === 'ap' ? <p>{period === 'latest' ? '最新帧实际服务 UE' : '窗口出现过的服务 UE'}：{snapshot.userIds.length} 个</p>
          : <p>最新帧服务 AP：<strong>{latestUE?.apId || '本帧无采样'}</strong>{period === 'window' && currentUser && `；窗口涉及 ${snapshot.apIds.length} 个 AP`}</p>}
        <p>{target.kind === 'ap' ? '窗口服务过的用户' : '窗口出现过的服务 AP'}：</p>
        <div className="ain-observation-related">{relatedIds.length ? relatedIds.map(id => <button className="ain-button" type="button" key={id}
          onClick={() => onSelect({ kind: target.kind === 'ap' ? 'ue' : 'ap', id })}>{id}</button>) : <span>无</span>}</div>
      </>}
    </div>
    {selectedLink && <div className="ain-observation-connection-detail" aria-live="polite"><h3>连接详情 · {selectedLink.source} → {selectedLink.target}</h3>
      <p>窗口关联记录：{linkRows.length} 条。以下为该连接的窗口采样均值。</p>
      <p>RSRP {displayMetric(linkMeans.rsrpDbm ?? undefined)} dBm · SINR {displayMetric(linkMeans.sinrDb ?? undefined)} dB</p>
      <p>吞吐量 {displayMetric(linkMeans.throughputMbps ?? undefined)} Mbps</p>
      <button className="ain-button" type="button" onClick={() => onSelect({ kind: 'ap', id: selectedLink.source })}>查看 {selectedLink.source}</button>
    </div>}
    <details className="ain-observation-details"><summary>更多指标与历史连接</summary>
      <div className="ain-observation-detail-body" role="region" aria-label="更多指标与历史连接" tabIndex={0}>
      <p className="ain-observation-note">统计使用 {snapshot.records.length} 条去重记录；缺失指标不计入均值。</p>
      <p className="ain-observation-note">{target.kind === 'all' ? '全网均值用于性能概览，空间分布请看主图。' : target.kind === 'ap' ? '仅统计各采样时刻由该 AP 实际服务的用户记录。' : period === 'latest' ? '仅显示全网最新采样帧中该 UE 的记录。' : '该 UE 在所选窗口内的有效采样均值。'}</p>
      <dl className="ain-observation-extra">{EXTRA.map(key => <div key={key}><dt>{key === 'resourceUtilizationPct' && target.kind !== 'ue' ? 'UE 记录 PRB 均值' : METRIC_META[key].label}</dt>
        <dd>{displayMetric(snapshot.means[key] ?? undefined)} {METRIC_META[key].unit}</dd></div>)}</dl>
      <p className="ain-observation-note">PRB 按 UE 记录口径展示，未认定为 AP 总负载。窗口均值按有效采样记录等权计算。</p>
      <h3>窗口历史连接</h3><p className="ain-observation-note">整个查询窗口出现过的服务关系，非同一时刻拓扑。</p>
      <div className="ain-observation-table-wrap"><table><thead><tr><th>AP</th><th>UE</th><th>关联记录</th></tr></thead><tbody>
        {windowLinks.map(link => <tr key={`${link.source}->${link.target}`}><td><button type="button" onClick={() => onSelect({ kind: 'ap', id: link.source })}>{link.source}</button></td>
          <td><button type="button" onClick={() => onSelect({ kind: 'ue', id: link.target })}>{link.target}</button></td><td>{link.count}</td></tr>)}
      </tbody></table></div>
      </div>
    </details>
  </aside>;
}
