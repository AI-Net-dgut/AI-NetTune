import { METRIC_META } from "../types/backend";
import type { DashboardData, MetricKey, MetricRecord, NetworkLink } from "../types/backend";
import { getLatestFrame, getUEHistory } from "./metrics";

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
