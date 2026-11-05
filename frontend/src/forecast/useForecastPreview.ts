import { record, forecastRecordIds } from '../history/store';
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
/** 入口摘要的刷新粒度：按**1 个模拟分钟**检查（复查 P1-3：5 分钟太粗，可能错过提前信号） */
const summaryStepMs = 60_000;

/** 不依赖弹窗的风险摘要（复查 P1-1）：入口必须能自己更新，且失败时不能冒充"无风险"。 */
export interface ForecastSummary {
  status: 'idle' | 'loading' | 'ready' | 'error';
  alertCount: number;
  baseTime: string;
  /** 结果是按哪个主时钟时刻算的，用于展示"基准时间"与判断过期 */
  checkedAt: number;
  /**
   * 后台重算中（已有上一次结果）。复查（用户实测）：不要因为重算就把标签切回"检查中…"，
   * 否则 4× 播放时每 15 秒闪一次；保留上一次结论，静默更新即可。
   */
  refreshing?: boolean;
  error: string;
}

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
  // 入口摘要独立于弹窗：自己的请求序号与中止器，避免与弹窗请求互相打断
  const [summary, setSummary] = useState<ForecastSummary>({ status: 'idle', alertCount: 0, baseTime: '', checkedAt: 0, error: '' });
  const summarySerial = useRef(0), summaryAbort = useRef<AbortController | null>(null), summarySlot = useRef(-1);
  // 进行中标记：播放很快时避免"下一个分钟立刻中断上一个请求"导致永远 loading
  const summaryInFlight = useRef(false);
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
    // 保留原问题与关注时段（复查 P1-2）：改预测时长/自动刷新时，调用方通常只传
    // horizonMinutes，早期实现会把 question / targetWindow 丢掉，于是"预测 14:35—14:40"
    // 改成长度后变成"分析整个预测范围"，用户看到的对象与问题不再对应。
    const previous = sessionRef.current?.request;
    const inheritedQuestion = options.question ?? previous?.question;
    const inheritedWindow = options.targetWindow ?? previous?.targetWindow;
    const request: ForecastRequest = {
      ...(FORECAST_CONFIG.responseProfile === 'main4' ? minuteForecastRequest(scope, minutes) : {
        scenario: config.scenario, baseTime: scope.end, history: { start: scope.start, end: scope.end }, horizonMinutes: minutes,
      }),
      ...(inheritedWindow ? { targetWindow: inheritedWindow } : {}),
      ...(inheritedQuestion ? { question: inheritedQuestion } : {}),
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
      if (valid()) {
        // 复查 P2-4：**只有正式提问**才建"问题记录"；自动刷新/改时长/预览不新增独立问题
        // （否则连续刷新会刷出同名记录、吃掉"最近 6 次/100 条"名额）。
        // 同一问题重复刷新（refresh/改时长）也不算新问题，避免"同名记录"堆积。
        // 快照仍保留在本轮会话状态里（last/result），作为证据可回看。
        if (request.question && request.question !== previous?.question) {
          const rid = record({ kind: 'forecast', question: request.question,
            scope: { scenario: request.scenario, start: request.history.start, end: request.history.end },
            analysisTime: result.baseTime,
            masterTime: current.currentMs ? new Date(current.currentMs + 8 * 3600000).toISOString().slice(0, 19).replace('T', ' ') : undefined,
            mode: current.viewMode, status: 'completed', payload: { request, result } });
          forecastRecordIds.set(result, rid);
        }
        setState(previous => ({ ...previous, result, pending: false, last: { key, result } }));
      }
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

  /**
   * 入口摘要的后台刷新（复查 P1-1/P1-3）：不等弹窗打开，按 1 个模拟分钟检查一次。
   * - 只在"跟随当前 + 主时钟播放"时运行，历史/固定时刻立即停；
   * - 不写问题记录（那是正式提问的事，见复查 P2-4）；
   * - 失败时把状态置为 error，界面必须显示"更新失败"，不能显示成"无风险"。
   */
  const summaryKey = replayContextKey(clock);
  useEffect(() => {
    // 复查（用户实测反馈）：入口摘要原先只在"跟随播放"时运行，一旦固定时刻/进入分析，
    // 摘要就停在上一次的值，界面却仍显示"N 项（未检出风险）"——与弹窗按冻结基准算出的结果矛盾。
    // 现在：固定时刻也检查一次（时间不前进 → 同一 slot 只发一次请求），保证入口与弹窗同基准。
    const liveRun = !clock.locked && clock.viewMode === 'live' && clock.playing && follow && !visible;
    const frozenRun = (clock.locked || clock.viewMode === 'analysis') && !visible;
    const canRun = clock.ready && (liveRun || frozenRun);
    if (!canRun) { summarySlot.current = -1; return; }
    const currentSlot = Math.floor(clock.currentMs / summaryStepMs);
    if (currentSlot === summarySlot.current) return;
    // 复查（用户实测：入口长期停在"检查中…"）：原来每到新的一模拟分钟就 abort 上一轮，
    // 被中断的那轮直接 return，状态就永远停在 loading。播放 4×（15 秒/分钟）时请求还没回来
    // 就被下一轮打断，于是"时间都到了还什么都没显示"。
    // 现在：**不打断进行中的检查**，完成后再取最新一轮；并且无论成功/失败/被中断，状态都要收敛。
    if (summaryInFlight.current) return;
    summarySlot.current = currentSlot;
    const scope = viewScope(config, clock);
    const minutes = horizonRef.current;
    const request: ForecastRequest = {
      ...(FORECAST_CONFIG.responseProfile === 'main4' ? minuteForecastRequest(scope, minutes) : {
        scenario: config.scenario, baseTime: scope.end, history: { start: scope.start, end: scope.end }, horizonMinutes: minutes,
      }),
    };
    const abort = new AbortController(); summaryAbort.current = abort;
    const id = ++summarySerial.current;
    summaryInFlight.current = true;
    // 已有结果时不退回 loading（否则标签会周期性闪烁），只标记"后台刷新中"
    setSummary(previous => (previous.status === 'ready'
      ? { ...previous, refreshing: true }
      : { status: 'loading', alertCount: 0, baseTime: '', checkedAt: clock.currentMs, error: '' }));
    const settle = (next: ForecastSummary) => {
      if (summarySerial.current !== id) return;
      summaryInFlight.current = false;
      setSummary({ ...next, refreshing: false });
    };
    void loadForecast(request, abort.signal).then(result => {
      if (abort.signal.aborted) return;
      settle({ status: 'ready', alertCount: result.alerts.length, baseTime: result.baseTime,
        checkedAt: clock.currentMs, error: '' });
    }).catch((reason: unknown) => {
      if (abort.signal.aborted) { summaryInFlight.current = false; return; }
      const conflict = reason instanceof Error && /结束时间|基准|一致的/i.test(reason.message);
      settle({ status: 'error', alertCount: 0, baseTime: '', checkedAt: clock.currentMs,
        error: conflict ? '预测范围与请求不一致（可能无数据或基准时刻已变）'
          : (reason instanceof Error ? reason.message : '预测检查失败') });
    });
  }, [clock, config, follow, visible, summaryKey]);

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
    summary,
    open, refresh, close, follow, toggleFollow, horizonMinutes, setHorizonMinutes,
    followStepMinutes: FORECAST_LIMITS.followStepMinutes,
  };
}
