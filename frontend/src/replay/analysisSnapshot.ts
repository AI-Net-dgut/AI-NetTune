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
export { needsFutureForecast } from './questionIntent';
