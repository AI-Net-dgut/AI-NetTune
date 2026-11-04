import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { loadDashboard, scopeLabel, toEpoch } from "../../services/backend";
import type { MetricRecord, NetworkNode } from "../../types/backend";
import { resolveHistoryQuestion } from "../../replay/historyQuestion";
import useNetworkQuery from "../../hooks/useNetworkQuery";
import { initialQueryState } from "../../chat/queryState";
import QueryPanel from "../../chat/QueryPanel";
import DataObservation from "../../components/observation/DataObservation";
import { REPLAY_CONFIG } from "../../replay/config";
import { formatReplayTime, viewScope, replayContextKey, viewTime } from "../../replay/replayState";
import type { ReplayAction } from "../../replay/replayState";
import { buildReplayDashboard, loadReplayRecords } from "../../replay/replayData";
import { SIM_CONFIG_CHANGED, type SimConfigChangeDetail } from "../../components/management/SimConfigBar";
import { captureAnalysis } from "../../replay/analysisSnapshot";
import type { AnalysisSnapshot } from "../../replay/analysisSnapshot";
import useReplay from "../../replay/useReplay";
import useScopedSpatial from "../../replay/useScopedSpatial";
import ReplayControls from "../../replay/ReplayControls";
import HistoryControls from "../../replay/HistoryControls";
import ReferenceWindowControls from "../../replay/ReferenceWindowControls";
import CaseEventButtons from "../../replay/CaseEventButtons";
import type { ReplayConfig } from "../../replay/config";
import useForecastPreview from "../../forecast/useForecastPreview";
import ForecastDialog from "../../forecast/ForecastDialog";
import ForecastReview from "../../forecast/ForecastReview";
import { FORECAST_CONFIG, FORECAST_LIMITS } from "../../forecast/config";
import type { ForecastHandoff } from "../../forecast/types";
import { resolveForecastQuestion } from '../../forecast/questionTime';
import { minuteForecastRequest } from '../../forecast/main4Adapter';
import { forecastQuestionKind } from '../../forecast/interaction';
import "./WorkbenchPage.css";
import "./BackendIntegration.css";
import { AnalysisContextBanner } from './WorkspacePresentation';
import { WorkspacePresentationContext } from './WorkspacePresentationContext';
import type { AnalysisContext } from './WorkspacePresentation';
import type { WorkspaceTab } from './WorkspacePresentationContext';
import './WorkbenchTabs.css';

interface ReplaySource {
  status: "loading" | "ready" | "error" | "empty";
  records: MetricRecord[]; aps: NetworkNode[]; error: string; notice: string;
}

