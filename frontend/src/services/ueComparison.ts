import type { DashboardData, MetricKey } from '../types/backend';
import type { ObservationPeriod, ObservationTarget } from './dataObservation';
import { observationMeans, observationSnapshot } from './dataObservation';

export interface UEComparisonItem {
  ueId: string; recordCount: number; means: Record<MetricKey, number | null>;
}
// 全网/UE视角保留比较背景；AP视角仅比较统计范围内真正由该AP服务的UE。
export function ueComparison(data: DashboardData, scope: ObservationTarget, period: ObservationPeriod): UEComparisonItem[] {
  const filtered = observationSnapshot(data.metrics, scope.kind === 'ap' ? scope : { kind: 'all' }, period).records;
  const ids = scope.kind === 'ap' ? [...new Set(filtered.map(record => record.ueId))]
    : [...new Set(data.nodes.filter(node => node.type === 'ue').map(node => node.id).concat(filtered.map(record => record.ueId)))];
  return ids.sort((a, b) => a.localeCompare(b, 'en', { numeric: true })).map(ueId => {
    const records = filtered.filter(record => record.ueId === ueId);
    return { ueId, recordCount: records.length, means: observationMeans(records) };
  });
}

export function nextComparisonScope(data: DashboardData, current: ObservationTarget, next: ObservationTarget, period: ObservationPeriod): ObservationTarget {
  if (next.kind === 'all' || next.kind === 'ap') return next;
  if (current.kind === 'ap' && ueComparison(data, current, period).some(item => item.ueId === next.id)) return current;
  return { kind: 'all' };
}
