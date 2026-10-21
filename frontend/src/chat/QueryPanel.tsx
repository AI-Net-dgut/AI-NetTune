import type { ReactNode } from "react";
import { useRef, useState } from "react";
import type useNetworkQuery from "../hooks/useNetworkQuery";
import type { DashboardData, QueryScope, SpatialMapData } from "../types/backend";
import { scopeLabel, exportReport } from "../services/backend";
import ManagementModule from "../components/management/ManagementModule";
import ForecastComparePanel from "../components/charts/ForecastComparePanel";
import type { ManagementHandle } from "../components/management/preparePlan";
import DiagnosisReply, { ReplyText } from "./DiagnosisAnswer";
import DiagnosisCharts from "./DiagnosisCharts";
import type { EvidenceSelection } from "./DiagnosisCharts";
import { sameQueryScope } from "./diagnosisModel";
import { hasHistoryTimeHint } from "../replay/historyQuestion";
import { needsFutureForecast } from "../replay/analysisSnapshot";
import TimeSelectionGuide from "../replay/TimeSelectionGuide";
import "./QueryPanel.css";

const EXAMPLES = [
  { label: "全网异常", question: "分析当前网络异常及原因，给出处理方案并比较仿真预计效果" },
  { label: "处理优先级", question: "分析哪个 AP 的异常最需要处理，说明原因，给出方案并比较预计效果" },
  { label: "弱覆盖", question: "分析弱覆盖异常的区域及原因，提出方案并比较预计覆盖效果" },
  { label: "AP 拥塞", question: "分析 AP 拥塞异常及原因，提出方案并比较预计负载变化" },
  { label: "连接分配", question: "分析用户连接分配异常及原因，提出调整方案并比较预计用户体验" },
  { label: "未来风险", question: "预测未来120分钟可能出现的网络风险，说明依据并给出预防方案" },
];

