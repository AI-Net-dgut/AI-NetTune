import type { QueryScope } from '../types/backend';
export type RecordKind = 'analysis' | 'forecast' | 'forecast-analysis' | 'parameters' | 'simulation' | 'spatial' | 'backtest';
export interface HistoryRecord {
    id: string;
    kind: RecordKind;
    question: string;
    createdAt: string;
    analysisTime: string;
    scope: QueryScope;
    masterTime?: string;
    mode?: string;
    parentId?: string;
    status?: 'pending' | 'completed' | 'failed' | 'cancelled';
    payload: unknown;
}
export const kindLabels: Record<RecordKind, string> = { analysis: '分析问答', forecast: '预测预览', 'forecast-analysis': '预测研判', parameters: '方案与参数', simulation: '仿真效果', spatial: '空间态势序列', backtest: '历史回测' };
const memory = new Map<string, HistoryRecord>();
const unsaved = new Set<string>();
let opening: Promise<IDBDatabase> | undefined;
let writes: Promise<void> = Promise.resolve();
let problem = '';
export const historyProblem = () => problem;
function changed() { window.dispatchEvent(new Event('aintune-history')); }
function db(): Promise<IDBDatabase> {
    if (!opening)
        opening = new Promise<IDBDatabase>((resolve, reject) => {
            if (!globalThis.indexedDB) {
                reject(new Error('浏览器不支持本地数据库'));
                return;
            }
            const req = indexedDB.open('aintune-history', 1);
            req.onupgradeneeded = () => { if (!req.result.objectStoreNames.contains('records'))
                req.result.createObjectStore('records', { keyPath: 'id' }); };
            req.onsuccess = () => { req.result.onversionchange = () => { req.result.close(); opening = undefined; }; resolve(req.result); };
            req.onerror = () => reject(req.error);
            req.onblocked = () => reject(new Error('请关闭其他旧版本页面后重试'));
        }).catch(e => { opening = undefined; throw e; });
    return opening;
}
async function mutate(action: (s: IDBObjectStore) => void) {
    const d = await db();
    await new Promise<void>((resolve, reject) => {
        const tx = d.transaction('records', 'readwrite');
        action(tx.objectStore('records'));
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
        tx.onabort = () => reject(tx.error);
    });
}
// 仅保存用户问答、处理方案和仿真效果。旧类型仍可读取，自动图表不再新增记录。
function compactPayload(kind: RecordKind, value: unknown): unknown {
    const object = (v: unknown): Record<string, unknown> => v !== null && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, unknown> : {};
    const effect = (v: unknown) => {
        const r = object(v);
        return Array.isArray(r.metrics) ? { id: r.id, status: r.status, scope: r.scope, summary: r.summary, metrics: r.metrics, parameterValues: r.parameterValues } : undefined;
    };
    const plan = (v: unknown) => {
        const r = object(v);
        return { id: r.id, name: r.name, description: r.description, scope: r.scope, objectIds: r.objectIds, parameters: r.parameters, simulation: effect(r.simulation) };
    };
    const p = object(value);
    if (kind === 'analysis') {
        const r = object(p.response);
        return { error: p.error, reply: p.reply, response: p.response ? {
            reply: r.reply, diagnosis: r.diagnosis, plans: Array.isArray(r.plans) ? r.plans.map(plan) : [],
        } : undefined };
    }
    if (kind === 'forecast-analysis') {
        const handoff = object(p.handoff);
        return { reply: p.reply, error: p.error, handoff: { targetWindow: handoff.targetWindow, forecast: object(handoff.result).forecast } };
    }
    return { error: p.error, action: p.action, values: p.values, plan: p.plan ? plan(p.plan) : undefined, result: effect(p.result) };
}
export function record(input: Omit<HistoryRecord, 'id' | 'createdAt'> & {
    id?: string;
    createdAt?: string;
}): string {
    if (input.kind === 'forecast' || input.kind === 'spatial' || input.kind === 'backtest') return '';
    const item = structuredClone({ ...memory.get(input.id ?? ''), ...input, payload: compactPayload(input.kind, input.payload), id: input.id ?? crypto.randomUUID(), createdAt: input.createdAt ?? memory.get(input.id ?? '')?.createdAt ?? new Date().toISOString() });
    memory.set(item.id, item);
    unsaved.add(item.id);
    writes = writes.then(async () => {
        try {
            await mutate(s => s.put(item));
            if (memory.get(item.id) === item)
                unsaved.delete(item.id);
            if (!unsaved.size)
                problem = '';
        }
        catch {
            problem = '记录尚未写入浏览器数据库。请重试保存或导出备份，离开页面可能丢失未保存内容。';
        }
        changed();
    });
    changed();
    return item.id;
}
export async function flushRecords() { await writes; }
export async function retryRecords() { for (const id of [...unsaved]) {
    const item = memory.get(id);
    if (item)
        record(item);
} await writes; }
export async function listRecords(): Promise<HistoryRecord[]> {
    await writes;
    let items: HistoryRecord[] = [];
    try {
        const d = await db();
        items = await new Promise((resolve, reject) => { const r = d.transaction('records').objectStore('records').getAll(); r.onsuccess = () => resolve(r.result); r.onerror = () => reject(r.error); });
    }
    catch {
        problem = '暂时无法读取本地记录，请重试；当前未保存内容仍可导出。';
    }
    const merged = new Map(items.map(r => [r.id, r]));
    for (const [id, r] of memory)
        merged.set(id, r);
    return [...merged.values()].filter(r => r && typeof r.id === 'string').sort((a, b) => String(b.createdAt ?? '').localeCompare(String(a.createdAt ?? '')));
}
export async function deleteRecords(ids: string[]) {
    await writes;
    await mutate(s => { for (const id of ids) s.delete(id); });
    for (const id of ids) { memory.delete(id); unsaved.delete(id); }
    changed();
}
export async function deleteRecord(id: string) { await deleteRecords([id]); }
export function downloadRecords(records: HistoryRecord[]) {
    const url = URL.createObjectURL(new Blob([JSON.stringify({ version: 1, exportedAt: new Date().toISOString(), records }, null, 2)], { type: 'application/json' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = `AI-NetTune-records-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export const forecastRecordIds = new WeakMap<object, string>();