export default function WorkbenchPage({ config = REPLAY_CONFIG }: { config?: ReplayConfig } = {}) {
  const replay = useReplay(config);
  const { state: clock, act: dispatchClock, latest, readClock } = replay;
  const contextKey = replayContextKey(clock);
  const [source, setSource] = useReducer((_previous: ReplaySource, next: ReplaySource) => next,
    { status: "loading", records: [], aps: [], error: "", notice: "" });
  const [reloadCount, reload] = useState(0);
  // 仿真配置变更后重新取数用的计数（与 reloadCount 分开：不能重置时钟，也不能回到演示起点）
  const [simRevision, setSimRevision] = useState(0);
  // 点案例后要预填进输入框的问题（只填不发送）
  const [caseQuestion, setCaseQuestion] = useState("");
  const [analysisNotice, setAnalysisNotice] = useState<{ key: string; text: string }>({ key: "", text: "" });
  const [focus, setFocus] = useState<{ contextKey: string; id: string | null }>({ contextKey, id: null });
  const focusedApId = focus.contextKey === contextKey ? focus.id : null;
  const [snapshot, setSnapshot] = useState<AnalysisSnapshot | null>(null);
  const snapshotRef = useRef<AnalysisSnapshot | null>(null);
  const query = useNetworkQuery();
  const forecast = useForecastPreview({ clock, config, readClock });
  const [forecastTransfer, setForecastTransfer] = useState<{ key: string; handoff: ForecastHandoff; revision: number } | null>(null);
  const forecastReviewSequence = useRef(0);
  const [forecastActivity, setForecastActivity] = useState({ key: '', pending: true });
  const reportForecastStatus = useCallback((key: string, pending: boolean) => setForecastActivity({ key, pending }), []);
  const [diagnosisHost, setDiagnosisHost] = useState<HTMLDivElement | null>(null);
  const [plansHost, setPlansHost] = useState<HTMLDivElement | null>(null);
  const [tabChoice, setTabChoice] = useState<{ context: string; analysis: string; tab: WorkspaceTab } | null>(null);
  const resetQuery = query.reset;
  const clearedContext = useRef(contextKey);
  const requestSerial = useRef(0);
  const clearContext = useCallback((key: string) => {
    clearedContext.current = key; requestSerial.current++;
    snapshotRef.current = null; setSnapshot(null); resetQuery(); setForecastTransfer(null);
  }, [resetQuery]);
  const act = useCallback((action: ReplayAction) => {
    const before = replayContextKey(readClock());
    dispatchClock(action);
    const after = replayContextKey(readClock());
    if (after !== before) clearContext(after);
  }, [dispatchClock, readClock, clearContext]);

  useEffect(() => {
    const controller = new AbortController(); let active = true;
    // simRevision 参与依赖：应用/回滚仿真配置后重新取数（保持观看时刻，不回到演示起点）
    void simRevision;
    loadReplayRecords(config, controller.signal).then(records => {
      if (!active) return;
      if (!records.length) {
        setSource({ status: "empty", records: [], aps: [], error: "", notice: "" }); return;
      }
      const loaded: ReplaySource = { status: "ready", records, aps: [], error: "", notice: "" };
      setSource(loaded); act({ type: "ready" });
      // 静态部署只从首个已有采样时刻读取；不使用预加载末尾的 UE 坐标/连接。
      const first = records[0].backendTime;
      void loadDashboard({ scenario: config.scenario, start: first, end: first }, controller.signal).then(result => {
        if (active) setSource({ ...loaded, aps: result?.nodes.filter(node => node.type === "ap") ?? [] });
      }).catch(() => {
        if (active) setSource({ ...loaded, notice: "静态部署暂未返回；主图使用已到达采样中的位置与连接。" });
      });
    }).catch(reason => {
      if (active && !controller.signal.aborted) setSource({ status: "error", records: [], aps: [],
        error: reason instanceof Error ? reason.message : "历史数据读取失败", notice: "" });
    });
    return () => { active = false; controller.abort(); };
  }, [reloadCount, simRevision, act, config]);

  // 仿真配置应用/回滚后：丢弃旧配置下的分析（结论、方案、证据都不再成立），
  // 保持当前观看时刻，然后重新取数。用事件解耦，避免三层 props 透传（复查 P0-3）。
  useEffect(() => {
    const onChanged = (event: Event) => {
      const detail = (event as CustomEvent<SimConfigChangeDetail>).detail;
      const key = replayContextKey(readClock());
      clearContext(key);                       // 取消在途请求 + 清旧分析与快照
      setAnalysisNotice({
        key,
        text: detail?.active
          ? `已应用仿真配置（修订 ${detail.revision}）：正在按新配置刷新数据与图表；旧结论已清空，请重新研判。`
          : `已回滚仿真配置（修订 ${detail?.revision ?? 0}）：正在恢复原始数据；旧结论已清空，请重新研判。`,
      });
      setSimRevision(value => value + 1);      // 触发重新取数（不重置时钟）
    };
    window.addEventListener(SIM_CONFIG_CHANGED, onChanged);
    return () => window.removeEventListener(SIM_CONFIG_CHANGED, onChanged);
  }, [clearContext, readClock]);

  // timer直接跨轮的情况也要清上下文；手动操作已同步清理，避免取消随后发起的新请求。
  useEffect(() => {
    if (clearedContext.current !== contextKey) clearContext(contextKey);
  }, [contextKey, clearContext]);

  const selectedTime = viewTime(clock);
  const scope = viewScope(config, clock);
  const scopeStart = scope.start, scopeEnd = scope.end, scopeScenario = scope.scenario;
  const viewData = useMemo(() => source.status === "ready"
    ? buildReplayDashboard(source.records, { scenario: scopeScenario, start: scopeStart, end: scopeEnd }, source.aps)
    : null, [source, scopeStart, scopeEnd, scopeScenario]);
  const activeSnapshot = snapshot?.epoch === clock.epoch && snapshot.viewRevision === clock.viewRevision ? snapshot : null;
  const data = activeSnapshot?.data ?? viewData;
  const visibleSpatial = useScopedSpatial(clock.viewMode !== "live" && data?.metrics.length ? scope : null);
  // 循环或跳转后的首次渲染就隐藏较晚时刻的回答，不等 effect 清理。
  const visibleQuery = activeSnapshot ? query : { ...query, ...initialQueryState };
  const currentTime = formatReplayTime(clock.currentMs);
  const displayedTime = formatReplayTime(selectedTime);
  const loading = source.status === "loading";

  async function submitAnalysis(message: string, preserveDiagnosis = false, retry = false) {
    let current = latest.current;
    if (!message.trim() || source.status !== "ready") return;
    // 用户要求：**有内容就能发**。跟随主时钟时在此自动固定时刻（无需先手动点固定），
    // 然后按冻结后的时刻取范围，保证"分析截至"等于用户点击瞬间。
    if (!current.locked && current.viewMode === "live") {
      act({ type: "freeze" });
      current = readClock();
    }
    if (forecastTransfer?.key === replayContextKey(current)) {
      setAnalysisNotice({ key: replayContextKey(current), text: '当前为预测风险研判；请先点击“切换到现存分析”，再查询当前或历史问题。' });
      return;
    }
    const sentMasterTime = formatReplayTime(current.currentMs);
    if (!preserveDiagnosis && !retry) {
      const future = resolveForecastQuestion(message, {
        knownTime: sentMasterTime,
        baseTime: FORECAST_CONFIG.responseProfile === "main4"
          ? minuteForecastRequest(viewScope(config, current), forecast.horizonMinutes).baseTime : viewScope(config, current).end,
        defaultHorizonMinutes: forecast.horizonMinutes, maxHorizonMinutes: FORECAST_LIMITS.maxHorizonMinutes,
      });
      if (future.kind === 'error') {
        setAnalysisNotice({ key: replayContextKey(current), text: `${future.message} 本次未发送，查看范围未改变。` }); return;
      }
      // 复合问法（"哪个 AP 最差，未来 3 小时会怎样"）不能抢先开预测弹窗：
      // 后端的工具链编排会先研判找对象、再做预测，并返回可渲染的步骤（核查清单第 5 条）。
      // 只有**纯未来**问题才走弹窗。
      const compound = ["最差", "最严重", "哪个 AP", "哪个ap", "哪里最"].some(k => message.includes(k));
      if (future.kind === 'forecast' && !compound) {
        if (current.viewMode === 'history') {
          setAnalysisNotice({ key: replayContextKey(current), text: '历史回顾期间不提供未来预测，请返回当前后再提问。' }); return;
        }
        if (current.viewMode === 'live') act({ type: 'freeze' });
        current = readClock();
        clearContext(replayContextKey(current));
        setAnalysisNotice({ key: replayContextKey(current), text: `已固定查看时刻并打开预测预警，预测未来${future.horizonMinutes}分钟。${future.targetWindow ? `关注：${future.targetWindow.start} — ${future.targetWindow.end}。` : ''}主时钟保持原播放状态。` });
        forecast.open({ horizonMinutes: future.horizonMinutes, targetWindow: future.targetWindow, question: message, follow: false });
        return;
      }
      const resolved = resolveHistoryQuestion(message, { scenario: config.scenario, masterTime: sentMasterTime, dataStart: config.start, dataEnd: config.end });
      if (resolved.kind === "error") {
        setAnalysisNotice({ key: replayContextKey(current), text: `${resolved.message} 本次问题未发送，查看范围未改变。` }); return;
      }
      if (resolved.kind === "range") {
        act({ type: "select-range", startMs: toEpoch(resolved.scope.start), endMs: toEpoch(resolved.scope.end) });
        current = latest.current;
        setAnalysisNotice({ key: replayContextKey(current), text: `已按问题切换到 ${resolved.scope.start} — ${resolved.scope.end}（北京时间）。${resolved.notice}` });
      } else if (current.viewMode === "live") return;
      else setAnalysisNotice({ key: replayContextKey(current), text: "" });
    } else if (current.viewMode === "live") return;
    if (current.viewMode === "live") return;
    const key = replayContextKey(current);
    // 可能恰好经历timer跨轮且尚未执行effect；先取消旧上下文，再开启本次请求。
    if (clearedContext.current !== key) clearContext(key);
    const previous = snapshotRef.current?.epoch === current.epoch && snapshotRef.current.viewRevision === current.viewRevision ? snapshotRef.current : null;
    const captured = (preserveDiagnosis || retry) && previous ? previous
      : captureAnalysis(source.records, config, current, source.aps, focus.contextKey === key ? focus.id : null);
    if (!captured.data.metrics.length) { setAnalysisNotice({ key, text: "指定范围暂无监测采样，未发送分析；可以调整范围或返回当前。" }); return; }
    setForecastTransfer(null);
    snapshotRef.current = captured; setSnapshot(captured);
    act({ type: "lock", value: true });
    const serial = ++requestSerial.current;
    if (!preserveDiagnosis && !retry && typeof document !== 'undefined') requestAnimationFrame(() =>
      document.getElementById('ain-tab-diagnosis')?.scrollIntoView?.({ behavior: 'smooth', block: 'nearest' }));
    try {
      await query.submit(message, config.scenario, captured.data.scope, {
        preserveDiagnosis, analysisMode: current.viewMode, currentTime: sentMasterTime, workspaceSnapshot: captured.data,
        validateResult: response => {
          const requested = captured.data.scope;
          if (response.scope.scenario !== requested.scenario || response.scope.start !== requested.start || response.scope.end !== requested.end
            || toEpoch(response.scope.end) > toEpoch(sentMasterTime))
            throw new Error("后端返回范围与本次请求不一致，已停止展示该回答、图表和方案；请核对后端采用的时间窗口。");
        },
      });
    } finally {
      if (requestSerial.current === serial && replayContextKey(latest.current) === key) act({ type: "lock", value: false });
    }
  }
  function reloadSource() {
    if (replay.latest.current.locked || loading) return;
    act({ type: "restart" }); act({ type: "unready" });
    setSource({ status: "loading", records: [], aps: [], error: "", notice: "" });
    reload(value => value + 1);
  }

  function reviewForecast(handoff: ForecastHandoff) {
    const current = readClock(), session = forecast.session;
    if (!session || current.locked || current.viewMode === "history" || session.key !== replayContextKey(current)
      || forecast.result !== handoff.result || handoff.result.status !== "ready") return;
    act({ type: "forecast-analysis", startMs: toEpoch(handoff.result.history.start), endMs: toEpoch(handoff.result.baseTime) });
    const next = readClock();
    if (next.viewMode !== "analysis" || formatReplayTime(viewTime(next)) !== handoff.result.baseTime) return;
    setCaseQuestion('');
    setForecastTransfer({ key: replayContextKey(next), handoff, revision: ++forecastReviewSequence.current }); forecast.close();
    if (typeof document !== "undefined") requestAnimationFrame(() => document.getElementById("ain-tab-diagnosis")?.scrollIntoView?.({ behavior: "smooth", block: "nearest" }));
  }

  const transferred = forecastTransfer?.key === contextKey ? forecastTransfer.handoff : null;
  const forecastNoticeResult = forecast.result ?? forecast.lastResult ?? transferred?.result;
  // 入口必须展示**独立检查**的结果（复查 P1-1）：打开弹窗前也要能更新；
  // 检查失败时显示"检查失败"，绝不能显示成"0 项/无风险"。
  const forecastSummary = forecast.summary;
  const forecastEntryLabel = forecastSummary.status === "error" ? "风险检查失败"
    : forecastSummary.status === "loading" ? "检查中…"
      : forecastSummary.status === "ready"
        ? `${forecastSummary.alertCount} 项${forecastSummary.alertCount > 0 ? "需关注" : "（未检出风险）"}`
        : forecastNoticeResult?.status === "ready"
          ? `${forecastNoticeResult.source === "demo" ? "示例·" : "最近查看·"}${forecastNoticeResult.alerts.length}项`
          : FORECAST_CONFIG.mode === "demo" ? "示例预览" : "查看预测";
  const forecastEntryCount = forecastSummary.status === "ready" ? forecastSummary.alertCount
    : forecastSummary.status === "error" ? 0
      : (forecastNoticeResult?.status === "ready" ? forecastNoticeResult.alerts.length : 0);
  const forecastEntryTime = forecastSummary.baseTime || forecastNoticeResult?.baseTime;
  const forecastReviewKey = transferred ? `${contextKey}|${forecastTransfer!.revision}` : '';
  const forecastPending = Boolean(transferred && (forecastActivity.key !== forecastReviewKey || forecastActivity.pending));

  function askForecast(message: string): string {
    if (!transferred || forecastPending || source.status !== 'ready') return '预测研判尚未就绪，请稍后重试。';
    // 按这轮固定基准解释时间，不采用继续前进的主时钟。
    const horizon = Math.round((toEpoch(transferred.result.forecast.end) - toEpoch(transferred.result.baseTime)) / 60_000);
    const kind = forecastQuestionKind(message, config.scenario);
    if (kind === 'existing') {
      return '当前为预测风险研判，请先点击“切换到现存分析”，再查询当前或历史问题。本次未发送，预测结果已保留。';
    }
    if (kind === 'range') {
      const future = resolveForecastQuestion(message, { baseTime: transferred.result.baseTime,
        defaultHorizonMinutes: horizon, maxHorizonMinutes: FORECAST_LIMITS.maxHorizonMinutes });
      if (future.kind === 'error') return future.message;
      if (future.kind !== 'forecast') return '请使用“预测未来60分钟”等明确范围，或点击“调整预测范围”。';
      forecast.open({ horizonMinutes: future.horizonMinutes, targetWindow: future.targetWindow, question: message, follow: false });
      return '';
    }
    // 普通追问原样保留预测轮次、所选预警、基准与关注时段。
    setForecastTransfer({ key: contextKey, handoff: { ...transferred, question: message }, revision: ++forecastReviewSequence.current });
    return '';
  }
  function leaveForecast() {
    setCaseQuestion('');
    clearContext(contextKey);
    setAnalysisNotice({ key: contextKey, text: '已切换到现存分析，查看时刻仍固定；请选择当前或历史问题后发送。' });
  }
  const hasAnalysis = Boolean(transferred || (activeSnapshot && (visibleQuery.question || visibleQuery.pending || visibleQuery.error || visibleQuery.result)));
  const canViewPlans = Boolean(transferred ? !forecastPending : (hasAnalysis && !visibleQuery.pending && (visibleQuery.diagnosisResult || (!visibleQuery.error && visibleQuery.result))));
  const analysisKey = transferred
    ? `forecast:${forecastReviewKey}:${transferred.result.id}:${transferred.alertId}:${transferred.targetWindow?.start ?? ''}:${transferred.targetWindow?.end ?? ''}`
    : hasAnalysis ? `query:${visibleQuery.requestId}` : '';
  useEffect(() => {
    // New analysis starts at its type/time header, rather than the previous answer's scroll position.
    for (const id of ['ain-panel-diagnosis', 'ain-panel-plans']) {
      const panel = document.getElementById(id);
      if (panel) panel.scrollTop = 0;
    }
  }, [analysisKey, contextKey]);
  const requestedTab = tabChoice?.context === contextKey && tabChoice.analysis === analysisKey ? tabChoice.tab : hasAnalysis ? 'diagnosis' : 'data';
  const activeTab: WorkspaceTab = requestedTab === 'plans' && !canViewPlans ? 'diagnosis'
    : requestedTab !== 'data' && !hasAnalysis ? 'data' : requestedTab;
  const selectTab = (tab: WorkspaceTab) => {
    if ((tab === 'diagnosis' && !hasAnalysis) || (tab === 'plans' && !canViewPlans)) return;
    setTabChoice({ context: contextKey, analysis: analysisKey, tab });
  };
  // Evidence queries keep the original diagnosis and its plans. Their headers
  // must keep that same question/forecast context, even if the helper fails.
  const contextResult = visibleQuery.contextRequest && visibleQuery.diagnosisResult ? visibleQuery.diagnosisResult : visibleQuery.result;
  const contextQuestion = visibleQuery.contextRequest && visibleQuery.diagnosisResult ? visibleQuery.diagnosisQuestion : visibleQuery.question;
  const chainForecast = contextResult?.chainForecast;
  const chainBase = chainForecast?.baseTime;
  const chainHorizon = chainForecast?.horizonMinutes ?? contextResult?.horizonMinutes;
  let chainFuture: { start: string; end: string } | undefined;
  if (chainBase && chainHorizon && Number.isFinite(chainHorizon) && chainHorizon > 0) {
    try { chainFuture = { start: chainBase, end: formatReplayTime(toEpoch(chainBase) + chainHorizon * 60_000) }; }
    catch { /* An optional malformed chain timestamp must not crash the workbench. */ }
  }
  const selectedForecastAlert = transferred?.result.alerts.find(a => a.id === transferred.alertId);
  const analysisContext: AnalysisContext = transferred ? {
    kind: 'forecast', question: transferred.question || '',
    history: { scenario: transferred.result.scenario, ...transferred.result.history },
    baseTime: transferred.result.baseTime, future: transferred.targetWindow ?? transferred.result.forecast,
    selectedRisk: selectedForecastAlert ? (selectedForecastAlert.title.includes(selectedForecastAlert.target) ? selectedForecastAlert.title : `${selectedForecastAlert.target} · ${selectedForecastAlert.title}`) : undefined,
  } : {
    kind: chainForecast ? 'mixed' : clock.viewMode === 'history' ? 'history' : 'current',
    history: activeSnapshot?.data.scope ?? scope, question: contextQuestion,
    baseTime: chainBase, future: chainFuture,
    anomalySpans: contextResult?.diagnosis?.anomalySpans,
    anomalyScope: contextResult?.diagnosis?.anomalyScope,
  };

  return <WorkspacePresentationContext.Provider value={{ diagnosisHost, plansHost, canViewPlans, showPlans: () => selectTab('plans') }}>
  <main className="ain-workbench ain-tabbed-workbench">
    <div className="ain-workbench-title"><div><h1>网络分析与管理</h1>
      <p>主时钟支持暂停与继续，固定查看时刻研判原因与处理方案。</p></div>
      <span className="ain-source-badge">历史数据回放</span></div>

    <ReplayControls state={clock} act={act} loading={loading} onReload={reloadSource} config={config} />

    <section className="ain-workspace-results" aria-label="数据与分析工作区">
      <div className="ain-workspace-tabs" role="tablist" aria-label="工作区内容" onKeyDown={event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        const tabs = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('button:not(:disabled)'));
        const at = tabs.indexOf(document.activeElement as HTMLButtonElement);
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (at + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
        event.preventDefault(); tabs[next]?.focus(); tabs[next]?.click();
      }}>
        {([{ id: 'data', label: '数据展示', enabled: true }, { id: 'diagnosis', label: '问题研判', enabled: hasAnalysis }, { id: 'plans', label: '方案效果', enabled: canViewPlans }] as const).map(tab =>
          <button key={tab.id} id={`ain-tab-${tab.id}`} type="button" role="tab" aria-selected={activeTab === tab.id}
            aria-controls={`ain-panel-${tab.id}`} disabled={!tab.enabled} tabIndex={activeTab === tab.id ? 0 : -1}
            title={!tab.enabled ? tab.id === 'diagnosis' ? '发送问题后查看研判' : '研判完成后查看方案与效果' : undefined}
            onClick={() => selectTab(tab.id)}>{tab.label}</button>)}
        <span>{hasAnalysis ? analysisContext.kind === 'forecast' ? '预测分析' : analysisContext.kind === 'mixed' ? '现存 + 预测' : '现存分析' : '提问后解锁研判与方案'}</span>
      </div>
      <div className="ain-workspace-body">
    <section hidden={activeTab !== 'data'} role="tabpanel" aria-labelledby="ain-tab-data" id="ain-panel-data" className="ain-data-module ain-feature-module">
      <header className="ain-feature-heading ain-data-heading"><div className="ain-module-title">
        <h2 className="ain-feature-title" id="ain-data-title">数据展示</h2>
        <p>{clock.viewMode === "live" ? "跟随主时钟更新" : "与AI查看时刻同步固定"} · 选择对象观察指标、地图与连接</p></div>
        <span className="ain-feature-context">{clock.viewMode === "history" ? "历史回顾" : clock.viewMode === "analysis" ? "固定分析" : "跟随当前"}</span>
        <HistoryControls state={clock} act={act} loading={loading} config={config} />
      </header>
      {clock.viewMode === "history" && <ReferenceWindowControls key={`history-${contextKey}`} idPrefix="ain-history-reference"
        state={clock} act={act} scope={scope} disabled={loading || Boolean(source.error)} />}
      {/* 预置案例：事件来自后端 /api/data/events（data/fault_events.csv），非手写时间戳 */}
      <CaseEventButtons key={`cases-${clock.viewMode}`} state={clock} act={act} scope={scope}
        disabled={loading || Boolean(source.error)}
        onPrefill={setCaseQuestion} />
      <section className="ain-filter-panel ain-replay-data-info" aria-label="回放数据来源与统计口径">
        <div className="ain-data-bar">
          <div className="ain-data-meta-summary">
            <span className="ain-data-scene">{config.scenario}</span>
            <span>查看截止：<time dateTime={displayedTime.replace(" ", "T") + "+08:00"}>{displayedTime}</time></span>
            {data && <span className="ain-data-meta-count">{data.metrics.length} 条 · {data.nodes.filter(node => node.type === "ap").length} AP / {data.nodes.filter(node => node.type === "ue").length} UE</span>}
          </div>
          <details className="ain-data-meta-details"><summary>范围与采样详情</summary>
            <dl><div><dt>数据来源</dt><dd>仿真实时数据流（历史 CSV 回放）</dd></div>
              <div><dt>分析范围</dt><dd>{scopeLabel(scope)}</dd></div>
              <div><dt>主时钟</dt><dd>{currentTime} · {clock.playing ? "持续运行" : clock.currentMs >= clock.endMs ? "已到末尾" : "已暂停"}</dd></div>
              <div><dt>最近有效采样</dt><dd>{data?.metrics.at(-1)?.backendTime ?? "暂无"}</dd></div></dl>
            <p>设备数为截至查看时刻的已知设备；主图默认窗口主连接与平均位置，采样信号使用最近有效帧，可切换最新帧实际连接。时间均为北京时间。</p>
          </details>
        </div>
        {source.notice && <p className="ain-filter-note">{source.notice}</p>}
      </section>
      {loading && <p className="ain-load-state" role="status">正在读取历史数据，完成后自动开始回放…</p>}
      {source.error && <div className="ain-error" role="alert"><p>{source.error}</p>
        <button type="button" className="ain-button" disabled={clock.locked} onClick={reloadSource}>重试读取历史数据</button></div>}
      {source.status === "empty" && <div className="ain-load-state" role="status"><h2>配置的回放时段暂无数据</h2>
        <p>请确认后端已有 {config.start} — {config.end} 的校园记录。</p></div>}
      {data && <>
        {!data.metrics.length && <p className="ain-load-state" role="status">当前查看窗口尚无采样，可返回当前或查看已经到达的历史时段。</p>}
        <DataObservation key={contextKey} data={data} spatial={visibleSpatial.data} pending={visibleSpatial.loading}
          error={visibleSpatial.error} onRetry={visibleSpatial.retry} onFocusedAP={id => setFocus({ contextKey, id })}
          spatialNotice={clock.viewMode === "live" ? "实时跟随展示最新采样；固定查看时刻后加载对应窗口的空间估计。" : ""} />
      </>}
    </section>
        <section id="ain-panel-diagnosis" className="ain-workspace-panel ain-ai-v3" role="tabpanel" aria-labelledby="ain-tab-diagnosis" hidden={activeTab !== 'diagnosis'}>
          {hasAnalysis && <AnalysisContextBanner context={analysisContext} section="问题研判" />}
          <div ref={setDiagnosisHost} />
        </section>
        <section id="ain-panel-plans" className="ain-workspace-panel ain-ai-v3" role="tabpanel" aria-labelledby="ain-tab-plans" hidden={activeTab !== 'plans'}>
          {hasAnalysis && <AnalysisContextBanner context={analysisContext} section="方案效果" />}
          <div ref={setPlansHost} />
          <nav className="ain-workspace-next"><button type="button" className="ain-button" onClick={() => selectTab('diagnosis')}>← 返回问题研判</button><button type="button" className="ain-button" onClick={() => selectTab('data')}>查看数据展示</button></nav>
        </section>
      </div>
    </section>
    <QueryPanel prefillQuestion={caseQuestion} analysisNotice={analysisNotice.key === contextKey ? analysisNotice.text : ""} referenceControls={<ReferenceWindowControls key={contextKey} state={clock} act={act} scope={scope} disabled={loading || Boolean(source.error)} />} query={visibleQuery} scenario={config.scenario} window={scope}
      onOpenForecast={() => forecast.open()} forecastDisabled={source.status !== "ready" || clock.locked}
      forecastLabel={forecastEntryLabel} forecastAlertCount={forecastEntryCount} forecastResultTime={forecastEntryTime}
      forecastReview={transferred ? <ForecastReview key={forecastReviewKey} reviewKey={forecastReviewKey} onStatusChange={reportForecastStatus} handoff={transferred} /> : undefined}
      forecastContext={transferred ?? undefined} forecastPending={forecastPending} onForecastQuestion={askForecast} onLeaveForecast={leaveForecast}
      disabled={!data?.metrics.length || loading || Boolean(source.error)} data={data}
      spatial={visibleSpatial.data} spatialPending={visibleSpatial.loading} spatialError={visibleSpatial.error}
      onSpatialRetry={visibleSpatial.retry} focusedApId={activeSnapshot ? activeSnapshot.focusedApId : focusedApId}
      dataLoading={loading} dataError={source.error} onSubmitAnalysis={submitAnalysis}
      replay={{ playing: clock.playing, currentTime, analysisTime: activeSnapshot?.data.scope.end ?? null,
        viewTime: displayedTime, mode: clock.viewMode, canAnalyzeQuestionTime: source.status === "ready", hasCurrentData: Boolean(data?.metrics.length),
        onFreeze: () => act({ type: "freeze" }), onExit: () => act({ type: "live" }) }} />

    {forecast.session && <ForecastDialog request={forecast.session.request} result={forecast.result} pending={forecast.pending}
      error={forecast.error} onClose={forecast.close} onRefresh={forecast.refresh} onAnalyze={reviewForecast}
      canAnalyze={!clock.locked && clock.viewMode !== "history" && source.status === "ready"} fixedView={clock.viewMode === "analysis"}
      follow={forecast.follow} onToggleFollow={forecast.toggleFollow} followStepMinutes={forecast.followStepMinutes}
      onHorizonChange={forecast.setHorizonMinutes} currentTime={currentTime} />}
  </main>
  </WorkspacePresentationContext.Provider>;
}
