import { METRIC_META } from '../types/backend';
import type { MetricKey, MetricRecord, NetworkLink } from '../types/backend';
import { getLatestFrame } from './metrics';

export type ObservationTarget = { kind: 'all' } | { kind: 'ap' | 'ue'; id: string };
export type ObservationPeriod = 'latest' | 'window';
export interface ObservationPoint {
  time: number; endTime: number; value: number | null; count: number;
  min: number | null; max: number | null;
}
export const targetLabel = (target: ObservationTarget) => target.kind === 'all' ? '全网' : target.id;
export const matchesTarget = (record: MetricRecord, target: ObservationTarget) =>
  target.kind === 'all' || (target.kind === 'ap' ? record.apId === target.id : record.ueId === target.id);

// 同 UE、同采样时刻只保留最后一条，避免重复记录改变均值权重。
export function uniqueObservationRecords(records: MetricRecord[]) {
  const unique = new Map<string, MetricRecord>();
  for (const record of records) {
    const time = Date.parse(record.timestamp);
    if (Number.isFinite(time)) unique.set(`${record.ueId}|${time}`, record);
  }
  return [...unique.values()].sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp));
}
export function observationMeans(records: MetricRecord[]) {
  return Object.fromEntries((Object.keys(METRIC_META) as MetricKey[]).map(key => {
    let sum = 0, count = 0;
    for (const record of records) if (Number.isFinite(record[key])) { sum += record[key]; count++; }
    return [key, count ? sum / count : null];
  })) as Record<MetricKey, number | null>;
}
export function observationSnapshot(records: MetricRecord[], target: ObservationTarget, period: ObservationPeriod) {
  const unique = uniqueObservationRecords(records);
  const frame = getLatestFrame(unique);
  const history = unique.filter(record => matchesTarget(record, target));
  const selected = (period === 'latest' ? frame.metrics : unique).filter(record => matchesTarget(record, target));
  return {
    records: selected, history, means: observationMeans(selected),
    frameTime: frame.metrics[0]?.backendTime ?? null,
    lastSeen: history.at(-1)?.backendTime ?? null,
    userIds: [...new Set(selected.map(record => record.ueId))],
    apIds: [...new Set(selected.map(record => record.apId))],
  };
}
// AP 曲线按每个时刻的实际服务关系汇总，而非按窗口主服务 AP 分组。
export function observationTrend(records: MetricRecord[], target: ObservationTarget, metric: MetricKey): ObservationPoint[] {
  const frames = new Map<number, MetricRecord[]>();
  for (const record of uniqueObservationRecords(records)) {
    const time = Date.parse(record.timestamp);
    const frame = frames.get(time) ?? []; frame.push(record); frames.set(time, frame);
  }
  return [...frames].map(([time, frame]) => {
    const values = frame.filter(record => matchesTarget(record, target)).map(record => record[metric]).filter(Number.isFinite);
    return { time, endTime: time, value: values.length ? values.reduce((a, b) => a + b, 0) / values.length : null,
      count: values.length, min: values.length ? Math.min(...values) : null, max: values.length ? Math.max(...values) : null };
  });
}
// 长窗按等时长区间压缩，同刻用户均值按时刻等权，保留区间极值和有效记录数。
export function binObservationTrend(points: ObservationPoint[], limit = 600): ObservationPoint[] {
  if (points.length <= limit) return points;
  const first = points[0].time, last = points.at(-1)!.time;
  const width = Math.max(1, Math.ceil((last - first + 1) / limit));
  const bins = new Map<number, ObservationPoint[]>();
  for (const point of points) {
    const index = Math.floor((point.time - first) / width);
    const bin = bins.get(index) ?? []; bin.push(point); bins.set(index, bin);
  }
  const result: ObservationPoint[] = [];
  for (let index = 0; index <= Math.floor((last - first) / width); index++) {
    const bin = bins.get(index) ?? [];
    const valid = bin.filter(point => point.value !== null);
    result.push({ time: first + index * width, endTime: Math.min(last, first + (index + 1) * width - 1),
      value: valid.length ? valid.reduce((sum, point) => sum + point.value!, 0) / valid.length : null,
      count: valid.reduce((sum, point) => sum + point.count, 0),
      min: valid.length ? Math.min(...valid.map(point => point.min!)) : null,
      max: valid.length ? Math.max(...valid.map(point => point.max!)) : null });
  }
  return result;
}
export function latestObservationLinks(records: MetricRecord[]): NetworkLink[] {
  return getLatestFrame(uniqueObservationRecords(records)).metrics.map(record => ({
    id: `${record.apId}->${record.ueId}`, source: record.apId, target: record.ueId,
    rsrp: record.rsrpDbm, sinr: record.sinrDb, throughput: record.throughputMbps,
  }));
}
export function isRelatedLink(link: NetworkLink, target: ObservationTarget) {
  return target.kind === 'all' || (target.kind === 'ap' ? link.source === target.id : link.target === target.id);
}
