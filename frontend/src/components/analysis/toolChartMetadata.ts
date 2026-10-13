import { METRIC_META } from '../../types/backend';
import type { MetricKey } from '../../types/backend';

export function toolLineMetadata(option: Record<string, unknown>) {
  const series = Array.isArray(option.series) ? option.series : [];
  const names = series.map(item => {
    const value = item && typeof item === 'object' ? (item as { name?: unknown }).name : null;
    return typeof value === 'string' && value.trim() ? value.trim() : '未说明指标';
  });
  const matchers: [RegExp, MetricKey][] = [
    [/rsrp/i, 'rsrpDbm'], [/sinr/i, 'sinrDb'], [/throughput|吞吐/i, 'throughputMbps'],
    [/latency|时延/i, 'latencyMs'], [/packet.?loss|丢包/i, 'packetLossPct'],
    [/prb|utilization|占用|利用率/i, 'resourceUtilizationPct'],
  ];
  const units = names.map(name => {
    const entry = matchers.find(([pattern]) => pattern.test(name));
    return entry ? METRIC_META[entry[1]].unit : '';
  });
  return { label: [...new Set(names)].join('、') || '未说明指标',
    unit: units.length && units.every(unit => unit === units[0]) ? units[0] : '',
    hasMixedUnits: new Set(units.filter(Boolean)).size > 1 };
}
