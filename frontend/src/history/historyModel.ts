import type { HistoryRecord } from './store';

export const object = (value: unknown): Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
export const array = (value: unknown): unknown[] => Array.isArray(value) ? value : [];
export const text = (value: unknown): string => typeof value === 'string' ? value : typeof value === 'number' && Number.isFinite(value) ? String(value) : '';
export interface QuestionEntry { record: HistoryRecord; members: HistoryRecord[] }
export function questionEntries(records: HistoryRecord[]): QuestionEntry[] {
  const items = records.filter(r => r && typeof r.id === 'string');
  const byId = new Map(items.map(r => [r.id, r]));
  const isQuestion = (r: HistoryRecord) => r.kind === 'forecast-analysis' || (r.kind === 'analysis' && !(r.parentId && object(object(r.payload).response).chart && !object(object(r.payload).response).diagnosis));
  function owner(r: HistoryRecord): HistoryRecord | undefined {
    const seen = new Set([r.id]); let current = r;
    while (current.parentId && !seen.has(current.parentId)) {
      seen.add(current.parentId); const parent = byId.get(current.parentId);
      if (!parent) return;
      if (isQuestion(parent)) return parent;
      current = parent;
    }
  }
  const roots = items.filter(r => isQuestion(r) || (['parameters', 'simulation'].includes(r.kind) && !owner(r)));
  const groups = new Map(roots.map(record => [record.id, { record, members: [] as HistoryRecord[] }]));
  for (const item of items) {
    if (groups.has(item.id) || !['parameters', 'simulation', 'analysis'].includes(item.kind)) continue;
    const parent = owner(item);
    if (parent) groups.get(parent.id)?.members.push(item);
  }
  return [...groups.values()].sort((a, b) => text(b.record.createdAt).localeCompare(text(a.record.createdAt)));
}

export interface ParameterView { label: string; value: string }
export interface EffectView {
  id: string; time: string; label: string; summary: string; error: string;
  parameters: ParameterView[]; metrics: { label: string; before: string; after: string; unit: string }[];
}
export interface PlanView { id: string; name: string; description: string; parameters: ParameterView[]; draft: boolean; effects: EffectView[] }
const metricLabels: Record<string, [string, string]> = {
  rsrpDbm: ['RSRP', 'dBm'], sinrDb: ['SINR', 'dB'], throughputMbps: ['吞吐量', 'Mbps'],
  resourceUtilizationPct: ['PRB占用率', '%'], packetLossPct: ['丢包率', '%'], latencyMs: ['时延', 'ms'],
  retransmissions: ['重传次数', '次'], demandMbps: ['业务需求', 'Mbps'],
};
function parameters(plan: Record<string, unknown>, values?: unknown): ParameterView[] {
  const saved = object(values);
  return array(plan.parameters).map(raw => {
    const p = object(raw); const value = saved[text(p.id)] ?? p.suggestedValue;
    return { label: [text(p.objectId), text(p.label) || text(p.key)].filter(Boolean).join(' · ') || '参数', value: `${text(value) || '未提供'} ${text(p.unit)}`.trim() };
  });
}
function effect(raw: unknown, item: HistoryRecord, plan: Record<string, unknown>, values: unknown, initial = false): EffectView | null {
  const r = object(raw), p = object(item.payload);
  // 预测结果也有 result 字段；只有真正的仿真结构才能作为效果展示。
  if (!Array.isArray(r.metrics) && !text(p.error)) return null;
  return { id: item.id, time: item.createdAt, label: initial ? '建议参数的预计效果' : '本次参数的仿真效果',
    summary: text(r.summary), error: text(p.error) || (r.status === 'failed' ? '仿真未成功，暂无有效效果。' : ''),
    parameters: parameters(plan, values ?? r.parameterValues),
    metrics: array(r.metrics).map(rawMetric => {
      const m = object(rawMetric), key = text(m.key), meta = metricLabels[key];
      return { label: [text(m.objectId), meta?.[0] || key || '指标'].filter(Boolean).join(' · '), unit: meta?.[1] || '', before: text(m.before) || '—', after: text(m.after) || '—' };
    }),
  };
}
export function recordContent(entry: QuestionEntry) {
  const { record, members } = entry, p = object(record.payload), response = object(p.response), d = object(response.diagnosis);
  const parts: string[] = [];
  if (text(d.anomalyType)) parts.push(`问题判断：${text(d.anomalyType)}`);
  if (text(d.cause)) parts.push(`原因研判：${text(d.cause)}`);
  const evidence = array(d.evidence).map(raw => {
    const e = object(raw); return `${text(e.metric)} ${text(e.value)}${text(e.threshold) ? `（阈值 ${text(e.threshold)}）` : ''}`;
  }).filter(v => v.trim());
  if (evidence.length) parts.push(`判断依据：${evidence.join('；')}`);
  const reply = text(response.reply) || text(p.reply) || parts.join('\n\n');
  const plans = new Map<string, PlanView>();
  const snapshots = new Map<string, Record<string, unknown>>();
  const ensure = (raw: unknown, fallback: string) => {
    const plan = object(raw); if (!Object.keys(plan).length) return null;
    const id = text(plan.id) || text(plan.name) || fallback;
    if (!plans.has(id)) {
      plans.set(id, { id, name: text(plan.name) || '处理方案', description: text(plan.description), parameters: parameters(plan), draft: false, effects: [] });
      snapshots.set(id, plan);
    }
    return plans.get(id)!;
  };
  for (const [i, raw] of array(response.plans).entries()) {
    const plan = ensure(raw, `plan-${i}`);
    if (plan) {
      const snapshot = object(raw), initial = effect(snapshot.simulation, record, snapshot, undefined, true);
      if (initial) plan.effects.push(initial);
    }
  }
  const operations = [record, ...members].filter(r => r.kind === 'parameters' || r.kind === 'simulation').sort((a, b) => text(a.createdAt).localeCompare(text(b.createdAt)));
  for (const item of operations) {
    const payload = object(item.payload), plan = ensure(payload.plan, item.id);
    if (!plan) continue;
    const snapshot = object(payload.plan);
    plan.parameters = parameters(snapshot, payload.values);
    if (item.kind === 'parameters') {
      plan.draft = payload.action !== '选择方案';
      if (payload.action === '选择方案' && !plan.effects.length) {
        const initial = effect(snapshot.simulation, item, snapshot, payload.values, true);
        if (initial) plan.effects.push(initial);
      }
    } else {
      const run = effect(payload.result, item, snapshot, payload.values);
      if (run) {
        // 旧版为默认仿真额外保存过一条，合并重复，保留手动重算记录。
        const duplicateInitial = /初始预计效果$/.test(item.question) && plan.effects.length && JSON.stringify(object(payload.result).metrics) === JSON.stringify(object(snapshots.get(plan.id)?.simulation).metrics);
        if (!duplicateInitial) plan.effects.unshift(run);
        if (!run.error) plan.draft = false;
      }
    }
  }
  return { reply, suggestion: text(d.suggestion), error: text(p.error), plans: [...plans.values()],
    future: object(object(p.handoff).targetWindow ?? object(p.handoff).forecast), malformed: !Object.keys(p).length };
}
export function searchable(entry: QuestionEntry): string {
  return [entry.record.question, entry.record.createdAt, entry.record.analysisTime, object(entry.record.scope).start, object(entry.record.scope).end,
    ...recordContent(entry).plans.map(p => p.name)].map(text).join(' ').toLowerCase();
}
