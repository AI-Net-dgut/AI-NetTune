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
  method: string; summary: string; assumptions: string[]; limitations: string[];
  parameterValues: Record<string, ParameterValue>;
  metrics: { key: MetricKey; objectId: string; before: number | null; after: number | null }[];
  trend: { metric: MetricKey; objectId: string; points: { time: string; before: number | null; after: number | null }[] } | null;
  coverage: { area: { xMin: number; xMax: number; yMin: number; yMax: number }; cellSize: { x: number; y: number };
    points: { x: number; y: number; before: number | null; after: number | null }[] } | null;
}
export interface TreatmentPlan {
  id: string; name: string; description: string; scope: QueryScope; objectIds: string[];
  goals: MetricKey[]; conditions: string[]; tradeoffs: string[]; tasks: string[];
  parameters: PlanParameter[]; simulation: SimulationResult | null;
}
