import { useCallback, useEffect, useReducer, useRef } from "react";
import type { ReplayConfig } from "./config";
import { initialReplayState, replayReducer } from "./replayState";
import type { ReplayAction } from "./replayState";

export default function useReplay(config: ReplayConfig) {
  const [state, dispatch] = useReducer(replayReducer, config, initialReplayState);
  const latest = useRef(state);
  const act = useCallback((action: ReplayAction) => {
    // 事件之间同步更新：历史边界与分析锁不等待下一次React渲染。
    latest.current = replayReducer(latest.current, action);
    dispatch(action);
  }, []);
  useEffect(() => {
    if (!state.playing) return;
    let previous = Date.now();
    const timer = setInterval(() => {
      const now = Date.now();
      // 切出标签页后不追赶大量时间，以免演示跳过异常片段。
      act({ type: "tick", elapsedMs: Math.min(2000, Math.max(0, now - previous)) }); previous = now;
    }, 1000);
    return () => clearInterval(timer);
  }, [state.playing, act]);
  const readClock = useCallback(() => latest.current, []);
  return { state, act, latest, readClock };
}
