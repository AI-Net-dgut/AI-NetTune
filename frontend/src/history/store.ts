import type { QueryScope } from '../types/backend';
import { questionEntries, isQuestionRecord } from './historyModel';
// 容量以“问题及其关联内容”为单位；满额时不自动删除已有记录。
export const MAX_HISTORY_QUESTIONS = 100;
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
    role?: 'question' | 'evidence';
    status?: 'pending' | 'completed' | 'failed' | 'cancelled';
    payload: unknown;
}
export const kindLabels: Record<RecordKind, string> = { analysis: '分析问答', forecast: '预测预览', 'forecast-analysis': '预测研判', parameters: '方案与参数', simulation: '仿真效果', spatial: '空间态势序列', backtest: '历史回测' };
const memory = new Map<string, HistoryRecord>();
const unsaved = new Set<string>();
let opening: Promise<IDBDatabase> | undefined;
let writes: Promise<void> = Promise.resolve();
let problem = '';
let contentProblem = '';
let capacityProblem = '';
const rejectedIds = new Set<string>();
export const historyProblem = () => [capacityProblem, contentProblem, problem].filter(Boolean).join(' ');
function changed() {
    try { globalThis.window?.dispatchEvent(new Event('aintune-history')); } catch { /* 通知异常不能影响分析请求。 */ }
}

