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
import { captureAnalysis, needsFutureForecast } from "../../replay/analysisSnapshot";
import type { AnalysisSnapshot } from "../../replay/analysisSnapshot";
import useReplay from "../../replay/useReplay";
import useScopedSpatial from "../../replay/useScopedSpatial";
import ReplayControls from "../../replay/ReplayControls";
import HistoryControls from "../../replay/HistoryControls";
import ReferenceWindowControls from "../../replay/ReferenceWindowControls";
import type { ReplayConfig } from "../../replay/config";
import "./WorkbenchPage.css";
import "./BackendIntegration.css";

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
  const [analysisNotice, setAnalysisNotice] = useState<{ key: string; text: string }>({ key: "", text: "" });
  const [focus, setFocus] = useState<{ contextKey: string; id: string | null }>({ contextKey, id: null });
  const focusedApId = focus.contextKey === contextKey ? focus.id : null;
  const [snapshot, setSnapshot] = useState<AnalysisSnapshot | null>(null);
  const snapshotRef = useRef<AnalysisSnapshot | null>(null);
  const query = useNetworkQuery();
  const resetQuery = query.reset;
  const clearedContext = useRef(contextKey);
  const requestSerial = useRef(0);
  const clearContext = useCallback((key: string) => {
    clearedContext.current = key; requestSerial.current++;
    snapshotRef.current = null; setSnapshot(null); resetQuery();
  }, [resetQuery]);
  const act = useCallback((action: ReplayAction) => {
    const before = replayContextKey(readClock());
    dispatchClock(action);
    const after = replayContextKey(readClock());
    if (after !== before) clearContext(after);
  }, [dispatchClock, readClock, clearContext]);

  useEffect(() => {
    const controller = new AbortController(); let active = true;
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
  }, [reloadCount, act, config]);

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
    if (current.locked || !message.trim() || source.status !== "ready" || needsFutureForecast(message)) return;
    const sentMasterTime = formatReplayTime(current.currentMs);
    if (!preserveDiagnosis && !retry) {
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
    snapshotRef.current = captured; setSnapshot(captured);
    act({ type: "lock", value: true });
    const serial = ++requestSerial.current;
    try {
      await query.submit(message, config.scenario, captured.data.scope, {
        preserveDiagnosis, analysisMode: current.viewMode, currentTime: sentMasterTime,
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

  return <main className="ain-workbench">
    <div className="ain-workbench-title"><div><h1>网络分析与管理</h1>
      <p>主时钟持续运行，固定查看时刻研判原因与处理方案。</p></div>
      <span className="ain-source-badge">历史数据回放</span></div>

    <ReplayControls state={clock} act={act} loading={loading} onReload={reloadSource} config={config} />

    <QueryPanel analysisNotice={analysisNotice.key === contextKey ? analysisNotice.text : ""} referenceControls={<ReferenceWindowControls key={contextKey} state={clock} act={act} scope={scope} disabled={loading || Boolean(source.error)} />} query={visibleQuery} scenario={config.scenario} window={scope}
      disabled={!data?.metrics.length || loading || Boolean(source.error)} data={data}
      spatial={visibleSpatial.data} spatialPending={visibleSpatial.loading} spatialError={visibleSpatial.error}
      onSpatialRetry={visibleSpatial.retry} focusedApId={activeSnapshot ? activeSnapshot.focusedApId : focusedApId}
      dataLoading={loading} dataError={source.error} onSubmitAnalysis={submitAnalysis}
      replay={{ playing: clock.playing, currentTime, analysisTime: activeSnapshot?.data.scope.end ?? null,
        viewTime: displayedTime, mode: clock.viewMode, canAnalyzeQuestionTime: source.status === "ready", hasCurrentData: Boolean(data?.metrics.length),
        onFreeze: () => act({ type: "freeze" }), onExit: () => act({ type: "live" }) }} />

    <section className="ain-data-module ain-feature-module" id="ain-data-module" aria-labelledby="ain-data-title">
      <header className="ain-feature-heading ain-data-heading"><div className="ain-module-title">
        <h2 className="ain-feature-title" id="ain-data-title">数据展示</h2>
        <p>{clock.viewMode === "live" ? "跟随主时钟更新" : "与AI查看时刻同步固定"} · 选择对象观察指标、地图与连接</p></div>
        <span className="ain-feature-context">{clock.viewMode === "history" ? "历史回顾 · 预测不可用" : clock.viewMode === "analysis" ? "分析视图已固定" : "仅展示已到达数据"}</span></header>
      <HistoryControls state={clock} act={act} loading={loading} config={config} />
      {clock.viewMode === "history" && <ReferenceWindowControls key={`history-${contextKey}`} idPrefix="ain-history-reference"
        state={clock} act={act} scope={scope} disabled={loading || Boolean(source.error)} />}
      <section className="ain-filter-panel ain-replay-data-info" aria-label="回放数据来源与统计口径">
        <div className="ain-data-bar"><span className="ain-source-badge">CSV 仿真历史数据</span>
          <span>场景：{config.scenario}</span><span>查看截止时刻：{displayedTime}</span>
          {clock.viewMode !== "live" && <span>主时钟：{currentTime} · {clock.playing ? "持续运行" : "已到末尾"}</span>}
          {data && <><span>窗口内 {data.metrics.length} 条记录</span>
            <span>{data.nodes.filter(node => node.type === "ap").length} AP / {data.nodes.filter(node => node.type === "ue").length} UE</span>
            <span>最近有效采样：{data.metrics.at(-1)?.backendTime ?? "暂无"}</span></>}
          <p className="ain-loaded-scope">趋势／窗口统计：{scopeLabel(scope)}；主图采样信号与最新帧连接使用最近有效采样。</p>
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
  </main>;
}
