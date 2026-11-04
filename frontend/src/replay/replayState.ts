import { toEpoch } from "../services/backend";
import type { QueryScope } from "../types/backend";
import type { ReplayConfig } from "./config";

export interface ReplayState {
  startMs: number; endMs: number; playbackStartMs: number; currentMs: number;
  referenceMinutes: number; customStartMs: number | null;
  ready: boolean; playing: boolean; locked: boolean;
  speed: number; loop: boolean; epoch: number; realSecondsPerMinute: number;
  viewMode: "live" | "analysis" | "history"; viewMs: number; viewRevision: number;
}
export type ReplayAction =
  | { type: "ready" | "unready" | "play" | "pause" | "restart" | "freeze" | "history" | "live" }
  | { type: "tick"; elapsedMs: number; loadedThroughMs?: number }
  | { type: "seek"; timeMs: number }
  | { type: "select-range"; startMs: number; endMs: number }
  | { type: "history-range"; startMs: number; endMs: number }
  | { type: "forecast-analysis"; startMs: number; endMs: number }
  | { type: "reference"; minutes: number }
  | { type: "reference-start"; timeMs: number }
  | { type: "speed"; value: number }
  | { type: "loop" | "lock"; value: boolean };

export function initialReplayState(config: ReplayConfig): ReplayState {
  const startMs = toEpoch(config.start), endMs = toEpoch(config.end);
  const playbackStartMs = toEpoch(config.playbackStart ?? config.start), speed = config.initialSpeed ?? 1;
  if (playbackStartMs < startMs || playbackStartMs > endMs || !Number.isFinite(speed) || speed <= 0 || endMs < startMs || !Number.isFinite(config.realSecondsPerMinute) || config.realSecondsPerMinute <= 0
    || !Number.isFinite(config.historyMinutes) || config.historyMinutes <= 0)
    throw new Error("回放配置的时间边界、播放间隔或历史窗口无效");
  return { startMs, endMs, playbackStartMs, currentMs: playbackStartMs, referenceMinutes: config.historyMinutes, customStartMs: null, ready: false, playing: false,
    locked: false, speed, loop: true, epoch: 0, realSecondsPerMinute: config.realSecondsPerMinute,
    viewMode: "live", viewMs: playbackStartMs, viewRevision: 0 };
}

export function viewTime(state: ReplayState): number {
  return state.viewMode === "live" ? state.currentMs : state.viewMs;
}
export function replayContextKey(state: ReplayState): string {
  return `${state.epoch}:${state.viewRevision}`;
}

