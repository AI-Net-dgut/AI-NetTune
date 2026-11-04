import { record,forecastRecordIds } from '../history/store';
import { useContext, useEffect, useState } from "react";
import { createPortal } from 'react-dom';
import { WorkspacePresentationContext } from '../pages/workbench/WorkspacePresentationContext';
import AnalysisNarrative from "../chat/AnalysisNarrative";
import { analysisSections, withoutQuestionEcho } from "../chat/analysisText";
import { analyzeForecast } from "./api";
import ForecastPlans from "./ForecastPlans";
import type { ForecastAnalysis, ForecastHandoff } from "./types";

export default function ForecastReview({ handoff, reviewKey = '', onStatusChange }: {
  handoff: ForecastHandoff; reviewKey?: string; onStatusChange?: (key: string, pending: boolean) => void;
}) {
  const workspace = useContext(WorkspacePresentationContext);
  const [reviewRecordId, setReviewRecordId] = useState<string>("");
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{ pending: boolean; result: ForecastAnalysis | null; error: string }>({ pending: true, result: null, error: "" });
  const { result, alertId, question, targetWindow } = handoff, alert = result.alerts.find(a => a.id === alertId);
  useEffect(() => {
    const abort = new AbortController(); let active = true;
    onStatusChange?.(reviewKey, true);
    const submittedAt = new Date().toISOString();
    // 组件以移交ID及重试次数为key重建；旧组件卸载后不能写入新结果。
    void analyzeForecast(result, alertId, abort.signal, undefined, undefined, { question, targetWindow }).then(reply => {
      if (active) {
        // 记住这条研判记录 id：预测方案/仿真要挂在它下面（审计 10-29 第 2 项，否则记录里看不到方案）
        const recordId = record({kind:"forecast-analysis",createdAt:submittedAt,parentId:forecastRecordIds.get(result),question:question||alert?.title||"预测风险详细研判",scope:{scenario:result.scenario,start:result.history.start,end:result.history.end},analysisTime:result.baseTime,status:"completed",payload:{handoff,reply:reply.reply,source:reply.source}});
        setReviewRecordId(recordId);
        setState({ pending: false, result: reply, error: "" });
        onStatusChange?.(reviewKey, false);
      }
    }).catch(reason => { if (active) {
      setState({ pending: false, result: null, error: reason instanceof Error ? reason.message : "预测研判失败" });
      onStatusChange?.(reviewKey, false);
    } });
    return () => { active = false; abort.abort(); };
  }, [result, alertId, attempt, question, targetWindow, handoff, alert?.title, reviewKey, onStatusChange]);
  const reply = state.result?.reply.trim() ?? "";
  return <article className="ain-forecast-review" aria-label="预测风险研判" aria-busy={state.pending}>
    {!workspace && <header><strong>{result.source === "demo" ? "示例 · " : ""}预测风险研判</strong><span>{alert ? (alert.title.includes(alert.target) ? alert.title : `${alert.target} · ${alert.title}`) : "整体预测态势"}</span></header>}
    {!workspace && question && <p className="ain-ai-user-question"><strong>本次未来提问</strong>{question}</p>}
    {!workspace && targetWindow && <p className="ain-forecast-review-scope">关注时段：{targetWindow.start} — {targetWindow.end}（北京时间）</p>}
    {!workspace && <p className="ain-forecast-review-scope">基准：{result.baseTime} · 预测至：{result.forecast.end}（北京时间）</p>}
    {state.pending && <p role="status">正在研判这份预测结果…</p>}
    {state.error && <div role="alert"><p>{state.error}</p><button type="button" onClick={() => { setState({ pending: true, result: null, error: "" }); setAttempt(n => n + 1); }}>重试预测研判</button></div>}
    {state.result && <AnalysisNarrative
      source={state.result.source === "demo" ? "界面示例 · 未调用大模型" : state.result.source === "rule" ? "规则研判" : "AI 研判"}
      sections={analysisSections(withoutQuestionEcho(reply, question) || "接口未返回研判文字，请重试。")}
      note={result.integrationNotice ? "本次采用的历史与频谱证据，以研判正文说明为准。" : undefined} />}
    {state.result && result.integrationNotice && <p className="ain-forecast-brief-note">预测基于历史观测，原因与效果仍需验证；证据是否充分以本次研判说明为准。</p>}
    {/* 预测风险的处置方案：与「处理建议与效果」里现存问题的方案分开（审计第 6 条） */}
    {workspace
      ? workspace.plansHost && createPortal(<ForecastPlans handoff={handoff} parentId={reviewRecordId || undefined} />, workspace.plansHost)
      : <ForecastPlans handoff={handoff} parentId={reviewRecordId || undefined} />}
  </article>;
}
