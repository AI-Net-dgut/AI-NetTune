import { aggregateCells, fetchRecords, toEpoch } from "../services/backend";
import { observationMeans, uniqueObservationRecords } from "../services/dataObservation";
import { getLatestFrame } from "../services/metrics";
import { sceneArea } from "../services/sceneGeometry";
import type { DashboardData, MetricRecord, NetworkLink, NetworkNode, QueryScope } from "../types/backend";
import { formatReplayTime } from "./replayState";

export interface ReplayRange { start: number; end: number }

const SECOND = 1000;

export function replayCoveredThrough(ranges: ReplayRange[], at: number): number | undefined {
  const second = Math.floor(at / SECOND) * SECOND;
  return ranges.find(range => range.start <= second && second <= range.end)?.end;
}

export function replayCovers(ranges: ReplayRange[], scope: QueryScope): boolean {
  return (replayCoveredThrough(ranges, toEpoch(scope.start)) ?? -Infinity) >= toEpoch(scope.end);
}

function mergeRanges(ranges: ReplayRange[]): ReplayRange[] {
  const merged: ReplayRange[] = [];
  for (const range of ranges.sort((a, b) => a.start - b.start)) {
    const last = merged.at(-1);
    if (last && range.start <= last.end + SECOND) last.end = Math.max(last.end, range.end);
    else merged.push({ ...range });
  }
  return merged;
}

export function createReplayBuffer(scenario: string, signal: AbortSignal, fetcher: typeof fetchRecords = fetchRecords) {
  let records: MetricRecord[] = [];
  let ranges: ReplayRange[] = [];
  const pending = new Set<ReplayRange & { promise: Promise<void> }>();

  async function ensure(scope: QueryScope): Promise<MetricRecord[]> {
    const start = toEpoch(scope.start), end = toEpoch(scope.end);
    if (scope.scenario !== scenario || start > end) throw new Error("回放查询范围无效");
    let next = start;
    while (next <= end) {
      signal.throwIfAborted();
      const covered = replayCoveredThrough(ranges, next);
      if (covered !== undefined) {
        next = covered + SECOND;
        continue;
      }
      const running = [...pending].find(range => range.start <= next && next <= range.end);
      if (running) {
        await running.promise;
        continue;
      }
      // 已缓存或正在请求的区间都不重复查询。接口区间两端包含，下一段从下一秒开始。
      const boundary = Math.min(end, ...[...ranges, ...pending]
        .filter(range => range.start > next).map(range => range.start - SECOND));
      const range = { start: next, end: boundary };
      const promise = fetcher({ scenario, start: formatReplayTime(range.start), end: formatReplayTime(range.end) }, signal)
        .then(rows => {
          signal.throwIfAborted();
          records = uniqueObservationRecords([...records, ...rows]);
          // 空响应同样表示范围已经查过，失败或取消时不写覆盖范围。
          ranges = mergeRanges([...ranges, range]);
        });
      const entry = { ...range, promise };
      pending.add(entry);
      try { await promise; } finally { pending.delete(entry); }
    }
    return records;
  }

  return { ensure, get records() { return records; }, get ranges() { return ranges; } };
}

function windowLinks(metrics: MetricRecord[]): NetworkLink[] {
  const byUE = new Map<string, Map<string, MetricRecord[]>>();
  for (const row of metrics) {
    const aps = byUE.get(row.ueId) ?? new Map<string, MetricRecord[]>();
    const rows = aps.get(row.apId) ?? []; rows.push(row); aps.set(row.apId, rows); byUE.set(row.ueId, aps);
  }
  return [...byUE].map(([ue, aps]) => {
    const [ap, rows] = [...aps].sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]))[0];
    const mean = observationMeans(rows);
    return { id: `${ap}->${ue}`, source: ap, target: ue, rsrp: mean.rsrpDbm ?? NaN,
      sinr: mean.sinrDb ?? NaN, throughput: mean.throughputMbps ?? NaN, primary: true };
  });
}

export function buildReplayDashboard(records: MetricRecord[], scope: QueryScope, knownAPs: NetworkNode[]): DashboardData {
  const start = toEpoch(scope.start), end = toEpoch(scope.end);
  const windowRecords: MetricRecord[] = [];
  const nodes = new Map<string, NetworkNode>();
  const lastSeenTimes = new Map<string, number>();
  // 仅保留已知静态部署，不复用预加载末尾的 UE 坐标或服务关系。
  for (const ap of knownAPs) if (ap.type === "ap") nodes.set(ap.id, { ...ap });
  // 已出现的节点短暂缺采样时保留最近已知位置；指标和连接仍只使用当前窗口。
  // 单次扫描，不为保留节点而对截至当前的全部历史重新排序。
  for (const row of records) {
    const time = Date.parse(row.timestamp);
    if (!Number.isFinite(time) || time > end) continue;
    if (time >= start) windowRecords.push(row);
    if (!nodes.has(row.apId)) nodes.set(row.apId, { id: row.apId, type: "ap", x: row.apX, y: row.apY });
    if (time >= (lastSeenTimes.get(row.ueId) ?? -Infinity)) {
      nodes.set(row.ueId, { id: row.ueId, type: "ue", x: row.x, y: row.y, lastSeen: row.backendTime });
      lastSeenTimes.set(row.ueId, time);
    }
  }
  const metrics = uniqueObservationRecords(windowRecords);
  const latest = getLatestFrame(metrics);
  const values = [...nodes.values()], gridSize = 20;
  return {
    scope: { ...scope }, generatedAt: latest.timestamp ?? "", sampledFrameTime: latest.metrics[0]?.backendTime ?? null, metrics, nodes: values,
    links: windowLinks(metrics), gridSize, cells: aggregateCells(latest.metrics, gridSize), apMeans: [],
    area: sceneArea(scope.scenario, values),
  };
}
