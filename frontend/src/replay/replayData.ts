import { aggregateCells, fetchRecords, toEpoch } from "../services/backend";
import { observationMeans, uniqueObservationRecords } from "../services/dataObservation";
import { getLatestFrame } from "../services/metrics";
import { sceneArea } from "../services/sceneGeometry";
import type { DashboardData, MetricRecord, NetworkLink, NetworkNode, QueryScope } from "../types/backend";
import { formatReplayTime } from "./replayState";
import type { ReplayConfig } from "./config";

export async function loadReplayRecords(config: ReplayConfig, signal?: AbortSignal, fetcher: typeof fetch = fetch): Promise<MetricRecord[]> {
  const start = toEpoch(config.start), end = toEpoch(config.end);
  if (end < start) throw new Error("回放结束时间早于开始时间");
  const rows: MetricRecord[] = [];
  // 兼容旧服务的三天上限；旧 fetchRecords 内部仍负责拆窗、接口上限和取消请求。
  const chunk = 2 * 86400_000;
  for (let time = start; time <= end; time += chunk) {
    rows.push(...await fetchRecords({ scenario: config.scenario, start: formatReplayTime(time),
      end: formatReplayTime(Math.min(end, time + chunk - 1000)) }, signal, fetcher));
  }
  return uniqueObservationRecords(rows).filter(row => {
    const time = Date.parse(row.timestamp); return time >= start && time <= end;
  });
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
