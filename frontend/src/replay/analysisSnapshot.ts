import type { DashboardData, MetricRecord, NetworkNode } from "../types/backend";
import type { ReplayConfig } from "./config";
import type { ReplayState } from "./replayState";
import { buildReplayDashboard } from "./replayData";
import { viewScope } from "./replayState";

export interface AnalysisSnapshot { epoch: number; viewRevision: number; data: DashboardData; focusedApId: string | null }
export function captureAnalysis(records: MetricRecord[], config: ReplayConfig, clock: ReplayState,
  knownAPs: NetworkNode[], focusedApId: string | null): AnalysisSnapshot {
  return { epoch: clock.epoch, viewRevision: clock.viewRevision,
    data: buildReplayDashboard(records, viewScope(config, clock), knownAPs), focusedApId };
}
export function needsFutureForecast(message: string): boolean {
  // “预计效果”属于方案评估，不因“预计”二字误归到未来预警。
  return /预测|预警|未来|将来|预防|明天|后天|今晚|明早|下周|下个月|下一(?:小时|分钟|天|周)|(?:\d+(?:\.\d+)?|[一二三四五六七八九十百半两]+)\s*(?:秒|分钟|小时|天|周|个月)\s*(?:之)?后|接下来(?:的)?\s*(?:\d+|[一二三四五六七八九十百半两]+)\s*(?:秒|分钟|小时|天|周)|接下来.*(?:网络|负载|拥塞|信号|覆盖|吞吐|时延|风险|趋势|会)/.test(message);
}
