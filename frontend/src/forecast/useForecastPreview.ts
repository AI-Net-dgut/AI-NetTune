import { useCallback, useEffect, useRef, useState } from "react";
import type { ReplayState } from "../replay/replayState";
import { replayContextKey, viewScope } from "../replay/replayState";
import type { ReplayConfig } from "../replay/config";
import { FORECAST_CONFIG } from "./config";
import { loadForecast } from "./api";
import type { ForecastRequest, ForecastResult } from "./types";

interface Session { request: ForecastRequest; key: string }
interface State { session: Session | null; result: ForecastResult | null; pending: boolean; error: string; last: { key: string; result: ForecastResult } | null }
const initial: State = { session: null, result: null, pending: false, error: "", last: null };
export default function useForecastPreview({ clock, config, readClock }: { clock: ReplayState; config: ReplayConfig; readClock: () => ReplayState }) {
  const [state, setState] = useState<State>(initial);
  const controller = useRef<AbortController | null>(null), serial = useRef(0);
  const close = useCallback(() => {
    serial.current++; controller.current?.abort(); controller.current = null;
    setState(previous => ({ ...previous, session: null, result: null, pending: false, error: "" }));
  }, []);
  const open = useCallback(() => {
    const current = readClock();
    if (!current.ready || current.locked || current.viewMode === "history") return;
    const scope = viewScope(config, current), key = replayContextKey(current);
    const request: ForecastRequest = { scenario: config.scenario, baseTime: scope.end,
      history: { start: scope.start, end: scope.end }, horizonMinutes: FORECAST_CONFIG.horizonMinutes };
    controller.current?.abort(); const abort = new AbortController(); controller.current = abort;
    const id = ++serial.current; setState(previous => ({ ...previous, session: { request, key }, result: null, pending: true, error: "" }));
    const valid = () => serial.current === id && !abort.signal.aborted && replayContextKey(readClock()) === key && readClock().viewMode !== "history";
    void loadForecast(request, abort.signal).then(result => {
      if (valid()) setState(previous => ({ ...previous, result, pending: false, last: { key, result } }));
    }).catch(reason => {
      if (valid()) setState(previous => ({ ...previous, pending: false, error: reason instanceof Error ? reason.message : "预测加载失败" }));
    });
  }, [config, readClock]);
  const key = replayContextKey(clock);
  const visible = Boolean(state.session && state.session.key === key && clock.viewMode !== "history" && clock.ready);
  useEffect(() => {
    if (state.session && (state.session.key !== key || clock.viewMode === "history" || !clock.ready)) {
      serial.current++; controller.current?.abort(); controller.current = null;
    }
  }, [state.session, key, clock.viewMode, clock.ready]);
  useEffect(() => () => { serial.current++; controller.current?.abort(); }, []);
  const lastResult = state.last?.key === key && clock.viewMode !== "history" ? state.last.result : null;
  return { session: visible ? state.session : null, result: visible ? state.result : null,
    pending: visible && state.pending, error: visible ? state.error : "", lastResult, open, refresh: open, close };
}
