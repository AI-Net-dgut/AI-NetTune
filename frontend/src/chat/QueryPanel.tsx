import type { ReactNode } from "react";
import { useContext, useEffect, useRef, useState } from "react";
import { createPortal } from 'react-dom';
import { WorkspacePresentationContext } from '../pages/workbench/WorkspacePresentationContext';
import type useNetworkQuery from "../hooks/useNetworkQuery";
import type { DashboardData, QueryScope, SpatialMapData } from "../types/backend";
import type { TreatmentPlan } from "../types/plan";
import type { ForecastHandoff } from "../forecast/types";
import { scopeLabel, exportReport, fetchSimConfig } from "../services/backend";
import { formatReplayTime } from "../replay/replayState";
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
const FORECAST_EXAMPLES = [
  { label: "风险原因", question: "针对所选预测风险，解释可能原因与判断限制" },
  { label: "支持证据", question: "所选预测风险有哪些支持证据和反面疑点" },
  { label: "影响范围", question: "所选预测风险可能影响哪些AP或用户，预计何时发生" },
  { label: "预防方案", question: "针对所选预测风险，给出可采取的预防方案和适用条件" },
  { label: "方案对比", question: "对比所选预测风险的预防方案，说明预期效果与代价" },
  { label: "不确定性", question: "所选预测风险有哪些不确定性，还需要补充哪些证据" },
];