export function replayReducer(state: ReplayState, action: ReplayAction): ReplayState {
  if (action.type === "lock") return { ...state, locked: action.value };
  if (action.type === "live") return state.viewMode === "live" && !state.locked ? state
    : { ...state, viewMode: "live", viewMs: state.currentMs, customStartMs: null, locked: false, viewRevision: state.viewRevision + 1 };
  // 锁的是分析上下文；主时钟tick仍须运行，跨轮会废弃旧上下文。
  if (state.locked && ["seek", "history", "freeze", "restart", "reference", "reference-start", "select-range", "history-range", "forecast-analysis"].includes(action.type)) return state;
  switch (action.type) {
    case "unready": return { ...state, ready: false, playing: false, locked: false,
      viewMode: "live", viewMs: state.currentMs, viewRevision: state.viewRevision + 1 };
    case "ready": return { ...state, ready: true, playing: state.endMs > state.startMs };
    case "play": return state.ready && state.endMs > state.startMs ? { ...state, playing: true } : state;
    case "pause": return state.playing ? { ...state, playing: false } : state;
    case "speed": return Number.isFinite(action.value) && action.value > 0 ? { ...state, speed: action.value } : state;
    case "loop": return { ...state, loop: action.value };
    case "restart": return { ...state, currentMs: state.playbackStartMs, customStartMs: null, playing: state.ready && state.endMs > state.startMs,
      epoch: state.epoch + 1, viewMode: "live", viewMs: state.playbackStartMs, viewRevision: state.viewRevision + 1, locked: false };
    case "freeze": return !state.ready || state.viewMode !== "live" ? state : { ...state,
      viewMode: "analysis", viewMs: Math.floor(state.currentMs / 1000) * 1000, viewRevision: state.viewRevision + 1 };
    case "history": return !state.ready || state.viewMode === "history" ? state : { ...state,
      viewMode: "history", viewMs: Math.floor(viewTime(state) / 1000) * 1000, viewRevision: state.viewRevision + 1 };
    case "seek": {
      if (!state.ready || state.viewMode !== "history" || !Number.isFinite(action.timeMs)) return state;
      const viewMs = Math.max(state.startMs, Math.min(Math.floor(state.currentMs / 1000) * 1000, Math.floor(action.timeMs / 1000) * 1000));
      return viewMs === state.viewMs ? state : { ...state, viewMs, customStartMs: state.customStartMs !== null && state.customStartMs > viewMs ? null : state.customStartMs, viewRevision: state.viewRevision + 1 };
    }
    case "forecast-analysis": {
      if (!state.ready || state.viewMode === "history" || !Number.isFinite(action.startMs) || !Number.isFinite(action.endMs)) return state;
      const start = Math.floor(action.startMs / 1000) * 1000, end = Math.floor(action.endMs / 1000) * 1000;
      if (start < state.startMs || start > end || end > Math.min(state.endMs, state.currentMs)) return state;
      return { ...state, viewMode: "analysis", viewMs: end, customStartMs: start, viewRevision: state.viewRevision + 1 };
    }
    case "select-range":
    case "history-range": {
      if (!state.ready || !Number.isFinite(action.startMs) || !Number.isFinite(action.endMs)) return state;
      if (action.type === "history-range" && state.viewMode !== "history") return state;
      const start = Math.floor(action.startMs / 1000) * 1000, end = Math.floor(action.endMs / 1000) * 1000;
      const knownEnd = Math.min(state.endMs, Math.floor(state.currentMs / 1000) * 1000);
      if (start < state.startMs || start > end || end > knownEnd) return state;
      if (action.type === "history-range" && state.customStartMs === start && state.viewMs === end) return state;
      return { ...state, viewMs: end, customStartMs: start,
        viewMode: action.type === "history-range" || end < knownEnd ? "history" : "analysis", viewRevision: state.viewRevision + 1 };
    }
    case "reference": {
      if (!state.ready || !Number.isFinite(action.minutes) || action.minutes <= 0) return state;
      return action.minutes === state.referenceMinutes && state.customStartMs === null ? state
        : { ...state, referenceMinutes: action.minutes, customStartMs: null, viewRevision: state.viewRevision + 1 };
    }
    case "reference-start": {
      if (!state.ready || state.viewMode === "live" || !Number.isFinite(action.timeMs)) return state;
      const start = Math.floor(action.timeMs / 1000) * 1000, end = Math.floor(viewTime(state) / 1000) * 1000;
      if (start < state.startMs || start > end) return state;
      return state.customStartMs === start ? state : { ...state, customStartMs: start, viewRevision: state.viewRevision + 1 };
    }
    case "tick": {
      if (!state.playing || !state.ready || !Number.isFinite(action.elapsedMs) || action.elapsedMs <= 0) return state;
      // 先显示终点，下一次 tick 才开始新周期；大倍速不会跳过最后一帧。
      if (state.currentMs >= state.endMs) return state.loop
        ? { ...state, currentMs: state.playbackStartMs, customStartMs: null, epoch: state.epoch + 1,
          viewMode: "live", viewMs: state.playbackStartMs, viewRevision: state.viewRevision + 1, locked: false }
        : { ...state, playing: false };
      const currentMs = Math.min(state.endMs, action.loadedThroughMs ?? state.endMs,
        state.currentMs + action.elapsedMs * 60 / state.realSecondsPerMinute * state.speed);
      if (currentMs <= state.currentMs) return state;
      return { ...state, currentMs, playing: currentMs < state.endMs || state.loop };
    }
  }
}

export function formatReplayTime(timeMs: number): string {
  return new Date(timeMs + 8 * 3600_000).toISOString().slice(0, 19).replace("T", " ");
}
export function replayScope(config: ReplayConfig, currentMs: number): QueryScope {
  const endMs = Math.max(toEpoch(config.start), Math.min(toEpoch(config.end), Math.floor(currentMs / 1000) * 1000));
  return { scenario: config.scenario,
    start: formatReplayTime(Math.max(toEpoch(config.start), endMs - config.historyMinutes * 60_000)),
    end: formatReplayTime(endMs) };
}

// 唯一的查看范围：主图/趋势/AI请求/空间证据共用，结束不超过已到达时刻。
export function viewScope(config: ReplayConfig, state: ReplayState): QueryScope {
  const end = Math.max(state.startMs, Math.min(state.currentMs, state.endMs, Math.floor(viewTime(state) / 1000) * 1000));
  const start = state.customStartMs ?? end - state.referenceMinutes * 60_000;
  return { scenario: config.scenario, start: formatReplayTime(Math.max(state.startMs, Math.min(start, end))), end: formatReplayTime(end) };
}
