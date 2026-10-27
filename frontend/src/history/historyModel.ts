import type { HistoryRecord } from './store';

export const object = (value: unknown): Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
export const array = (value: unknown): unknown[] => Array.isArray(value) ? value : [];
export const text = (value: unknown): string => typeof value === 'string' ? value : typeof value === 'number' && Number.isFinite(value) ? String(value) : '';
export interface QuestionEntry { record: HistoryRecord; members: HistoryRecord[] }
export const isQuestionRecord = (r: Pick<HistoryRecord, 'kind' | 'role' | 'parentId' | 'payload'>) => r.kind === 'forecast-analysis' || (r.kind === 'analysis'
  && r.role !== 'evidence'
  // 旧版辅助请求有父问题，但精简后可能已丢失chart；不能依靠chart来识别角色。
  && !(r.role !== 'question' && r.parentId && !object(object(r.payload).response).diagnosis));
export function questionEntries(records: HistoryRecord[]): QuestionEntry[] {
  const items = records.filter(r => r && typeof r.id === 'string');
  const byId = new Map(items.map(r => [r.id, r]));
  function owner(r: HistoryRecord): HistoryRecord | undefined {
    const seen = new Set([r.id]); let current = r;
    while (current.parentId && !seen.has(current.parentId)) {
      seen.add(current.parentId); const parent = byId.get(current.parentId);
      if (!parent) return;
      if (isQuestionRecord(parent)) return parent;
      current = parent;
    }
  }
  const roots = items.filter(r => isQuestionRecord(r) || (['parameters', 'simulation'].includes(r.kind) && !owner(r)));
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
export interface PlanView { id: string; name: string; description: string; parameters: ParameterView[]; draft: boolean; effects: EffectView[]; tasks: string[]; conditions: string[]; tradeoffs: string[] }
const metricLabels: Record<string, [string, string]> = {
  rsrpDbm: ['RSRP', 'dBm'], sinrDb: ['SINR', 'dB'], throughputMbps: ['吞吐量', 'Mbps'],
  resourceUtilizationPct: ['PRB占用率', '%'], packetLossPct: ['丢包率', '%'], latencyMs: ['时延', 'ms'],
  retransmissions: ['重传次数', '次'], demandMbps: ['业务需求', 'Mbps'],
};
function parameters(plan: Record<string, unknown>, values?: unknown): ParameterView[] {
  const saved = object(values);
  return array(plan.parameters).map(raw => {
    const p = object(raw); const value = saved[text(p.id)] ?? p.suggestedValue;
    const formatted = p.type === 'number' && text(value).trim() && Number.isFinite(Number(value)) ? String(Number(value)) : text(value);
    return { label: [text(p.objectId), text(p.label) || text(p.key)].filter(Boolean).join(' · ') || '参数', value: `${formatted || '未提供'} ${text(p.unit)}`.trim() };
  });
}
function effect(raw: unknown, item: HistoryRecord, plan: Record<string, unknown>, values: unknown, initial = false): EffectView | null {
  const r = object(raw), p = object(item.payload);
  // 预测结果也有 result 字段；只有真正的仿真结构才能作为效果展示。
  if (!Array.isArray(r.metrics) && !text(p.error)) return null;
  return { id: item.id, time: text(p.completedAt) || item.createdAt, label: initial ? '建议参数的预计效果' : '本次参数的仿真效果',
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
      plans.set(id, { id, name: text(plan.name) || '处理方案', description: text(plan.description), parameters: parameters(plan), draft: false, effects: [], tasks: array(plan.tasks).map(text).filter(Boolean), conditions: array(plan.conditions).map(text).filter(Boolean), tradeoffs: array(plan.tradeoffs).map(text).filter(Boolean) });
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
  const inputTime = (item: HistoryRecord) => text(object(item.payload).inputAt) || text(item.createdAt);
  const operations = [record, ...members].filter(r => r.kind === 'parameters' || r.kind === 'simulation').sort((a, b) => {
    const byTime = inputTime(a).localeCompare(inputTime(b));
    // 同一时间戳时优先保留显式保存的草稿，不让迟到结果覆盖它。
    return byTime || Number(a.kind === 'parameters') - Number(b.kind === 'parameters');
  });
  const manualInputs = new Map<string, string>();
  const needsEvaluation = new Set<string>();
  for (const item of operations) {
    const payload = object(item.payload), plan = ensure(payload.plan, item.id);
    if (!plan) continue;
    const snapshot = object(payload.plan);
    for (const key of ['tasks', 'conditions', 'tradeoffs'] as const) {
      if (Array.isArray(snapshot[key])) plan[key] = array(snapshot[key]).map(text).filter(Boolean);
    }
    if (item.kind === 'parameters') {
      manualInputs.set(plan.id, inputTime(item));
      plan.parameters = parameters(snapshot, payload.values);
      if (payload.action !== '选择方案') needsEvaluation.add(plan.id);
      else needsEvaluation.delete(plan.id);
      if (payload.action === '选择方案' && !plan.effects.length) {
        const initial = effect(snapshot.simulation, item, snapshot, payload.values, true);
        if (initial) plan.effects.push(initial);
      }
    } else {
      const run = effect(payload.result, item, snapshot, payload.values);
      if (run) {
        // 仿真依据“发起时的参数版本”。旧记录没有inputAt时，不覆盖已有手工保存版本。
        const manualTime = manualInputs.get(plan.id);
        if (!manualTime || (text(payload.inputAt) && inputTime(item) > manualTime)) {
          plan.parameters = parameters(snapshot, payload.values);
          needsEvaluation.add(plan.id);
        }
        const duplicateInitial = /初始预计效果$/.test(item.question) && plan.effects.length && JSON.stringify(object(payload.result).metrics) === JSON.stringify(object(snapshots.get(plan.id)?.simulation).metrics);
        if (!duplicateInitial) plan.effects.push(run);
      }
    }
  }
  const sameParameters = (a: ParameterView[], b: ParameterView[]) => a.length === b.length && a.every((v, i) => v.label === b[i].label && v.value === b[i].value);
  for (const plan of plans.values()) {
    plan.effects.sort((a, b) => text(b.time).localeCompare(text(a.time)));
    plan.draft = needsEvaluation.has(plan.id) && !plan.effects.some(e => !e.error && e.metrics.length > 0 && sameParameters(e.parameters, plan.parameters));
  }
  return { reply, suggestion: text(d.suggestion), error: text(p.error), plans: [...plans.values()],
    future: object(object(p.handoff).targetWindow ?? object(p.handoff).forecast), malformed: !Object.keys(p).length };
}
export function searchable(entry: QuestionEntry): string {
  return [entry.record.question, entry.record.createdAt, entry.record.analysisTime, object(entry.record.scope).start, object(entry.record.scope).end,
    ...recordContent(entry).plans.map(p => p.name)].map(text).join(' ').toLowerCase();
}
