import { parseGoal, parseSubject } from './userExperience';
import { METRIC_META } from '../types/backend';
import type { MetricKey, QueryScope } from '../types/backend';
import type { ParameterValue, PlanParameter, SimulationResult, TreatmentPlan } from '../types/plan';
const object = (v: unknown): Record<string, unknown> => v && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, unknown> : {};
const list = (v: unknown): unknown[] => Array.isArray(v) ? v : [];
const string = (v: unknown): string => typeof v === 'string' ? v : '';
const strings = (v: unknown) => list(v).filter((s): s is string => typeof s === 'string' && Boolean(s.trim()));
const number = (v: unknown): number | null => typeof v === 'number' && Number.isFinite(v) ? v : null;
const value = (v: unknown): ParameterValue | null => typeof v === 'string' && v.trim() ? v : number(v);
const metric = (v: unknown): v is MetricKey => typeof v === 'string' && Object.hasOwn(METRIC_META, v);
const matches = (a: QueryScope, b: QueryScope) => a.scenario === b.scenario && a.start === b.start && a.end === b.end;
function scope(v: unknown, fallback: QueryScope): QueryScope {
  if (v === undefined) return fallback;
  const s = object(v); return { scenario: string(s.scenario), start: string(s.start), end: string(s.end) };
}
function parameter(v: unknown): PlanParameter | null {
  const p = object(v); if (!string(p.id) || !string(p.objectId) || !string(p.key) || !string(p.label) || !['number', 'enum'].includes(string(p.type))) return null;
  return { id: string(p.id), objectId: string(p.objectId), key: string(p.key), label: string(p.label), description: string(p.description),
    type: p.type as PlanParameter['type'], unit: string(p.unit), currentValue: value(p.currentValue), suggestedValue: value(p.suggestedValue),
    editable: p.editable === true, unavailableReason: string(p.unavailableReason), min: number(p.min), max: number(p.max), step: number(p.step),
    options: list(p.options).map(v => { const o = object(v); return { value: string(o.value), label: string(o.label) }; }).filter(o => o.value && o.label) };
}
function simulation(v: unknown, expected: QueryScope): SimulationResult | null {
  if (!v) return null;
  const s = object(v), ownScope = scope(s.scope, expected);
  if (!string(s.id) || !matches(ownScope, expected) || !['completed', 'unsupported', 'failed'].includes(string(s.status))) return null;
  const params: Record<string, ParameterValue> = {};
  for (const [key, v] of Object.entries(object(s.parameterValues))) { const n = value(v); if (n !== null) params[key] = n; }
  const t = object(s.trend), c = object(s.coverage), a = object(c.area), cell = object(c.cellSize);
  const areaNumbers = [number(a.xMin), number(a.xMax), number(a.yMin), number(a.yMax)];
  const coverageValid = areaNumbers.every(v => v !== null) && Number(a.xMax) > Number(a.xMin) && Number(a.yMax) > Number(a.yMin)
    && number(cell.x) !== null && Number(cell.x) > 0 && number(cell.y) !== null && Number(cell.y) > 0;
  return { id: string(s.id), status: s.status as SimulationResult['status'], subject: parseSubject(s.subject), goalAssessment: parseGoal(s.goalAssessment), scope: ownScope, method: string(s.method), summary: string(s.summary),
    assumptions: strings(s.assumptions), limitations: strings(s.limitations), parameterValues: params,
    metrics: list(s.metrics).flatMap(v => { const m = object(v); return metric(m.key) ? [{ key: m.key, objectId: string(m.objectId), before: number(m.before), after: number(m.after) }] : []; }),
    trend: metric(t.metric) ? { metric: t.metric, objectId: string(t.objectId), points: list(t.points).flatMap(v => {
      const p = object(v), time = string(p.time); return /(?:Z|[+-]\d{2}:\d{2})$/.test(time) && Number.isFinite(Date.parse(time)) ? [{ time, before: number(p.before), after: number(p.after) }] : [];
    }) } : null,
    coverage: coverageValid ? { area: { xMin: Number(a.xMin), xMax: Number(a.xMax), yMin: Number(a.yMin), yMax: Number(a.yMax) },
      cellSize: { x: Number(cell.x), y: Number(cell.y) }, points: list(c.points).flatMap(v => { const p = object(v); return number(p.x) !== null && number(p.y) !== null
        ? [{ x: Number(p.x), y: Number(p.y), before: number(p.before), after: number(p.after) }] : []; }) } : null };
}
// 可选扩展：旧后端没有 plans 时返回空列表；扩展内容无效不阻断原有研判。
export function parsePlans(v: unknown, expected: QueryScope): TreatmentPlan[] {
  const ids = new Set<string>();
  return list(v).flatMap(v => {
    const p = object(v), ownScope = scope(p.scope, expected), id = string(p.id);
    if (!id || ids.has(id) || !string(p.name) || !matches(ownScope, expected)) return [];
    const parameters = list(p.parameters).map(parameter).filter((p): p is PlanParameter => p !== null);
    if (parameters.length !== list(p.parameters).length || new Set(parameters.map(p => p.id)).size !== parameters.length) return [];
    ids.add(id);
    return [{ id, subject: parseSubject(p.subject), goal: parseGoal(p.goal), name: string(p.name), description: string(p.description), scope: ownScope, objectIds: strings(p.objectIds), goals: list(p.goals).filter(metric),
      conditions: strings(p.conditions), tradeoffs: strings(p.tradeoffs), tasks: strings(p.tasks), parameters, simulation: simulation(p.simulation, ownScope) }];
  });
}

/** 解析"改参数后重算"返回的单个仿真结果（供 /api/plan/simulate 复用同一套校验）。 */
export function parseSimulation(v: unknown, expected: QueryScope): SimulationResult | null {
  return simulation(v, expected);
}
