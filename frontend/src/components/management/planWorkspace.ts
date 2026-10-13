import type { Candidate, Diagnosis, QueryScope } from '../../types/backend';
import type { PlanParameter, TreatmentPlan } from '../../types/plan';
import { sameQueryScope } from '../../chat/diagnosisReply';
export function initialParameterValues(plan: TreatmentPlan): Record<string, string> {
  return Object.fromEntries(plan.parameters.map(p => [p.id, p.suggestedValue === null ? '' : String(p.suggestedValue)]));
}
export function validateParameter(p: PlanParameter, text: string): string {
  if (!p.editable && text !== String(p.suggestedValue ?? '')) return '此参数不允许编辑';
  if (!text.trim()) return '请填写建议值';
  if (p.type === 'enum') return p.options.some(o => o.value === text) ? '' : '请选择有效选项';
  const n = Number(text); if (!Number.isFinite(n)) return '请输入有效数字';
  if ((p.min !== null && n < p.min) || (p.max !== null && n > p.max)) return `超出允许范围 ${p.min ?? '不限'}～${p.max ?? '不限'} ${p.unit}`;
  if (p.step !== null && p.step > 0) { const steps = (n - (p.min ?? 0)) / p.step; if (Math.abs(steps - Math.round(steps)) > 1e-7) return `请按步长 ${p.step} 调整`; }
  return '';
}
export function isSimulationCurrent(plan: TreatmentPlan, values: Record<string, string>, scope: QueryScope): boolean {
  const s = plan.simulation;
  return Boolean(s && s.status === 'completed' && sameQueryScope(plan.scope, scope) && sameQueryScope(s.scope, scope)
    && plan.parameters.every(p => !validateParameter(p, values[p.id] ?? '') && Object.hasOwn(s.parameterValues, p.id)
      && (p.type === 'number' ? Number(values[p.id]) === Number(s.parameterValues[p.id]) : values[p.id] === String(s.parameterValues[p.id]))));
}
export function candidateGroups(d: Diagnosis): { id: string; candidates: Candidate[] }[] {
  const merge = (items: Candidate[]) => {
    const map = new Map<string, Candidate>();
    for (const item of items) {
      const key = item.cause.trim(); const previous = map.get(key);
      map.set(key, previous ? { ...previous, support: [...new Set([...previous.support, ...item.support])], against: [...new Set([...previous.against, ...item.against])], checks: [...new Set([...previous.checks, ...item.checks])] } : { ...item });
    }
    return [...map.values()];
  };
  const ids = [...new Set(d.affectedAps)];
  if (ids.length === 1) return [{ id: ids[0], candidates: merge([...d.candidates, ...d.apCandidates.filter(g => g.id === ids[0]).flatMap(g => g.candidates)]) }];
  return [{ id: '主要异常', candidates: merge(d.candidates) }, ...d.apCandidates.map(g => ({ id: g.id, candidates: merge(g.candidates) }))].filter(g => g.candidates.length);
}
export function fallbackPlan(d: Diagnosis): TreatmentPlan | null {
  if (d.state !== 'anomaly' || !d.suggestion.trim()) return null;
  return { id: 'legacy-suggestion', name: '研判处理建议', description: d.suggestion, scope: d.scope, objectIds: d.affectedAps,
    goals: [], conditions: [], tradeoffs: [], tasks: [], parameters: [], simulation: null };
}
