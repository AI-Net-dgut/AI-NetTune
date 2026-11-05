import type { MetricKey, QueryScope } from './backend';
export type ParameterValue = string | number;
export interface PlanParameter {
  id: string; objectId: string; key: string; label: string; description: string;
  type: 'number' | 'enum'; unit: string; currentValue: ParameterValue | null; suggestedValue: ParameterValue | null;
  editable: boolean; unavailableReason: string; min: number | null; max: number | null; step: number | null;
  options: { value: string; label: string }[];
}
export interface SimulationResult {
  id: string; status: 'completed' | 'unsupported' | 'failed'; scope: QueryScope;
  subject?: PlanSubject; goalAssessment?: GoalAssessment;
  method: string; summary: string; assumptions: string[]; limitations: string[];
  parameterValues: Record<string, ParameterValue>;
  metrics: { key: MetricKey; objectId: string; before: number | null; after: number | null }[];
  trend: { metric: MetricKey; objectId: string; points: { time: string; before: number | null; after: number | null }[] } | null;
  coverage: { area: { xMin: number; xMax: number; yMin: number; yMax: number }; cellSize: { x: number; y: number };
    points: { x: number; y: number; before: number | null; after: number | null }[] } | null;
}
export interface TreatmentPlan {
  subject?: PlanSubject; goal?: GoalAssessment;
  id: string; name: string; description: string; scope: QueryScope; objectIds: string[];
  goals: MetricKey[]; conditions: string[]; tradeoffs: string[]; tasks: string[];
  parameters: PlanParameter[]; simulation: SimulationResult | null;
}

/** 用户级经验判定与提升目标；与后端的结构化状态保持一致。 */
export interface UserAssessment {
  dataStatus: 'ready' | 'insufficient_data';
  windowAssessment: 'within_policy' | 'exceeded' | 'insufficient_data';
  endState: 'persistent_anomaly' | 'warning' | 'recovered' | 'within_policy' | 'unknown';
  policy: { version: string; source: string; badShareLimit: number; persistentMinutes: number };
  checks: { metric: string; label: string; share: number | null; validSamples: number; totalSamples: number;
    value: number | null; threshold: number; ok: boolean | null }[];
  anomalySpans: { metric: string; start: string; end: string; minutes: number }[];
}
export interface GoalAssessment {
  status: 'pending' | 'not_evaluable' | 'estimated_met' | 'estimated_unmet';
  baseline: number | null; percent: number | null; target: number | null; predicted: number | null;
  gap: number | null; improvementPct: number | null;
}
export interface PlanSubject {
  type: 'user' | 'ap'; id: string; primaryAp?: string; apConstraint?: string;
  coverageScope?: string; trendScope?: string; note?: string;
}
