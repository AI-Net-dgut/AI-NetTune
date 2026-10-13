import type { QueryScope } from "../types/backend";

export interface ReplayConfig extends QueryScope {
  playbackStart?: string;
  initialSpeed?: number;
  realSecondsPerMinute: number;
  historyMinutes: number;
}

// 数据边界与演示起点分开；后端需读取这一份七天校园数据。
// 起点之前是已知历史背景，起点之后仅随主时钟逐步展示。
export const REPLAY_CONFIG: ReplayConfig = {
  scenario: "校园",
  start: "2026-01-01 00:00:00",
  end: "2026-01-07 23:59:00",
  playbackStart: "2026-01-02 09:15:00",
  initialSpeed: 4,
  realSecondsPerMinute: 10,
  historyMinutes: 60,
};
export const REPLAY_SPEEDS = [1, 4, 16, 64] as const;
