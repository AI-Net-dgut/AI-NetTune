import type { HistoryRecord } from './store';

export const object = (value: unknown): Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
export const array = (value: unknown): unknown[] => Array.isArray(value) ? value : [];
export const text = (value: unknown): string => typeof value === 'string' ? value : typeof value === 'number' && Number.isFinite(value) ? String(value) : '';
export interface QuestionEntry { record: HistoryRecord; members: HistoryRecord[] }
// 问题级记录（会作为列表根）：分析问答、预测研判，以及 archivePlan 之外的三类归档
// （预测预览 / 空间态势 / 回测）。后三类此前被 store 静默丢弃，现已允许入库，
// 必须同时被识别为根，否则它们会在列表里"入库但不可见"（独立审计 P2）。
export const isQuestionRecord = (r: Pick<HistoryRecord, 'kind' | 'role' | 'parentId' | 'payload'>) =>
  r.kind === 'forecast-analysis' || r.kind === 'forecast' || r.kind === 'spatial' || r.kind === 'backtest'
  || (r.kind === 'analysis'
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
  // 根 = 问题记录 / 找不到父行的参数·仿真记录 / **找不到父行的分析记录**。
  // 最后一类此前被漏掉（独立审计 P3：role='evidence' 的分析记录在父问题缺失时
  // 既不算根也不被任何分组收为成员 → 行还在库里，但列表/搜索/导出全都看不到）。
  const orphaned = (r: HistoryRecord) => ['parameters', 'simulation', 'analysis'].includes(r.kind) && !owner(r);
  const roots = items.filter(r => isQuestionRecord(r) || orphaned(r));
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
  method: string; assumptions: string[]; limitations: string[];
  parameters: ParameterView[]; metrics: { label: string; before: string; after: string; unit: string }[];
}
export interface EvaluationView {
  id: string; recordIds: string[]; time: string;
  source: 'suggested' | 'manual';
  state: 'completed' | 'failed' | 'draft' | 'unavailable' | 'pending' | 'cancelled';
  parameters: ParameterView[]; effect: EffectView | null;
}
export interface PlanView {
  id: string; name: string; description: string; source: 'system';
  evaluations: EvaluationView[];
  tasks: string[]; conditions: string[]; tradeoffs: string[];
}
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
  const error = initial ? '' : text(p.error);
  // 预测结果也有 result 字段；只有仿真结构或明确的失败才作为效果展示。
  const failed = r.status === 'failed' || (!initial && item.status === 'failed');
  if (!Array.isArray(r.metrics) && !error && !failed) return null;
  return { id: item.id, time: text(p.completedAt) || item.createdAt, label: initial ? '建议参数的预计效果' : '仿真预计效果',
    summary: text(r.summary), error: error || (failed ? '仿真未成功，暂无有效效果。' : ''),
    method: text(r.method), assumptions: array(r.assumptions).map(text).filter(Boolean), limitations: array(r.limitations).map(text).filter(Boolean),
    parameters: parameters(plan, values ?? r.parameterValues),
    metrics: array(r.metrics).map(rawMetric => {
      const m = object(rawMetric), key = text(m.key), meta = metricLabels[key];
      return { label: [text(m.objectId), meta?.[0] || key || '指标'].filter(Boolean).join(' · '), unit: meta?.[1] || '', before: text(m.before) || '—', after: text(m.after) || '—' };
    }),
  };
}
// 按参数名和值做多重集比较，兼容旧快照的参数顺序及重名参数。
function sameParameters(a: ParameterView[], b: ParameterView[]) {
  const signature = (values: ParameterView[]) => values.map(v => JSON.stringify([v.label, v.value])).sort();
  return a.length === b.length && JSON.stringify(signature(a)) === JSON.stringify(signature(b));
}
function sameEffect(a: EffectView | null, b: EffectView | null) {
  if (!a || !b) return a === b;
  return sameParameters(a.parameters, b.parameters)
    && JSON.stringify([a.summary, a.error, a.metrics, a.method, a.assumptions, a.limitations])
      === JSON.stringify([b.summary, b.error, b.metrics, b.method, b.assumptions, b.limitations]);
}
function evaluationState(item: HistoryRecord, result: EffectView | null): EvaluationView['state'] {
  if (result?.error || item.status === 'failed') return 'failed';
  if (item.status === 'pending' || item.status === 'cancelled') return item.status;
  return result ? result.metrics.length ? 'completed' : 'unavailable' : 'draft';
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
  const initialActions = new Set(['选择方案', '生成预测处置方案', '选择预测方案']);
  const inputTime = (item: HistoryRecord) => text(object(item.payload).inputAt) || text(item.createdAt);
  const addSuggested = (plan: PlanView, snapshot: Record<string, unknown>, item: HistoryRecord) => {
    // 模板效果始终使用它自身的建议参数，不能套用该记录的手动 values。
    const initial = effect(snapshot.simulation, item, snapshot, undefined, true);
    const values = initial?.parameters ?? parameters(snapshot);
    const existing = plan.evaluations.find(v => v.source === 'suggested'
      && sameParameters(v.parameters, values) && sameEffect(v.effect, initial));
    if (existing) {
      if (!existing.recordIds.includes(item.id)) existing.recordIds.push(item.id);
      return;
    }
    plan.evaluations.push({ id: item.id, recordIds: [item.id], time: item.createdAt,
      source: 'suggested', state: initial ? initial.error ? 'failed' : initial.metrics.length ? 'completed' : 'unavailable' : 'draft',
      parameters: values, effect: initial });
  };
  const ensure = (raw: unknown, item: HistoryRecord, fallback: string) => {
    const snapshot = object(raw); if (!Object.keys(snapshot).length) return null;
    const id = text(snapshot.id) || text(snapshot.name) || fallback;
    if (!plans.has(id)) {
      const plan: PlanView = { id, name: text(snapshot.name) || '处理方案', description: text(snapshot.description), source: 'system', evaluations: [],
        tasks: array(snapshot.tasks).map(text).filter(Boolean), conditions: array(snapshot.conditions).map(text).filter(Boolean), tradeoffs: array(snapshot.tradeoffs).map(text).filter(Boolean) };
      plans.set(id, plan);
      addSuggested(plan, snapshot, item);
    }
    return plans.get(id)!;
  };
  for (const [i, raw] of array(response.plans).entries()) ensure(raw, record, `plan-${i}`);
  const operations = [record, ...members].filter(r => r.kind === 'parameters' || r.kind === 'simulation').sort((a, b) => {
    const byTime = inputTime(a).localeCompare(inputTime(b));
    // 同时归档的参数快照先于结果，合并为一个参数/效果版本。
    return byTime || Number(a.kind === 'simulation') - Number(b.kind === 'simulation');
  });
  for (const item of operations) {
    const payload = object(item.payload), plan = ensure(payload.plan, item, item.id);
    if (!plan) continue;
    const snapshot = object(payload.plan);
    for (const key of ['tasks', 'conditions', 'tradeoffs'] as const) {
      if (Array.isArray(snapshot[key])) plan[key] = array(snapshot[key]).map(text).filter(Boolean);
    }
    if (item.kind === 'parameters' && initialActions.has(text(payload.action))) {
      addSuggested(plan, snapshot, item);
      if (sameParameters(parameters(snapshot, payload.values), parameters(snapshot))
        && !text(payload.error) && item.status !== 'failed' && item.status !== 'pending' && item.status !== 'cancelled') continue;
    }
    const run = effect(payload.result, item, snapshot, payload.values);
    const values = run?.parameters ?? parameters(snapshot, payload.values);
    if (item.kind === 'simulation' && /初始预计效果$/.test(item.question)) {
      const initial = plan.evaluations.find(v => v.source === 'suggested'
        && sameParameters(v.parameters, values) && (!v.effect || sameEffect(v.effect, run)));
      if (initial) {
        initial.effect = run;
        initial.state = evaluationState(item, run);
        if (!initial.recordIds.includes(item.id)) initial.recordIds.push(item.id);
      } else {
        plan.evaluations.push({ id: item.id, recordIds: [item.id], time: inputTime(item), source: 'suggested',
          state: evaluationState(item, run), parameters: values, effect: run });
      }
      continue;
    }
    // 仅把结果并入发起前保存的同一组参数。没有 inputAt 的旧结果独立成版，
    // 避免把迟到的效果错误配给请求期间新保存的草稿。
    const draft = item.kind === 'simulation' && text(payload.inputAt)
      ? [...plan.evaluations].reverse().find(v => v.source === 'manual' && v.state === 'draft'
        && v.time <= inputTime(item) && sameParameters(v.parameters, values)) : undefined;
    if (draft) {
      draft.effect = run;
      draft.state = evaluationState(item, run);
      draft.recordIds.push(item.id);
    } else {
      plan.evaluations.push({ id: item.id, recordIds: [item.id], time: inputTime(item), source: 'manual',
        state: evaluationState(item, run), parameters: values, effect: run });
    }
  }
  for (const plan of plans.values()) plan.evaluations.sort((a, b) => a.time.localeCompare(b.time));
  return { reply, suggestion: text(d.suggestion), error: text(p.error), plans: [...plans.values()],
    future: object(object(p.handoff).targetWindow ?? object(p.handoff).forecast), malformed: !Object.keys(p).length };
}
export function searchable(entry: QuestionEntry): string {
  return [entry.record.question, entry.record.createdAt, entry.record.analysisTime, object(entry.record.scope).start, object(entry.record.scope).end,
    ...recordContent(entry).plans.map(p => p.name)].map(text).join(' ').toLowerCase();
}
