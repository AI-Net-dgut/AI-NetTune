import { useEffect, useReducer, useState } from "react";
import { fetchSpatialMap } from "../services/backend";
import type { QueryScope, SpatialMapData } from "../types/backend";

interface SpatialState { key: string; data: SpatialMapData | null; loading: boolean; error: string }
const empty: SpatialState = { key: "", data: null, loading: false, error: "" };

// 监测地图与AI证据各自调用此hook，结果归属各自的时间窗口；旧响应不会串入新窗口。
export default function useScopedSpatial(scope: QueryScope | null) {
  const [state, update] = useReducer((_state: SpatialState, next: SpatialState) => next, empty);
  const [retryCount, retry] = useState(0);
  const key = scope ? `${scope.scenario}|${scope.start}|${scope.end}` : "";
  useEffect(() => {
    if (!key) return;
    const [scenario, start, end] = key.split("|");
    const controller = new AbortController(); let active = true;
    update({ key, data: null, loading: true, error: "" });
    fetchSpatialMap({ scenario, start, end }, controller.signal).then(data => {
      if (active) update({ key, data, loading: false, error: "" });
    }).catch(reason => {
      if (active && !controller.signal.aborted) update({ key, data: null, loading: false,
        error: reason instanceof Error ? reason.message : "空间估计加载失败" });
    });
    return () => { active = false; controller.abort(); };
  }, [key, retryCount]);
  const current = key && state.key === key ? state : { ...empty, loading: Boolean(key) };
  return { data: current.data, loading: current.loading, error: current.error, retry: () => retry(value => value + 1) };
}