export default function QueryPanel({ query, scenario, window: scopeWindow, disabled, data,
  spatial = null, spatialPending = false, spatialError = "", onSpatialRetry = () => {}, focusedApId = null,
  dataLoading = false, dataError = "", replay, onSubmitAnalysis, referenceControls, analysisNotice = "",
  onOpenForecast, forecastDisabled = false, forecastLabel = "查看未来风险", forecastAlertCount = 0, forecastResultTime, forecastReview,
  forecastContext, forecastPending = false, onForecastQuestion, onLeaveForecast,
  prefillQuestion = "",
}: {
  query: ReturnType<typeof useNetworkQuery>; scenario: string; window?: QueryScope; disabled: boolean;
  data: DashboardData | null; spatial?: SpatialMapData | null; spatialPending?: boolean; spatialError?: string;
  onSpatialRetry?: () => void; focusedApId?: string | null;
  dataLoading?: boolean; dataError?: string; referenceControls?: ReactNode; analysisNotice?: string;
  onOpenForecast?: () => void; forecastDisabled?: boolean; forecastLabel?: string; forecastAlertCount?: number; forecastResultTime?: string; forecastReview?: ReactNode;
  forecastContext?: ForecastHandoff; forecastPending?: boolean;
  onForecastQuestion?: (message: string) => string; onLeaveForecast?: () => void;
  /** 点案例时预填的问题：**只填进输入框，不自动发送**（用户仍可改写） */
  prefillQuestion?: string;
  replay?: { playing: boolean; currentTime: string; analysisTime: string | null; hasCurrentData?: boolean; viewTime?: string; mode?: "live" | "analysis" | "history"; canAnalyzeQuestionTime?: boolean; onFreeze?: () => void; onExit?: () => void };
  onSubmitAnalysis?: (message: string, preserveDiagnosis: boolean, retry: boolean) => Promise<void>;
}) {
  const workspace = useContext(WorkspacePresentationContext);
  const forecastMode = Boolean(forecastReview);
  // 切换任务/所选预警时不沿用另一任务的草稿，避免“当前网络”被带进预测提问框。
  const draftKey = forecastMode ? `forecast:${forecastContext?.result.id}:${forecastContext?.alertId}:${forecastContext?.targetWindow?.start}:${forecastContext?.targetWindow?.end}` : 'network';
  const [draftState, setDraftState] = useState({ key: draftKey, text: '' });
  const draft = draftState.key === draftKey ? draftState.text : '';
  const setDraft = (text: string) => setDraftState({ key: draftKey, text });
  const [sendNoticeState, setSendNoticeState] = useState({ key: draftKey, text: '' });
  const sendNotice = sendNoticeState.key === draftKey ? sendNoticeState.text : '';
  const setSendNotice = (text: string) => setSendNoticeState({ key: draftKey, text });
  const [exporting, setExporting] = useState(false);
  const [exportNotice, setExportNotice] = useState("");
  const [evidenceSelection, setEvidenceSelection] = useState<(EvidenceSelection & { diagnosisId: number; requestId: number }) | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  // 点案例后把问题填进输入框（只填不发送）；预填是"响应用户点击"的一次性同步动作，
  // 不会引起级联渲染，故对这条规则做定向豁免。
  useEffect(() => {
    if (!prefillQuestion || forecastMode) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setDraftState({ key: draftKey, text: prefillQuestion });
    setSendNoticeState({ key: draftKey, text: '' });
  }, [prefillQuestion, forecastMode, draftKey]);
  const managementRef = useRef<ManagementHandle>(null);
  const followingLive = replay?.mode ? replay.mode === "live" : Boolean(replay?.playing);
  const historical = replay?.mode === "history";
  const busy = disabled || query.pending || followingLive;
  const currentEmpty = replay?.hasCurrentData === false;
  const result = forecastMode ? null : query.result;
  const analysis = forecastMode ? null : query.diagnosisResult;
  const diagnosis = analysis?.diagnosis || null;
  const scope = data?.scope || scopeWindow;
  const mismatch = Boolean(result && scope && !sameQueryScope(result.scope, scope));
  const analysisMismatch = Boolean(diagnosis && scope && !sameQueryScope(diagnosis.scope, scope));
  const currentEvidenceSelection = evidenceSelection?.diagnosisId === query.diagnosisId && evidenceSelection?.requestId === query.requestId ? evidenceSelection : null;
  const dataStatus = dataLoading ? "数据加载中" : dataError ? "数据加载失败" : !data ? "当前范围暂无数据" : replay && !data.metrics.length ? "当前时刻暂无采样" : "";
  const status = forecastMode ? forecastPending ? "预测研判中…" : "预测风险研判" : dataStatus || (query.pending ? "正在分析" : currentEmpty ? "当前窗口暂无采样" : followingLive ? "实时跟随" : query.error ? "请求失败" : result ? "已返回" : historical ? "历史回顾，可提问" : replay ? "已固定，可提问" : "等待提问");
  const diagnosisQuestion = query.diagnosisQuestion || (result === analysis ? query.question : "");

  const questionTime = Boolean(replay?.canAnalyzeQuestionTime && (hasHistoryTimeHint(draft) || needsFutureForecast(draft)));
  // 跟随当前时不再禁用发送：点击发送会自动固定当前时刻（见 WorkbenchPage.submitAnalysis）
  const sendBlocked = forecastMode ? forecastPending || !onForecastQuestion : (disabled && !questionTime) || dataLoading || Boolean(dataError) || query.pending;

  /** 生成并打开/下载分析报告（接口4）。报告口径与当前展示范围一致。 */
  async function runExport(format: "md" | "html") {
    if (!scope || exporting || forecastMode) return;
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
  /**
   * 导出**本次分析**（快照）：把屏幕上这一轮的问题、结论、证据要点、方案与当前仿真配置
   * 原样写成 Markdown 并下载。
   *
   * 与「导出该时段全网报告」的区别（复查 P1-24 那条边界）：
   *   后者由后端**按时间窗口重新研判整网**，不携带本次 AP / 手改参数；
   *   本按钮不重算，只导出"这一次问答的上下文"，两者名称与 aria 必须能区分。
   */
  async function exportSnapshot() {
    if (exporting || forecastMode) return;
    setExporting(true); setExportNotice("");
    try {
      const simStatus = await fetchSimConfig().catch(() => null);
      const active = simStatus?.active ?? null;
      const d = analysis?.diagnosis ?? result?.diagnosis ?? null;
      const lines: string[] = [];
      lines.push(`# AI-NetTune 本次分析快照`, "");
      lines.push(`- 生成时间：${formatReplayTime(Date.now())}（北京时间）`);
      lines.push(`- 场景：${scenario}`);
      lines.push(`- 本次提问：${query.question || draft || "（未记录）"}`);
      if (scope) lines.push(`- 分析范围：${scope.start} — ${scope.end}`);
      lines.push(`- 数据来源：${active ? "simulation（仿真配置生效）" : "raw（原始数据）"}`);
      lines.push(`- 配置版本：${active ? `${active.id}（revision ${simStatus?.revision ?? 0}）` : "无生效配置"}`);
      if (active) {
        lines.push(`- 生效对象：${active.apIds.join("、")}；生效时刻：${active.effectiveFrom}`);
        lines.push(`- 生效参数：${Object.entries(active.params).map(([k, v]) => `${k}=${v}`).join("，")}`);
        lines.push("", "> 注意：频谱预警仍基于原始频谱，本次配置未模拟其对频谱的影响。");
      }
      lines.push("", "## 研判结论");
      if (d) {
        lines.push(`- 状态：${d.currentState}（${d.state}）`);
        if (d.anomalyType) lines.push(`- 异常类型：${d.anomalyType}`);
        if (d.lastAnomalyAt) lines.push(`- 最近异常时刻：${d.lastAnomalyAt}`);
        if (d.anomalySpans?.length) lines.push(`- 异常区间：${d.anomalySpans.map(s => `${s.start} — ${s.end}`).join("；")}`);
        if (d.candidates?.length) {
          lines.push("", "### 候选原因（按规则强度，非概率）");
          d.candidates.slice(0, 5).forEach((c, i) => lines.push(`${i + 1}. ${c.cause}（规则强度 ${c.score}）`));
        }
      } else {
        lines.push("（本次未产生研判结论）");
      }
      lines.push("", "## 处理方案");
      const plans = (d as { plans?: TreatmentPlan[] } | null)?.plans ?? result?.plans ?? [];
      if (plans.length) {
        plans.forEach((p, i) => {
          lines.push(`### ${i + 1}. ${p.name}`);
          if (p.description) lines.push(p.description);
        });
      } else {
        lines.push("（本次未生成方案：可能当前窗口判定为正常，或尚未完成研判）");
      }
      if (result?.chain?.length) {
        lines.push("", "## 编排步骤");
        result.chain.forEach((s, i) => lines.push(`${i + 1}. ${s.title ?? s.tool}：${s.summary ?? ""}`));
      }
      lines.push("", "---", "本快照由界面直接生成，不重新研判；数值口径以页面为准。");
      const blob = new Blob([lines.join("\n")], { type: "text/markdown;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `本次分析_${formatReplayTime(Date.now()).replace(/[: ]/g, "-")}.md`;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
      setExportNotice("已导出本次分析快照（Markdown）。");
    } catch (error) {
      setExportNotice(error instanceof Error ? `导出失败：${error.message}` : "导出失败");
    } finally {
      setExporting(false);
    }
  }
  function send(message = draft, preserveDiagnosis = false, retry = false) {
    if (forecastMode) {
      if (!message.trim() || forecastPending) return;
      setSendNotice(onForecastQuestion ? onForecastQuestion(message.trim()) : '当前预测仅支持查看，请先切换到现存分析后再提问。');
      return;
    }
    const explicitTime = Boolean(replay?.canAnalyzeQuestionTime && (hasHistoryTimeHint(message) || needsFutureForecast(message)) && !preserveDiagnosis && !retry);
    // 复查（用户要求）：有内容就能发；"跟随当前"时由工作台在发送时**自动固定时刻**，
    // 不再要求用户先手动点「固定当前时刻」。
    if ((disabled && !explicitTime) || dataLoading || dataError || query.pending || !message.trim()) return;
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

  const diagnosisContent = (
      <section className="ain-ai-diagnosis-column" aria-labelledby="ain-ai-diagnosis-title">
        <div className={`ain-ai-column-heading${workspace && forecastMode ? " ain-visually-hidden" : ""}`}><h3 id="ain-ai-diagnosis-title" className={workspace ? "ain-visually-hidden" : undefined}>问题研判与证据</h3>
          {/* 状态徽标按后端 current_state 分档：窗口内出现过异常 ≠ 此刻仍在故障，
              必须区分「当前故障 / 窗口内已恢复 / 性能提示 / 正常」，
              否则用户会把"过去 60 分钟里发生过"误读成"现在还在故障"。 */}
          <span data-state={query.pending ? "pending" : diagnosis?.currentState || ""}>
            {forecastMode ? "预测研判" : query.pending ? "等待本次结果"
              : diagnosis?.answerFocus?.status === "insufficient" ? `${diagnosis.answerFocus.label} · 证据不足`
              : diagnosis?.answerFocus?.status === "not_confirmed" ? `${diagnosis.answerFocus.label} · 未确诊`
              : diagnosis?.currentState === "fault_active" ? "当前故障中"
              : diagnosis?.currentState === "recovered_in_window" ? "窗口内有故障·已恢复"
              : diagnosis?.currentState === "performance_warning" ? "性能提示（未确诊）"
              : diagnosis?.state === "anomaly" ? "发现异常"
              : diagnosis?.state === "normal" ? "未发现明显异常"
              : "助手回答"}
          </span>
          {/* 来源必须如实展示（核查清单第 4 条 / 复审 P2）。
              注意：这个徽标只说**意图**，不能说"未走大模型"——规则命中时根本没降级，
              回答仍可能由模型生成（正文里的 [AI] 标记才是回答来源）。
              有降级原因（模型不可用/调用失败）时才显示那串原因。 */}
          {!workspace && !query.pending && query.result?.intentSource && query.result.intentSource !== "llm" && (
            <span className="ain-ai-source-badge" title={
              query.result.intentDegradeReason
                ? `意图未使用大模型：${query.result.intentDegradeReason}`
                : "本次意图由关键词规则命中（规则优先，不调用大模型）；回答是否由模型生成，见正文的 AI 标记"
            }>
              {query.result.intentSource === "rule" ? "意图·规则" : "意图·兜底"}
            </span>
          )}
          </div>
        <div className="ain-ai-answer" aria-live="polite" aria-busy={query.pending}>
          {forecastReview}
          {!workspace && !forecastMode && query.question && <p className="ain-ai-user-question"><strong>本次提问</strong>{query.question}</p>}
          {/* UE 级提问的兜底说明：平台方案是 AP/区域级的，单用户优化未建模。
              提前说清边界，避免"我问的是 UE1，为什么结论是整网"的误解。 */}
          {!forecastMode && query.question && /\bUE[-－\s]?0*\d+\b/i.test(query.question) && (
            <p className="ain-ai-note" role="note">
              本次提问涉及具体用户：平台的处理方案按 <strong>AP / 区域</strong> 生成；
              该用户的连接与体验请用「连接分配」或「用户横向对比」查看，单用户级参数优化不在本模块范围。
            </p>)}
          {!forecastMode && query.pending && <p className="ain-ai-pending" role="status">正在查询固定分析时刻的数据…{analysis ? "下方保留此辅助查询关联的研判。" : "本次结果尚未返回，上一轮研判和方案已清空。"}</p>}
          {!forecastMode && query.error && <div className="ain-query-error" role="alert"><p>{query.error}</p>
            <button type="button" className="ain-button" disabled={busy} onClick={() => send(query.question, query.contextRequest, true)}>重试本次问题</button></div>}
          {mismatch && <p className="ain-ai-warning">本次返回范围与已加载数据不同，请核对范围后使用结果。</p>}
          {analysisMismatch && <p className="ain-ai-warning">研判范围与已加载数据不同，方案暂不可采用，辅助图使用已加载数据。</p>}
          {!query.pending && !query.error && result && !result.diagnosis && <div className="ain-ai-tool-reply"><ReplyText text={result.reply} />
            {/* 多步编排：把每一步的实际结果列出来（核查清单第 5 条：
                不要只返回"已执行两步"的文案，要能看出每步做了什么、结论是什么） */}
            {result.chain && result.chain.length > 0 && (
              <ol className="ain-ai-chain" aria-label="本次工具编排步骤">
                {result.chain.map((step, index) => (
                  <li key={`${step.tool}-${index}`} className={`ain-ai-chain-step${step.status && step.status !== 'ok' ? ' is-warn' : ''}`}>
                    <span className="ain-ai-chain-no">{index + 1}</span>
                    <div>
                      <strong>{step.title || step.tool}</strong>
                      {step.summary && <p>{step.summary}</p>}
                      {(step.window?.start || step.baseTime) && (
                        <p className="ain-ai-note">{
                          step.window?.start
                            ? `范围：${step.window.start} — ${step.window.end ?? ''}`
                            : `基准：${step.baseTime ?? ''}${step.horizonMinutes ? `（未来 ${step.horizonMinutes} 分钟）` : ''}`
                        }</p>
                      )}
                    </div>
                  </li>
                ))}
              </ol>
            )}
            {!workspace && <p className="ain-ai-result-scope">返回范围：{scopeLabel(result.scope)}</p>}</div>}
          {analysis && <>{(!workspace || result !== analysis) && <div className="ain-ai-analysis-caption">{result === analysis ? "本次研判" : "关联研判（保留）"}{result !== analysis && diagnosisQuestion && ` · ${diagnosisQuestion}`}</div>}
            <DiagnosisReply result={analysis} showScope={!workspace} onEvidence={selection => setEvidenceSelection(previous => ({ ...selection, id: (previous?.id ?? 0) + 1, diagnosisId: query.diagnosisId, requestId: query.requestId }))} /></>}
          {!forecastReview && !analysis && !result && !query.pending && !query.error && <p className="ain-ai-empty">{dataStatus ? `${dataStatus}，加载有效数据后开始研判。` : "先选择一个问题示例，或直接描述异常。回答与证据会展示在问题研判页。"}</p>}
        </div>
        {data && (diagnosis || result?.chart) && <DiagnosisCharts key={`${query.diagnosisId}-${result?.chart ? query.requestId : "evidence"}-${currentEvidenceSelection?.id ?? 0}`}
          data={data} diagnosis={analysisMismatch ? null : diagnosis} toolResult={mismatch ? null : result}
          spatial={spatial} spatialPending={spatialPending} spatialError={spatialError} onSpatialRetry={onSpatialRetry}
          busy={busy} onTool={tool} selection={currentEvidenceSelection} />}
        {!workspace && diagnosis?.state === "anomaly" && diagnosis.suggestion.trim() && <button type="button" className="ain-ai-adopt-link"
          disabled={busy || analysisMismatch || !data} onClick={() => { managementRef.current?.prepare({ source: "chat", result: diagnosis, candidate: diagnosis.candidates[0] || null }); }}>
          根据此研判准备方案 →</button>}
        {/* key 让范围变化时重建面板，从而丢弃旧的回测结果 */}
        {!workspace && !forecastMode && <ForecastComparePanel key={scope ? `${scope.scenario}|${scope.start}|${scope.end}` : "none"}
          scope={scope ?? null} disabled={disabled} archiveParentId={query.archiveId} />}

        {workspace && <nav className="ain-workspace-next" aria-label="研判后续操作">
          <span>{forecastPending || query.pending ? '研判完成后，可继续查看方案与效果。' : query.error ? query.contextRequest && analysis ? '辅助查询失败，原研判与方案仍可查看。' : '请重试本次研判后继续。' : '核对研判与证据后，继续选择方案并比较预计效果。'}</span>
          <button type="button" className="ain-button ain-primary" disabled={!workspace.canViewPlans} onClick={workspace.showPlans}>查看方案效果 →</button>
        </nav>}
      </section>
  );
  const planContent = (<>
{!forecastMode && (data && (analysis || (!query.pending && !query.error && result)) ? <ManagementModule key={`${data.generatedAt}-${query.diagnosisId}`} ref={managementRef} data={data}
        diagnosis={null} chatDiagnosis={diagnosis} focusedApId={focusedApId} disabled={busy} plans={analysisMismatch ? [] : analysis?.plans ?? result?.plans}
        sourceQuestion={diagnosisQuestion || query.question} pending={query.pending} archiveParentId={query.archiveId} />
        : <section className="ain-ai-management-empty" aria-label="处理建议与效果"><h3>处理建议与效果</h3><p>{query.pending ? "正在研判本次问题，方案将在结果返回后展示。" : query.error ? "本次研判未完成，请返回问题研判页重试。" : "本次回答未提供处理方案，可继续提问需要处理的异常及目标。"}</p></section>)}
  </>);
  return <section className="ain-ai-panel ain-ai-v3 ain-feature-module" id="ain-ai-module" aria-labelledby="ain-ai-title">
    <header className="ain-ai-heading">
      <div className="ain-ai-heading-title"><span className="ain-ai-symbol" aria-hidden="true">AI</span>
        <div><h2 id="ain-ai-title">AI 网络助手</h2><p>{workspace ? "在此提问，在工作区查看研判与方案" : "提问 → 原因与证据 → 处理方案 → 仿真效果对比"}</p></div></div>
      <div className="ain-ai-heading-actions">
        <button type="button" className={onOpenForecast ? `ain-forecast-trigger${!historical && forecastAlertCount > 0 ? " has-risk" : ""}` : "ain-forecast-placeholder"} disabled={historical || forecastDisabled || query.pending || dataLoading || !onOpenForecast}
          onClick={onOpenForecast} title={historical ? "历史查看期间不提供未来预测" : `查看频谱态势、未来风险及相关指标${forecastResultTime ? `；最近预测基准：${forecastResultTime}（北京时间），打开后更新` : "；尚未查询预测"}`}>{!historical && forecastAlertCount > 0 && <svg className="ain-forecast-risk-icon" viewBox="0 0 20 20" aria-hidden="true"><path d="M10 2 19 18H1Z" fill="currentColor" /><path d="M10 7v5M10 15v.1" stroke="white" strokeWidth="2" strokeLinecap="round" /></svg>}{forecastMode ? "调整预测范围" : "预测预警"} {!forecastMode && <span>{historical ? "历史不可用" : forecastLabel}</span>}</button>
        <span className="ain-ai-status" data-tone={query.error || dataError ? "error" : query.pending || dataLoading ? "info" : "neutral"}>{status}</span>
      </div>
    </header>
    {forecastMode && <div className="ain-forecast-task-mode"><strong>当前任务：预测风险研判</strong>
      <button type="button" className="ain-button" onClick={() => { setDraft(''); setSendNotice(''); onLeaveForecast?.(); }} disabled={!onLeaveForecast}>切换到现存分析</button>
    </div>}
    {replay ? <div className="ain-ai-scope ain-ai-replay-scope">
      <span>主时钟：{replay.currentTime}（北京时间）</span>
      {forecastMode ? <>
        <span>预测基准：{forecastContext?.result.baseTime ?? replay.viewTime}（北京时间）</span>
        {forecastContext && <span>关注时段：{(forecastContext.targetWindow ?? forecastContext.result.forecast).start} — {(forecastContext.targetWindow ?? forecastContext.result.forecast).end}</span>}
      </> : <>
        <span>查看截止时刻：{replay.viewTime ?? replay.analysisTime ?? replay.currentTime}（北京时间）</span>
        <span className="ain-ai-view-mode">查看状态：{historical ? "历史回顾" : followingLive ? "跟随当前" : "固定时刻分析"}</span>
      </>}
      {replay.mode ? <button type="button" className="ain-button" disabled={followingLive && disabled}
        onClick={() => { if (forecastMode) { setDraft(''); setSendNotice(''); } (followingLive ? replay.onFreeze : replay.onExit)?.(); }}>{forecastMode ? "退出预测，跟随主时钟" : followingLive ? "固定当前时刻" : "退出分析，返回当前"}</button>
        : <span>{replay.analysisTime ? `分析截至：${replay.analysisTime}（北京时间）` : "固定查看时刻后发送问题"}</span>}
    </div> : <p className="ain-ai-scope">分析范围：{scope ? scopeLabel(scope) : `${scenario} · 后端默认范围`}</p>}
    {forecastMode ? <details className="ain-question-time-settings"><summary>预测依据与提问说明</summary>
      {forecastContext && <p>参考历史：{forecastContext.result.history.start} — {forecastContext.result.history.end}（北京时间）</p>}
      <p>本区继续提问只分析所选预测风险，保持同一基准和关注时段；修改未来范围会打开预测预警，确认风险后再交给 AI。查询当前或历史问题，请先切换到现存分析。</p>
    </details> : workspace ? <details className="ain-question-time-settings"><summary>参考时间与提问说明</summary>{referenceControls}<TimeSelectionGuide historical={historical} /></details> : <>{referenceControls}<TimeSelectionGuide historical={historical} /></>}
    {!forecastMode && analysisNotice && <p className="ain-ai-warning" role="status">{analysisNotice}</p>}

    <div className="ain-query-examples" aria-label={forecastMode ? "预测问题示例" : "问题示例"}>
      {(forecastMode ? FORECAST_EXAMPLES : EXAMPLES).map(item => <button className="ain-query-example" key={item.label} type="button" disabled={forecastPending || query.pending || (historical && needsFutureForecast(item.question))}
        onClick={() => { setSendNotice(""); setDraft(`${scenario}：${item.question}`); inputRef.current?.focus(); }}>
        <strong>{item.label}</strong><span>{item.question}{historical && needsFutureForecast(item.question) && <small className="ain-example-pending">历史不可用</small>}</span></button>)}
    </div>
    <form className="ain-query-form" onSubmit={event => { event.preventDefault(); send(); }}>
      <label className="ain-input-label" htmlFor="ain-question">{forecastMode ? "继续提问所选预测风险" : "输入你的网络问题"}</label>
      <div className="ain-query-composer"><textarea id="ain-question" ref={inputRef} rows={2} maxLength={500} value={draft} disabled={query.pending || forecastPending}
        placeholder={forecastMode ? "例如：这条预测风险为什么会发生？有哪些预防方案？" : "分析异常及原因，给出处理方案并比较仿真预计效果。"}
        onChange={event => { setSendNotice(""); setDraft(event.target.value); event.target.style.height = "auto"; event.target.style.height = `${Math.min(event.target.scrollHeight, 100)}px`; }} onKeyDown={event => {
          if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && !event.nativeEvent.isComposing) {
            event.preventDefault(); send();
          }
        }} /><button type="submit" className="ain-button ain-primary" disabled={sendBlocked || (!forecastMode && currentEmpty && !questionTime) || !draft.trim()}>
          {query.pending || forecastPending ? "分析中…" : "发送"}</button></div>
      <div className="ain-query-input-meta"><span>{forecastMode ? "继续分析所选预测风险 · Ctrl / Cmd + Enter" : followingLive ? (replay?.canAnalyzeQuestionTime ? "历史时间或未来预测可直接发送；未来问题会固定查看时刻并打开预测。" : "可先编辑问题；固定当前时刻后发送，主时钟播放状态不变。") : "未来问题进入预测预警；点击示例填入后可编辑 · Ctrl / Cmd + Enter"}</span><span>{draft.length}/500</span></div>
      {/* 常驻操作提示（核查清单第 10 条）：主时钟在走时必须让人看见
          "时钟仍在走 / 分析时刻已固定"的区别，不能只靠一次性的警告条。 */}
      {followingLive && (
        <p className="ain-query-livehint" role="status">
          主时钟正在播放：直接输入问题发送即可，系统会**自动固定当前时刻**并在结果里标注"分析截至"时间；
          含历史时间或未来预测的问题按你给的时间处理。
        </p>
      )}
    </form>
    {forecastMode && <p className="ain-forecast-task-note">本次提问针对所选预测风险；查看当前或历史问题，请先切换分析模式。</p>}
    {sendNotice && <p className="ain-ai-warning" role="status">{sendNotice}</p>}
    {!forecastMode && currentEmpty && !disabled && !query.pending && <p className="ain-ai-note" role="status">当前窗口暂无采样，暂不能发起新分析；仍可查看及重试原截止时刻的分析。</p>}
    {!forecastMode && disabled && !query.pending && <p className="ain-ai-note" role={dataError ? "alert" : "status"}>{dataLoading ? "正在读取历史数据，完成后固定时刻即可提问。" : dataError ? `${dataError}；请在顶部重载数据。` : "当前查看时刻暂无有效采样，请返回当前或查看已到达的历史数据。"}</p>}

    {workspace ? <>
      {workspace.diagnosisHost && createPortal(diagnosisContent, workspace.diagnosisHost)}
      {workspace.plansHost && createPortal(planContent, workspace.plansHost)}
    </> : <div className="ain-ai-workspace">{diagnosisContent}{planContent}</div>}

    {!forecastMode && <details className="ain-question-tools"><summary>本次记录与导出</summary>
    <div className="ain-query-footer"><details className="ain-query-history-details">
      <summary>本次查询记录 <span>{query.history.length}/6</span></summary>
      <p className="ain-ai-note">最近 6 次 · 仅记录问题和回答摘要</p>
      <div className="ain-query-history">{!query.history.length && <p className="ain-ai-note">暂无查询记录</p>}
        {query.history.map(entry => <article key={entry.id} className={`ain-query-record${entry.failed ? " is-error" : ""}`}>
          <div className="ain-query-record-meta"><strong>{entry.question}</strong><time>{new Date(entry.createdAt).toLocaleTimeString("zh-CN", { hour12: false })}</time></div>
          <ReplyText text={entry.answer} /><p className="ain-ai-note">{entry.analysisMode === "history" ? "历史回顾" : "固定时刻分析"} · 分析截至：{entry.analysisTime ?? "未记录"}（北京时间）</p><p className="ain-ai-note">{entry.scope}（北京时间）</p></article>)}</div>
    </details>
      <span className="ain-report-actions">
        {/* 与"该时段全网报告"区分：本按钮**不重算**，只把屏幕上这一轮的上下文（问题、
            研判结论、方案、当前仿真配置版本）写成 Markdown，便于归档与答辩留证。 */}
        <button type="button" className="ain-ai-text-button" disabled={exporting || forecastMode}
          aria-label="导出本次分析快照：仅导出当前屏幕上的问题、结论、方案与配置版本，不重新研判"
          title={forecastMode ? "预测研判暂不支持快照导出" : "导出本次分析：绑定本次提问、结论、方案与当前仿真配置版本；不重新研判，数值以页面为准"}
          onClick={exportSnapshot}>{exporting ? "生成中…" : "导出本次分析"}</button>
        {/*
          按钮名必须说清导的是什么（核查清单第 11 条）：后端按"时段整窗"重新研判，
          不携带本次提问的 AP、也不带手改参数；写作"分析报告"会被当成"本次问答快照"。 */}
        <button type="button" className="ain-ai-text-button" disabled={!scope || exporting || forecastMode}
          aria-label="导出该时段全网报告：后端重新研判整窗，包含当前场景的全部 AP"
          title={forecastMode ? "预测研判可在分析记录中查看与导出" : "按当前展示时段导出该场景全部 AP 的报告：后端会重新研判整窗；本次提问的 AP 与手改参数不会被带入。如需保留本次问答与评估，请到分析记录页导出该条记录"}
          onClick={() => runExport("html")}>{exporting ? "生成中…" : "导出该时段全网报告"}</button>
        <button type="button" className="ain-ai-text-button" disabled={!scope || exporting || forecastMode}
          title="同上，Markdown 版"
          onClick={() => runExport("md")}>下载 Markdown</button>
      </span>
      <button type="button" className="ain-ai-text-button" disabled={query.pending || !query.history.length} onClick={query.clearHistory}>清空记录</button>
    </div>
    </details>}
    {!forecastMode && exportNotice && <p className="ain-ai-note" role="status">{exportNotice}</p>}
  </section>;
}
