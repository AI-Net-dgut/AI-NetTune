import { useCallback, useEffect, useRef, useState } from 'react';
import type { ReplayState } from '../replay/replayState';
import { replayContextKey, viewScope } from '../replay/replayState';
import type { ReplayConfig } from '../replay/config';
import { FORECAST_CONFIG, FORECAST_LIMITS } from './config';
import { loadForecast } from './api';
import { minuteForecastRequest } from './main4Adapter';
import type { ForecastRequest, ForecastResult } from './types';

export interface ForecastOpenOptions {
  horizonMinutes?: number;
  targetWindow?: ForecastRequest['targetWindow'];
  question?: string;
  follow?: boolean;
}
interface Session { request: ForecastRequest; key: string }
interface State {
  session: Session | null; result: ForecastResult | null; pending: boolean; error: string;
  last: { key: string; result: ForecastResult } | null;
}
const initial: State = { session: null, result: null, pending: false, error: '', last: null };
const stepMs = FORECAST_LIMITS.followStepMinutes * 60_000;

export default function useForecastPreview({ clock, config, readClock }: {
  clock: ReplayState; config: ReplayConfig; readClock: () => ReplayState;
}) {
  const [state, setState] = useState<State>(initial);
  const [follow, setFollow] = useState(true);
  const [horizonMinutes, setHorizon] = useState(FORECAST_CONFIG.horizonMinutes);
  const horizonRef = useRef(horizonMinutes);
  const controller = useRef<AbortController | null>(null);
  const serial = useRef(0), openedSlot = useRef(Math.floor(clock.currentMs / stepMs));
  const sessionRef = useRef<Session | null>(null);
  const close = useCallback(() => {
    serial.current++; controller.current?.abort(); controller.current = null; sessionRef.current = null;
    setState(previous => ({ ...previous, session: null, result: null, pending: false, error: '' }));
  }, []);

  const open = useCallback((options: ForecastOpenOptions = {}) => {
    const current = readClock();
    if (!current.ready || current.locked || current.viewMode === 'history') return;
    const minutes = options.horizonMinutes ?? horizonRef.current;
    if (!Number.isInteger(minutes) || minutes < 1 || minutes > FORECAST_LIMITS.maxHorizonMinutes) return;
    horizonRef.current = minutes; setHorizon(minutes);
    if (options.follow !== undefined) setFollow(options.follow);
    openedSlot.current = Math.floor(current.currentMs / stepMs);
    const scope = viewScope(config, current), key = replayContextKey(current);
    const request: ForecastRequest = {
      ...(FORECAST_CONFIG.responseProfile === 'main4' ? minuteForecastRequest(scope, minutes) : {
        scenario: config.scenario, baseTime: scope.end, history: { start: scope.start, end: scope.end }, horizonMinutes: minutes,
      }),
      ...(options.targetWindow ? { targetWindow: options.targetWindow } : {}),
      ...(options.question ? { question: options.question } : {}),
    };
    controller.current?.abort();
    const abort = new AbortController(); controller.current = abort;
    const session = { request, key }; sessionRef.current = session;
    const id = ++serial.current;
    setState(previous => ({
      ...previous, session, pending: true, error: '',
      // 自动更新保留上一轮有效图；其基准时刻由result自身提供，不能冒充新请求的结果。
      result: previous.session?.key === key && previous.session.request.horizonMinutes === minutes
        && previous.session.request.question === request.question ? previous.result : null,
    }));
    const valid = () => serial.current === id && !abort.signal.aborted
      && replayContextKey(readClock()) === key && readClock().viewMode !== 'history';
    void loadForecast(request, abort.signal).then(result => {
      if (valid()) setState(previous => ({ ...previous, result, pending: false, last: { key, result } }));
    }).catch(reason => {
      if (valid()) setState(previous => ({ ...previous, pending: false, error: reason instanceof Error ? reason.message : '预测加载失败' }));
    });
  }, [config, readClock]);

  const key = replayContextKey(clock);
  const visible = Boolean(state.session && state.session.key === key && clock.viewMode !== 'history' && clock.ready);
  useEffect(() => {
    if (state.session && !visible) {
      serial.current++; controller.current?.abort(); controller.current = null; sessionRef.current = null;
    }
  }, [state.session, visible]);
  useEffect(() => () => { serial.current++; controller.current?.abort(); }, []);

  const slot = Math.floor(clock.currentMs / stepMs);
  useEffect(() => {
    // 只更新当前可见会话；旧上下文的session不能让已经关闭的弹窗自行重开。
    if (!visible || !follow || state.pending || clock.locked || clock.viewMode !== 'live' || !clock.playing) return;
    if (slot > openedSlot.current) open();
  }, [visible, follow, slot, state.pending, clock.locked, clock.viewMode, clock.playing, open]);

  const setHorizonMinutes = useCallback((minutes: number) => {
    if (!Number.isInteger(minutes) || minutes < 1 || minutes > FORECAST_LIMITS.maxHorizonMinutes) return;
    horizonRef.current = minutes; setHorizon(minutes);
    if (sessionRef.current?.key === replayContextKey(readClock())) open({ horizonMinutes: minutes });
  }, [open, readClock]);
  const refresh = useCallback(() => {
    const current = sessionRef.current;
    open(current ? { horizonMinutes: current.request.horizonMinutes, targetWindow: current.request.targetWindow, question: current.request.question } : {});
  }, [open]);
  const toggleFollow = useCallback(() => setFollow(value => !value), []);
  const lastResult = state.last?.key === key && clock.viewMode !== 'history' ? state.last.result : null;
  return {
    session: visible ? state.session : null, result: visible ? state.result : null,
    pending: visible && state.pending, error: visible ? state.error : '', lastResult,
    open, refresh, close, follow, toggleFollow, horizonMinutes, setHorizonMinutes,
    followStepMinutes: FORECAST_LIMITS.followStepMinutes,
  };
}
