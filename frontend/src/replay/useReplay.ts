import { useCallback, useContext, useEffect, useReducer, useRef } from "react";
import { WorkbenchActivityContext } from "./WorkbenchActivityContext";
import type { ReplayConfig } from "./config";
import { initialReplayState, replayReducer } from "./replayState";
import type { ReplayAction } from "./replayState";

export default function useReplay(config: ReplayConfig, coveredThrough?: (at: number) => number) {
  const active = useContext(WorkbenchActivityContext);
  const [state, dispatch] = useReducer(replayReducer, config, initialReplayState);
  const latest = useRef(state);
  const act = useCallback((action: ReplayAction) => {
    // 事件之间同步更新：历史边界与分析锁不等待下一次React渲染。
    latest.current = replayReducer(latest.current, action);
    dispatch(action);
  }, []);
  useEffect(() => {
    // 站内切页期间暂挂计时；返回时从原时刻继续，不补算离开期间的时间。
    if (!active || !state.playing) return;
    let previous = Date.now();
    const timer = setInterval(() => {
      const now = Date.now();
      // 切出标签页后不追赶大量时间，以免演示跳过异常片段。
      act({ type: "tick", elapsedMs: Math.min(2000, Math.max(0, now - previous)), loadedThroughMs: coveredThrough?.(latest.current.currentMs) }); previous = now;
    }, 1000);
    return () => clearInterval(timer);
  }, [active, state.playing, act, coveredThrough]);
  const readClock = useCallback(() => latest.current, []);
  return { state, act, latest, readClock };
}
