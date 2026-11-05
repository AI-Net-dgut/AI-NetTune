import type { GoalAssessment, PlanSubject, UserAssessment } from '../types/plan';
const object = (v: unknown): Record<string, unknown> => v && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, unknown> : {};
const text = (v: unknown) => typeof v === 'string' ? v : '';
const num = (v: unknown): number | null => typeof v === 'number' && Number.isFinite(v) ? v : null;
/** 对象范围不能在解析时被丢弃，重评估与导出复用同一字段。 */
export function parseSubject(v: unknown): PlanSubject | undefined {
  const s = object(v); if (!['user', 'ap'].includes(text(s.type)) || !text(s.id)) return undefined;
  return { type: s.type as PlanSubject['type'], id: text(s.id), primaryAp: text(s.primaryAp) || undefined,
    apConstraint: text(s.apConstraint) || undefined, coverageScope: text(s.coverageScope) || undefined,
    trendScope: text(s.trendScope) || undefined, note: text(s.note) || undefined };
}
export function parseGoal(v: unknown): GoalAssessment | undefined {
  const s = object(v); if (!['pending', 'not_evaluable', 'estimated_met', 'estimated_unmet'].includes(text(s.status))) return undefined;
  return { status: s.status as GoalAssessment['status'], baseline: num(s.baseline), percent: num(s.percent),
    target: num(s.target), predicted: num(s.predicted), gap: num(s.gap), improvementPct: num(s.improvementPct) };
}
export function parseAssessment(v: unknown): UserAssessment | undefined {
  const s = object(v), p = object(s.policy);
  if (!['ready', 'insufficient_data'].includes(text(s.dataStatus))
    || !['within_policy', 'exceeded', 'insufficient_data'].includes(text(s.windowAssessment))
    || !['persistent_anomaly', 'warning', 'recovered', 'within_policy', 'unknown'].includes(text(s.endState))) return undefined;
  return { dataStatus: s.dataStatus as UserAssessment['dataStatus'], windowAssessment: s.windowAssessment as UserAssessment['windowAssessment'],
    endState: s.endState as UserAssessment['endState'], policy: { version: text(p.version), source: text(p.source),
      badShareLimit: num(p.badShareLimit) ?? .1, persistentMinutes: num(p.persistentMinutes) ?? 5 },
    checks: (Array.isArray(s.checks) ? s.checks : []).map(v => { const c = object(v); return {
      metric: text(c.metric), label: text(c.label), share: num(c.share), validSamples: num(c.validSamples) ?? 0,
      totalSamples: num(c.totalSamples) ?? 0, value: num(c.value), threshold: num(c.threshold) ?? 0,
      ok: typeof c.ok === 'boolean' ? c.ok : null }; }),
    anomalySpans: (Array.isArray(s.anomalySpans) ? s.anomalySpans : []).map(v => { const a = object(v); return {
      metric: text(a.metric), start: text(a.start), end: text(a.end), minutes: num(a.minutes) ?? 0 }; }) };
}
export const windowAssessmentLabels = { within_policy: '整窗越阈占比未超过暂定门槛', exceeded: '整窗体验未达暂定门槛', insufficient_data: '有效数据不足，无法判断整窗体验' };
export const endStateLabels = { persistent_anomaly: '截至分析时刻存在连续异常', warning: '末端短时越阈，需继续观察', recovered: '窗口内出现连续异常，末端已恢复', within_policy: '截至分析时刻未见越阈', unknown: '末端数据不足或过旧，状态未知' };
export const goalLabels = { pending: '待方案评估', not_evaluable: '基线无效或为零，无法评估相对提升', estimated_met: '预计达到目标', estimated_unmet: '预计未达到目标' };
