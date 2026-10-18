import { METRIC_META } from "../types/backend";
import type { DashboardData, MetricKey, MetricRecord, NetworkLink, NetworkNode } from "../types/backend";
import { getLatestFrame, getUEHistory } from "./metrics";
import { uniqueObservationRecords } from "./dataObservation";

// 仅平滑展示位置：窗口模式为采样平均位置；最新帧模式保持实际最新位置。
// 不改写 data.nodes/data.metrics，不把过往位置标成当前测量，也不补造连接。
export function topologyNodes(data: DashboardData, period: "latest" | "window"): NetworkNode[] {
  const records = uniqueObservationRecords(data.metrics);
  const byUE = new Map<string, MetricRecord[]>();
  for (const row of records) {
    const rows = byUE.get(row.ueId) ?? []; rows.push(row); byUE.set(row.ueId, rows);
  }
  const latest = new Map(getLatestFrame(records).metrics.map(row => [row.ueId, row]));
  return data.nodes.map(node => {
    if (node.type === "ap") return node;
    const rows = byUE.get(node.id) ?? [], last = rows.at(-1);
    if (period === "window") {
      const positions = rows.filter(row => Number.isFinite(row.x) && Number.isFinite(row.y));
      if (positions.length) return { ...node,
        x: positions.reduce((sum, row) => sum + row.x, 0) / positions.length,
        y: positions.reduce((sum, row) => sum + row.y, 0) / positions.length,
        lastSeen: last?.backendTime ?? node.lastSeen, missingSample: false,
        positionNote: "窗口平均位置，非某一时刻实际坐标" };
    } else if (latest.has(node.id)) {
      const row = latest.get(node.id)!;
      return { ...node, x: row.x, y: row.y, lastSeen: row.backendTime, missingSample: false, positionNote: "最新帧实际采样位置" };
    }
    return { ...node, missingSample: true,
      positionNote: `${period === "window" ? "本窗口" : "本帧"}无采样，仅显示最近已知位置，不代表离线` };
  });
}

export const LINK_METRICS = {
  rsrp: { label: "RSRP", unit: "dBm" },
  sinr: { label: "SINR", unit: "dB" },
  throughput: { label: "吞吐量", unit: "Mbps" },
};
export type LinkMetric = keyof typeof LINK_METRICS;

function means(records: MetricRecord[]) {
  return Object.fromEntries((Object.keys(METRIC_META) as MetricKey[]).map(metric => {
    const values = records.map(record => record[metric]).filter(Number.isFinite);
    return [metric, values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null];
  })) as Record<MetricKey, number | null>;
}
export function getUEDetails(data: DashboardData, id: string) {
  const records = data.metrics.filter(record => record.ueId === id);
  const history = getUEHistory(records, id);
  return {
    node: data.nodes.find(node => node.type === "ue" && node.id === id),
    latest: history.at(-1) ?? null,
    recordCount: records.length,
    links: data.links.filter(link => link.target === id),
    means: means(records),
  };
}
export function getAPDetails(data: DashboardData, id: string) {
  const records = data.metrics.filter(record => record.apId === id);
  const latest = getLatestFrame(data.metrics);
  return {
    node: data.nodes.find(node => node.type === "ap" && node.id === id),
    links: data.links.filter(link => link.source === id),
    recordCount: records.length,
    means: means(records),
    latestTime: latest.metrics[0]?.backendTime ?? null,
    latestServingUEs: latest.metrics.filter(record => record.apId === id),
  };
}
export function getLinkDetails(data: DashboardData, link: NetworkLink) {
  const ueRecords = data.metrics.filter(record => record.ueId === link.target);
  const records = ueRecords.filter(record => record.apId === link.source);
  const ordered = records.filter(record => Number.isFinite(Date.parse(record.timestamp)))
    .sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp));
  return {
    link, recordCount: records.length, ueRecordCount: ueRecords.length,
    recordShare: ueRecords.length ? records.length / ueRecords.length : null,
    firstTime: ordered[0]?.backendTime ?? null,
    lastTime: ordered.at(-1)?.backendTime ?? null,
  };
}
export function linkMetricRange(links: NetworkLink[], metric: LinkMetric) {
  const values = links.map(link => link[metric]).filter(Number.isFinite);
  return values.length ? { min: Math.min(...values), max: Math.max(...values) } : null;
}
export function linkMetricColor(value: number, range: { min: number; max: number } | null) {
  if (!Number.isFinite(value) || !range) return "#67768a";
  const colors = ["#2e69d2", "#36b7f3", "#27d7d2", "#58edc6"];
  if (range.min === range.max) return colors[1];
  const fraction = Math.max(0, Math.min(1, (value - range.min) / (range.max - range.min)));
  return colors[Math.min(3, Math.floor(fraction * colors.length))];
}