export default function QueryPanel({ query, scenario, window: scopeWindow, disabled, data,
  spatial = null, spatialPending = false, spatialError = "", onSpatialRetry = () => {}, focusedApId = null,
  dataLoading = false, dataError = "", replay, onSubmitAnalysis, referenceControls, analysisNotice = "",
  onOpenForecast, forecastDisabled = false, forecastLabel = "查看未来风险", forecastReview,
}: {
  query: ReturnType<typeof useNetworkQuery>; scenario: string; window?: QueryScope; disabled: boolean;
  data: DashboardData | null; spatial?: SpatialMapData | null; spatialPending?: boolean; spatialError?: string;
  onSpatialRetry?: () => void; focusedApId?: string | null;
  dataLoading?: boolean; dataError?: string; referenceControls?: ReactNode; analysisNotice?: string;
  onOpenForecast?: () => void; forecastDisabled?: boolean; forecastLabel?: string; forecastReview?: ReactNode;
  replay?: { playing: boolean; currentTime: string; analysisTime: string | null; hasCurrentData?: boolean; viewTime?: string; mode?: "live" | "analysis" | "history"; canAnalyzeQuestionTime?: boolean; onFreeze?: () => void; onExit?: () => void };
  onSubmitAnalysis?: (message: string, preserveDiagnosis: boolean, retry: boolean) => Promise<void>;
}) {
  const [draft, setDraft] = useState("");
  const [sendNotice, setSendNotice] = useState("");
  const [exporting, setExporting] = useState(false);
  const [exportNotice, setExportNotice] = useState("");
  const [evidenceSelection, setEvidenceSelection] = useState<(EvidenceSelection & { diagnosisId: number; requestId: number }) | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const managementRef = useRef<ManagementHandle>(null);
  const followingLive = replay?.mode ? replay.mode === "live" : Boolean(replay?.playing);
  const historical = replay?.mode === "history";
  const busy = disabled || query.pending || followingLive;
  const currentEmpty = replay?.hasCurrentData === false;
  const result = query.result;
  const analysis = query.diagnosisResult;
  const diagnosis = analysis?.diagnosis || null;
  const scope = data?.scope || scopeWindow;
  const mismatch = Boolean(result && scope && !sameQueryScope(result.scope, scope));
  const analysisMismatch = Boolean(diagnosis && scope && !sameQueryScope(diagnosis.scope, scope));
  const currentEvidenceSelection = evidenceSelection?.diagnosisId === query.diagnosisId && evidenceSelection?.requestId === query.requestId ? evidenceSelection : null;
  const dataStatus = dataLoading ? "数据加载中" : dataError ? "数据加载失败" : !data ? "当前范围暂无数据" : replay && !data.metrics.length ? "当前时刻暂无采样" : "";
  const status = dataStatus || (query.pending ? "正在分析" : currentEmpty ? "当前窗口暂无采样" : followingLive ? "实时跟随" : query.error ? "请求失败" : result ? "已返回" : historical ? "历史回顾，可提问" : replay ? "已固定，可提问" : "等待提问");
  const diagnosisQuestion = query.diagnosisQuestion || (result === analysis ? query.question : "");

  const questionTime = Boolean(replay?.canAnalyzeQuestionTime && (hasHistoryTimeHint(draft) || needsFutureForecast(draft)));
  const sendBlocked = (disabled && !questionTime) || dataLoading || Boolean(dataError) || query.pending || (followingLive && !questionTime);

  /** 生成并打开/下载分析报告（接口4）。报告口径与当前展示范围一致。 */
  async function runExport(format: "md" | "html") {
    if (!scope || exporting) return;
    setExporting(true); setExportNotice("");
    try {
      const { url, reportId, bytes } = await exportReport(scope, format);
      if (format === "html") {
        globalThis.open(url, "_blank", "noopener");
        setExportNotice(`报告 ${reportId} 已生成（${Math.round(bytes / 1024)} KB），已在新标签打开。`);
      } else {
        const a = document.createElement("a");
        a.href = url; a.download = `${reportId}.md`;
        document.body.appendChild(a); a.click(); a.remove();
        setExportNotice(`报告 ${reportId} 已下载（${Math.round(bytes / 1024)} KB）。`);
      }
    } catch (error) {
      setExportNotice(error instanceof Error ? `报告生成失败：${error.message}` : "报告生成失败");
    } finally {
      setExporting(false);
    }
  }
  function send(message = draft, preserveDiagnosis = false, retry = false) {
    const explicitTime = Boolean(replay?.canAnalyzeQuestionTime && (hasHistoryTimeHint(message) || needsFutureForecast(message)) && !preserveDiagnosis && !retry);
    if ((disabled && !explicitTime) || dataLoading || dataError || query.pending || (followingLive && !explicitTime) || !message.trim()) return;
    if (currentEmpty && !explicitTime && !preserveDiagnosis && !retry) {
      setSendNotice("当前统计窗口暂无采样，暂不能发起新分析；旧分析的证据和重试仍对应原截止时间。"); return;
    }
    // 未来问题走预测预览接口，显式传入时间与时长；历史回顾保持既有禁用规则。
    if (historical && needsFutureForecast(message)) {
      setSendNotice("历史回顾期间不提供未来预测；请先「退出分析，返回当前」并固定时刻后再问未来趋势。");
      return;
    }
    setSendNotice(""); setEvidenceSelection(null);
    if (onSubmitAnalysis) void onSubmitAnalysis(message, preserveDiagnosis, retry);
    else void query.submit(message, scenario, (preserveDiagnosis || retry) ? data?.scope ?? scopeWindow : scopeWindow, { preserveDiagnosis, analysisMode: historical ? "history" : "analysis" });
  }
  function tool(type: "heatmap" | "topology") {
    send(`${scenario}的${type === "heatmap" ? "信号热力图" : "拓扑连接关系"}`, true);
  }

  return <section className="ain-ai-panel ain-ai-v3 ain-feature-module" id="ain-ai-module" aria-labelledby="ain-ai-title">
    <header className="ain-ai-heading">
      <div className="ain-ai-heading-title"><span className="ain-ai-symbol" aria-hidden="true">AI</span>
        <div><h2 id="ain-ai-title">AI 网络助手</h2><p>提问 → 原因与证据 → 处理方案 → 仿真效果对比</p></div></div>
      <div className="ain-ai-heading-actions">
        <button type="button" className={onOpenForecast ? "ain-forecast-trigger" : "ain-forecast-placeholder"} disabled={historical || forecastDisabled || query.pending || dataLoading || !onOpenForecast}
          onClick={onOpenForecast} title={historical ? "历史查看期间不提供未来预测" : "查看频谱态势、未来风险及相关指标；结果固定到打开时刻"}>预测预警 <span>{historical ? "历史不可用" : forecastLabel}</span></button>
        <span className="ain-ai-status" data-tone={query.error || dataError ? "error" : query.pending || dataLoading ? "info" : "neutral"}>{status}</span>
      </div>
    </header>
    {replay ? <div className="ain-ai-scope ain-ai-replay-scope">
      <span>主时钟：{replay.currentTime}（北京时间）</span>
      <span>查看截止时刻：{replay.viewTime ?? replay.analysisTime ?? replay.currentTime}（北京时间）</span>
      <span className="ain-ai-view-mode">查看状态：{historical ? "历史回顾" : followingLive ? "跟随当前" : "固定当前"}</span>
      {replay.mode ? <button type="button" className="ain-button" disabled={followingLive && disabled}
        onClick={followingLive ? replay.onFreeze : replay.onExit}>{followingLive ? "固定当前时刻" : "退出分析，返回当前"}</button>
        : <span>{replay.analysisTime ? `分析截至：${replay.analysisTime}（北京时间）` : "固定查看时刻后发送问题"}</span>}
    </div> : <p className="ain-ai-scope">分析范围：{scope ? scopeLabel(scope) : `${scenario} · 后端默认范围`}</p>}
    {referenceControls}
    <TimeSelectionGuide historical={historical} />
    {analysisNotice && <p className="ain-ai-warning" role="status">{analysisNotice}</p>}

    <div className="ain-query-examples" aria-label="问题示例">
      {EXAMPLES.map(item => <button className="ain-query-example" key={item.label} type="button" disabled={query.pending || (historical && needsFutureForecast(item.question))}
        onClick={() => { setSendNotice(""); setDraft(`${scenario}：${item.question}`); inputRef.current?.focus(); }}>
        <strong>{item.label}</strong><span>{item.question}{historical && needsFutureForecast(item.question) && <small className="ain-example-pending">历史不可用</small>}</span></button>)}
    </div>
    <form className="ain-query-form" onSubmit={event => { event.preventDefault(); send(); }}>
      <label className="ain-input-label" htmlFor="ain-question">输入你的网络问题</label>
      <div className="ain-query-composer"><textarea id="ain-question" ref={inputRef} rows={2} maxLength={500} value={draft} disabled={query.pending}
        placeholder="分析异常及原因，给出处理方案并比较仿真预计效果。"
        onChange={event => { setSendNotice(""); setDraft(event.target.value); event.target.style.height = "auto"; event.target.style.height = `${Math.min(event.target.scrollHeight, 100)}px`; }} onKeyDown={event => {
          if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && !event.nativeEvent.isComposing) {
            event.preventDefault(); send();
          }
        }} /><button type="submit" className="ain-button ain-primary" disabled={sendBlocked || (currentEmpty && !questionTime) || !draft.trim()}>
          {query.pending ? "分析中…" : followingLive && !questionTime ? "先固定时刻" : "发送"}</button></div>
      <div className="ain-query-input-meta"><span>{followingLive ? (replay?.canAnalyzeQuestionTime ? "历史时间或未来预测可直接发送；未来问题会固定查看时刻并打开预测。" : "可先编辑问题；固定当前时刻后发送，主时钟播放状态不变。") : "未来问题进入预测预警；点击示例填入后可编辑 · Ctrl / Cmd + Enter"}</span><span>{draft.length}/500</span></div>
    </form>
    {sendNotice && <p className="ain-ai-warning" role="status">{sendNotice}</p>}
    {currentEmpty && !disabled && !query.pending && <p className="ain-ai-note" role="status">当前窗口暂无采样，暂不能发起新分析；仍可查看及重试原截止时刻的分析。</p>}
    {disabled && !query.pending && <p className="ain-ai-note" role={dataError ? "alert" : "status"}>{dataLoading ? "正在读取历史数据，完成后固定时刻即可提问。" : dataError ? `${dataError}；请在顶部重载数据。` : "当前查看时刻暂无有效采样，请返回当前或查看已到达的历史数据。"}</p>}

    <div className="ain-ai-workspace">
      <section className="ain-ai-diagnosis-column" aria-labelledby="ain-ai-diagnosis-title">
        <div className="ain-ai-column-heading"><h3 id="ain-ai-diagnosis-title">问题研判与证据</h3>
          <span>{query.pending ? "等待本次结果" : diagnosis?.state === "anomaly" ? "发现异常" : diagnosis?.state === "normal" ? "未发现明显异常" : "助手回答"}</span></div>
        <div className="ain-ai-answer" aria-live="polite" aria-busy={query.pending}>
          {forecastReview}
          {query.question && <p className="ain-ai-user-question"><strong>本次提问</strong>{query.question}</p>}
          {query.pending && <p className="ain-ai-pending" role="status">正在查询固定分析时刻的数据…{analysis ? "下方保留此辅助查询关联的研判。" : "本次结果尚未返回，上一轮研判和方案已清空。"}</p>}
          {query.error && <div className="ain-query-error" role="alert"><p>{query.error}</p>
            <button type="button" className="ain-button" disabled={busy} onClick={() => send(query.question, query.contextRequest, true)}>重试本次问题</button></div>}
          {mismatch && <p className="ain-ai-warning">本次返回范围与已加载数据不同，请核对范围后使用结果。</p>}
          {analysisMismatch && <p className="ain-ai-warning">研判范围与已加载数据不同，右侧建议暂不可采用，辅助图使用已加载数据。</p>}
          {!query.pending && !query.error && result && !result.diagnosis && <div className="ain-ai-tool-reply"><ReplyText text={result.reply} />
            <p className="ain-ai-result-scope">返回范围：{scopeLabel(result.scope)}</p></div>}
          {analysis && <><div className="ain-ai-analysis-caption">{result === analysis ? "本次研判" : "关联研判（保留）"}{result !== analysis && diagnosisQuestion && ` · ${diagnosisQuestion}`}</div>
            <DiagnosisReply result={analysis} onEvidence={selection => setEvidenceSelection(previous => ({ ...selection, id: (previous?.id ?? 0) + 1, diagnosisId: query.diagnosisId, requestId: query.requestId }))} /></>}
          {!forecastReview && !analysis && !result && !query.pending && !query.error && <p className="ain-ai-empty">{dataStatus ? `${dataStatus}，加载有效数据后开始研判。` : "先选择一个问题示例，或直接描述异常。回答、证据图和处理建议会展示在这里。"}</p>}
        </div>
        {data && (diagnosis || result?.chart) && <DiagnosisCharts key={`${query.diagnosisId}-${result?.chart ? query.requestId : "evidence"}-${currentEvidenceSelection?.id ?? 0}`}
          data={data} diagnosis={analysisMismatch ? null : diagnosis} toolResult={mismatch ? null : result}
          spatial={spatial} spatialPending={spatialPending} spatialError={spatialError} onSpatialRetry={onSpatialRetry}
          busy={busy} onTool={tool} selection={currentEvidenceSelection} />}
        {diagnosis?.state === "anomaly" && diagnosis.suggestion.trim() && <button type="button" className="ain-ai-adopt-link"
          disabled={busy || analysisMismatch || !data} onClick={() => managementRef.current?.prepare({ source: "chat", result: diagnosis, candidate: diagnosis.candidates[0] || null })}>
          根据此研判准备方案 →</button>}
        {/* key 让范围变化时重建面板，从而丢弃旧的回测结果 */}
        <ForecastComparePanel key={scope ? `${scope.scenario}|${scope.start}|${scope.end}` : "none"}
          scope={scope ?? null} disabled={disabled} />
      </section>
      {data ? <ManagementModule key={`${data.generatedAt}-${query.diagnosisId}`} ref={managementRef} data={data}
        diagnosis={null} chatDiagnosis={diagnosis} focusedApId={focusedApId} disabled={busy} plans={analysisMismatch ? [] : analysis?.plans}
        sourceQuestion={diagnosisQuestion} pending={query.pending} />
        : <section className="ain-ai-management-empty" aria-label="处理建议与效果"><h3>处理建议与效果</h3><p>{dataStatus}。加载数据并完成研判后，查看处理方案、配置参数和仿真预计效果。</p></section>}
    </div>

    <div className="ain-query-footer"><details className="ain-query-history-details">
      <summary>本次查询记录 <span>{query.history.length}/6</span></summary>
      <p className="ain-ai-note">最近 6 次 · 仅记录问题和回答摘要</p>
      <div className="ain-query-history">{!query.history.length && <p className="ain-ai-note">暂无查询记录</p>}
        {query.history.map(entry => <article key={entry.id} className={`ain-query-record${entry.failed ? " is-error" : ""}`}>
          <div className="ain-query-record-meta"><strong>{entry.question}</strong><time>{new Date(entry.createdAt).toLocaleTimeString("zh-CN", { hour12: false })}</time></div>
          <ReplyText text={entry.answer} /><p className="ain-ai-note">{entry.analysisMode === "history" ? "历史回顾" : "固定时刻分析"} · 分析截至：{entry.analysisTime ?? "未记录"}（北京时间）</p><p className="ain-ai-note">{entry.scope}（北京时间）</p></article>)}</div>
    </details>
      <span className="ain-report-actions">
        <button type="button" className="ain-ai-text-button" disabled={!scope || exporting}
          title="按当前展示范围生成报告：研判结论、证据链、候选原因、分 AP 对比、小时趋势与处理方案"
          onClick={() => runExport("html")}>{exporting ? "生成中…" : "导出分析报告"}</button>
        <button type="button" className="ain-ai-text-button" disabled={!scope || exporting}
          onClick={() => runExport("md")}>下载 Markdown</button>
      </span>
      <button type="button" className="ain-ai-text-button" disabled={query.pending || !query.history.length} onClick={query.clearHistory}>清空记录</button>
    </div>
    {exportNotice && <p className="ain-ai-note" role="status">{exportNotice}</p>}
  </section>;
}
