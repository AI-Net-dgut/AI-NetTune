import { useState } from 'react';
import type { DashboardData, NetworkLink, SpatialMapData } from '../../types/backend';
import type { ObservationPeriod, ObservationTarget } from '../../services/dataObservation';
import { latestObservationLinks, targetLabel } from '../../services/dataObservation';
import { nextComparisonScope } from '../../services/ueComparison';
import NetworkSpaceView from './NetworkSpaceView';
import ObservationPanel from './ObservationPanel';
import ObservationTrend from './ObservationTrend';
import UEComparisonCharts from './UEComparisonCharts';
import './DataObservation.css';

interface Props {
  data: DashboardData; spatial: SpatialMapData | null; pending: boolean; error: string;
  onRetry: () => void; onFocusedAP: (id: string | null) => void;
  spatialNotice?: string;
}
export default function DataObservation({ data, spatial, pending, error, onRetry, onFocusedAP, spatialNotice = '' }: Props) {
  const [target, setTarget] = useState<ObservationTarget>({ kind: 'all' });
  const [comparisonScope, setComparisonScope] = useState<ObservationTarget>({ kind: 'all' });
  const [period, setPeriod] = useState<ObservationPeriod>('latest');
  const [selectedLink, setSelectedLink] = useState<NetworkLink | null>(null);
  const aps = data.nodes.filter(node => node.type === 'ap');
  const ues = data.nodes.filter(node => node.type === 'ue');
  // 保留用户选择，但连接的数值来自当前数据，不把上一个播放帧的值留在详情里。
  const activeSelectedLink = selectedLink ? (selectedLink.primary ? data.links : latestObservationLinks(data.metrics))
    .find(link => link.id === selectedLink.id) ?? null : null;
  function select(next: ObservationTarget) {
    if (next.kind !== 'all' && !data.nodes.some(node => node.id === next.id && node.type === next.kind)) return;
    const nextScope = nextComparisonScope(data, comparisonScope, next, period);
    setComparisonScope(nextScope);
    setTarget(next); setSelectedLink(null); onFocusedAP(nextScope.kind === 'ap' ? nextScope.id : null);
  }
  function selectLink(link: NetworkLink) {
    if (!data.nodes.some(node => node.id === link.target && node.type === 'ue')) return;
    setComparisonScope(nextComparisonScope(data, comparisonScope, { kind: 'ue', id: link.target }, period));
    setTarget({ kind: 'ue', id: link.target }); setSelectedLink(link); onFocusedAP(link.source);
  }
  function selectComparisonUE(id: string) {
    if (!ues.some(node => node.id === id)) return;
    select({ kind: 'ue', id });
  }
  return <div className="ain-observation">
    <div className="ain-observation-object-controls" aria-label="观察对象">
      <div className="ain-observation-tabs">
        <button type="button" aria-pressed={target.kind === 'all'} onClick={() => select({ kind: 'all' })}>全网</button>
        <button type="button" aria-pressed={target.kind === 'ap'} disabled={!aps.length}
          onClick={() => { const first = aps[0]; if (first) select({ kind: 'ap', id: first.id }); }}>AP</button>
        <button type="button" aria-pressed={target.kind === 'ue'} disabled={!ues.length}
          onClick={() => { const first = ues[0]; if (first) select({ kind: 'ue', id: first.id }); }}>UE</button>
      </div>
      {target.kind !== 'all' && <label>观察对象<select aria-label="选择观察对象" value={target.id} onChange={event => select({ kind: target.kind, id: event.target.value })}>
        {!(target.kind === 'ap' ? aps : ues).some(node => node.id === target.id) && <option value={target.id}>{target.id}（当前无采样）</option>}
        {(target.kind === 'ap' ? aps : ues).map(node => <option key={node.id}>{node.id}</option>)}
      </select></label>}
      <span className="ain-observation-breadcrumb" aria-live="polite">{target.kind === 'all' ? '全网概览' : `全网 / ${targetLabel(target)}`}</span>
      <p>点击地图中的 AP、UE 或连接，可联动指标和趋势。</p>
    </div>
    <div className="ain-observation-layout">
      <NetworkSpaceView data={data} target={target} spatial={spatial} pending={pending} error={error} onRetry={onRetry}
        selectedLink={activeSelectedLink} onSelect={select} onSelectLink={selectLink} spatialNotice={spatialNotice} />
      <ObservationPanel data={data} target={target} period={period} onPeriod={setPeriod} onSelect={select} selectedLink={activeSelectedLink} />
      <ObservationTrend data={data} target={target} />
      <UEComparisonCharts data={data} comparisonScope={comparisonScope} target={target} period={period}
        onSelectUE={selectComparisonUE} />
    </div>
  </div>;
}
