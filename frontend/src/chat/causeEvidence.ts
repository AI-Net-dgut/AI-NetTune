import type { Candidate, ChatResult, MetricKey, MetricRecord } from '../types/backend';
import type { EvidenceSelection } from './DiagnosisCharts';
import type { UserTrendMetric } from './userTrend';

/** 入口只选择已有的数据视图，不生成新的原因结论。 */
export function causeEvidenceSelection(candidate: Candidate, objectId?: string): Omit<EvidenceSelection, 'id'> {
  const cause = candidate.cause;
  if (/连接|接入|切换|关联/.test(cause)) return { view: 'connections', objectId };
  if (/覆盖|天线|倾角|遮挡|功率/.test(cause)) return { view: 'coverage', objectId, metric: 'rsrpDbm' };
  if (/拥塞|负载|容量|资源|PRB/i.test(cause)) return { view: 'trend', objectId, metric: 'resourceUtilizationPct' };
  if (/干扰|SINR|信噪/i.test(cause)) return { view: 'trend', objectId, metric: 'sinrDb' };
  return { view: 'trend', objectId, metric: 'throughputMbps' };
}

/** 指定 AP 的用户分析，对照图也只比较该 AP 下的同窗采样。 */
export function evidenceComparisonRows(rows: MetricRecord[], subject?: ChatResult['subject']): MetricRecord[] {
  return subject?.apConstraint ? rows.filter(row => row.apId === subject.apConstraint) : rows;
}

export function userMetricForEvidence(metric?: MetricKey): UserTrendMetric {
  return metric === 'rsrpDbm' ? 'rsrp' : metric === 'sinrDb' ? 'sinr' : metric === 'latencyMs' ? 'latency' : 'throughput';
}