// ===== 跨标签页同步（审计第 9 条）=====
// 问题：memory/unsaved/rejectedIds 都是**每个标签页私有**的，而数据库是共享的。
// 于是 A 页在 B 页删除后仍显示旧记录；A 页迟到的分析结果还会把已删记录重新写回。
// 做法：删除成功后广播 id（BroadcastChannel 优先，localStorage 作兼容兜底），
// 收到广播的页面把 id 加入 rejectedIds 并从 memory 移除 —— 既同步列表，也拦住迟到写入。
type SyncChannel = { postMessage: (message: unknown) => void; onmessage: ((event: { data: unknown }) => void) | null };
const SYNC_NAME = 'aintune-history';
const DELETED_KEY = 'aintune-history-deleted';
function makeChannel(): SyncChannel | null {
    try {
        const Ctor = (globalThis as unknown as { BroadcastChannel?: new (name: string) => SyncChannel }).BroadcastChannel;
        return typeof Ctor === 'function' ? new Ctor(SYNC_NAME) : null;
    } catch { return null; }
}
const syncChannel = makeChannel();
function broadcastDeleted(ids: string[]) {
    try { syncChannel?.postMessage({ type: 'deleted', ids }); } catch { /* 广播失败不影响删除结果。 */ }
    try { (globalThis as unknown as { localStorage?: Storage }).localStorage?.setItem(DELETED_KEY, JSON.stringify({ ids, at: Date.now() })); } catch { /* 隐私模式可能禁用。 */ }
}
function applyDeleted(ids: string[]) {
    for (const id of ids) {
        memory.delete(id);
        unsaved.delete(id);
        rejectedIds.add(id);        // 关键：拦住本页迟到的写入，避免"删除后复活"
    }
    capacityProblem = '';           // 别的页面腾出了容量，本页的满额提示要撤掉
    changed();
}
if (syncChannel) {
    syncChannel.onmessage = (event) => {
        const data = event?.data as { type?: string; ids?: unknown } | undefined;
        if (data?.type === 'deleted' && Array.isArray(data.ids)) applyDeleted(data.ids.filter((x): x is string => typeof x === 'string'));
        else if (data?.type === 'saved') changed();      // 别的页面存了新记录 → 让本页重新读库
    };
}
try {
    (globalThis as unknown as { window?: Window }).window?.addEventListener?.('storage', (event: StorageEvent) => {
        if (event.key !== DELETED_KEY || !event.newValue) return;
        try {
            const data = JSON.parse(event.newValue) as { ids?: unknown };
            if (Array.isArray(data?.ids)) applyDeleted(data.ids.filter((x): x is string => typeof x === 'string'));
        } catch { /* 非法载荷忽略。 */ }
    });
} catch { /* 非浏览器环境。 */ }
let idSequence = 0;
function createId() {
    try { if (typeof globalThis.crypto?.randomUUID === 'function') return globalThis.crypto.randomUUID(); } catch { /* 普通内网HTTP可能不支持此API。 */ }
    return `history-${Date.now().toString(36)}-${(++idSequence).toString(36)}-${Math.random().toString(36).slice(2)}`;
}
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
async function removeWithinTransaction(ids: string[]): Promise<string[]> {
    const d = await db();
    return new Promise((resolve, reject) => {
        const tx = d.transaction('records', 'readwrite'), s = tx.objectStore('records');
        const removed = new Set(ids);
        tx.oncomplete = () => resolve([...removed]);
        tx.onerror = () => reject(tx.error);
        tx.onabort = () => reject(tx.error);
        const req = s.getAll();
        req.onerror = () => reject(req.error);
        req.onsuccess = () => {
            try {
                // 以事务执行时的数据为准，包含点击删除前刚返回、尚未显示的子记录。
                const all = new Map<string, HistoryRecord>(req.result.map((row: HistoryRecord) => [row.id, row]));
                for (const [id, row] of memory) all.set(id, row);
                // 必须遵守页面的问题分组边界，不能顺着parentId误删另一个独立问题。
                for (const group of questionEntries([...all.values()])) {
                    if (removed.has(group.record.id)) for (const member of group.members) removed.add(member.id);
                }
                for (const id of removed) s.delete(id);
            } catch (reason) {
                reject(reason);
                tx.abort?.();
            }
        };
    });
}
function exceedsCapacity(rows: HistoryRecord[], item: HistoryRecord) {
    const before = questionEntries(rows).length;
    const next = new Map(rows.map(r => [r.id, r]));
    next.set(item.id, item);
    const after = questionEntries([...next.values()]).length;
    return after > MAX_HISTORY_QUESTIONS && after > before;
}
function refuseRecord(item: HistoryRecord) {
    rejectedIds.add(item.id);
    memory.delete(item.id);
    unsaved.delete(item.id);
    capacityProblem = `已达到 ${MAX_HISTORY_QUESTIONS} 个问题的保存上限，新问题未加入记录。分析仍可继续；请先导出或删除部分记录，再保存后续新问题。`;
}
async function persistWithinCapacity(item: HistoryRecord): Promise<boolean> {
    const d = await db();
    return new Promise((resolve, reject) => {
        // 在同一读写事务中计数与写入，多个页面同时保存也不能越过上限。
        const tx = d.transaction('records', 'readwrite'), s = tx.objectStore('records');
        let accepted = false;
        tx.oncomplete = () => resolve(accepted);
        tx.onerror = () => reject(tx.error);
        tx.onabort = () => reject(tx.error);
        const req = s.getAll();
        req.onerror = () => reject(req.error);
        req.onsuccess = () => {
            try {
                if (exceedsCapacity(req.result, item)) return;
                s.put(item);
                accepted = true;
            } catch (reason) {
                reject(reason);
                tx.abort?.();
            }
        };
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
        return { id: r.id, name: r.name, description: r.description, scope: r.scope, objectIds: r.objectIds, parameters: r.parameters, tasks: r.tasks, conditions: r.conditions, tradeoffs: r.tradeoffs, simulation: effect(r.simulation) };
    };
    const p = object(value);
    if (kind === 'analysis') {
        const r = object(p.response);
        return { error: p.error, reply: p.reply, response: p.response ? {
            reply: r.reply, intent: r.intent, hasChart: r.hasChart ?? Boolean(r.chart), diagnosis: r.diagnosis, plans: Array.isArray(r.plans) ? r.plans.map(plan) : [],
        } : undefined };
    }
    if (kind === 'forecast-analysis') {
        const handoff = object(p.handoff);
        return { reply: p.reply, error: p.error, handoff: { targetWindow: handoff.targetWindow, forecast: handoff.forecast ?? object(handoff.result).forecast } };
    }
    return { error: p.error, action: p.action, inputAt: p.inputAt, completedAt: p.completedAt, values: p.values, plan: p.plan ? plan(p.plan) : undefined, result: effect(p.result) };
}
type RecordInput = Omit<HistoryRecord, 'id' | 'createdAt'> & { id?: string; createdAt?: string };
export function record(input: RecordInput): string {
    try { return saveRecord(input); }
    catch {
        contentProblem = '部分记录未能完整保存，分析仍可继续。请保留页面结果或导出已有记录。';
        changed();
        return '';
    }
}
function saveRecord(input: RecordInput): string {
    if (input.kind === 'forecast' || input.kind === 'spatial' || input.kind === 'backtest') return '';
    if (input.role === 'evidence' && !input.parentId) return '';
    if ((input.id && rejectedIds.has(input.id)) || (input.parentId && rejectedIds.has(input.parentId) && !isQuestionRecord(input))) return '';
    const item = structuredClone({ ...memory.get(input.id ?? ''), ...input, payload: compactPayload(input.kind, input.payload), id: input.id || createId(), createdAt: input.createdAt ?? memory.get(input.id ?? '')?.createdAt ?? new Date().toISOString() });
    // 内存可能没有旧父问题，不能把它的每次仿真误当成独立问题。
    // 子记录与旧版结构交给下方完整数据库事务核对。
    const inMemoryQuestions = [...memory.values()].filter(r => r.kind === 'analysis' || r.kind === 'forecast-analysis');
    if (!input.id && !item.parentId && (item.kind === 'analysis' || item.kind === 'forecast-analysis') && exceedsCapacity(inMemoryQuestions, item)) { refuseRecord(item); changed(); return ''; }
    memory.set(item.id, item);
    unsaved.add(item.id);
    writes = writes.then(async () => {
        try {
            if (rejectedIds.has(item.id) || (item.parentId && rejectedIds.has(item.parentId) && !isQuestionRecord(item))) {
                memory.delete(item.id);
                unsaved.delete(item.id);
            } else if (!await persistWithinCapacity(item)) {
                refuseRecord(item);
            }
            if (memory.get(item.id) === item)
                unsaved.delete(item.id);
            if (!unsaved.size)
                problem = '';
            try { syncChannel?.postMessage({ type: 'saved' }); } catch { /* 通知别的页面刷新列表。 */ }
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
    // 只把"还没落库"的条目（unsaved，等待重试的）补进来。
    // 不能无条件用 memory 覆盖数据库：别的标签页删除后，本页 memory 仍是旧值，
    // 会把已删记录重新显示出来（审计第 9 条场景一）。
    for (const [id, r] of memory)
        if (!merged.has(id) && unsaved.has(id)) merged.set(id, r);
    return [...merged.values()].filter(r => r && typeof r.id === 'string').sort((a, b) => String(b.createdAt ?? '').localeCompare(String(a.createdAt ?? '')));
}
export async function deleteRecords(ids: string[]) {
    // 删除也进入写入队列。之后到达的分析/仿真响应必须等删除完成，
    // 再根据已删除的ID丢弃，不能重新写回。
    const deletion = writes.then(async () => {
        const removed = await removeWithinTransaction(ids);
        for (const id of removed) { memory.delete(id); unsaved.delete(id); rejectedIds.add(id); }
        capacityProblem = '';
        if (!unsaved.size) problem = '';
        // 通知其它标签页：这些 id 已删，别再显示、也别让迟到写入复活
        if (removed.length) broadcastDeleted(removed);
        changed();
    });
    writes = deletion.catch(() => { /* 调用方显示删除错误，后续分析保存仍可继续。 */ });
    await deletion;
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
