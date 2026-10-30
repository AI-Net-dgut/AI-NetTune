import { record,forecastRecordIds } from '../history/store';
import { useEffect, useState } from "react";
import { ReplyText } from "../chat/DiagnosisAnswer";
import { analyzeForecast } from "./api";
import ForecastPlans from "./ForecastPlans";
import type { ForecastAnalysis, ForecastHandoff } from "./types";

export default function ForecastReview({ handoff }: { handoff: ForecastHandoff }) {
  const [reviewRecordId, setReviewRecordId] = useState<string>("");
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{ pending: boolean; result: ForecastAnalysis | null; error: string }>({ pending: true, result: null, error: "" });
  const { result, alertId, question, targetWindow } = handoff, alert = result.alerts.find(a => a.id === alertId);
  useEffect(() => {
    const abort = new AbortController(); let active = true;
    const submittedAt = new Date().toISOString();
    // 组件以移交ID及重试次数为key重建；旧组件卸载后不能写入新结果。
    void analyzeForecast(result, alertId, abort.signal, undefined, undefined, { question, targetWindow }).then(reply => {
      if (active) {
        // 记住这条研判记录 id：预测方案/仿真要挂在它下面（审计 10-29 第 2 项，否则记录里看不到方案）
        const recordId = record({kind:"forecast-analysis",createdAt:submittedAt,parentId:forecastRecordIds.get(result),question:question||alert?.title||"预测风险详细研判",scope:{scenario:result.scenario,start:result.history.start,end:result.history.end},analysisTime:result.baseTime,status:"completed",payload:{handoff,reply:reply.reply,source:reply.source}});
        setReviewRecordId(recordId);
        setState({ pending: false, result: reply, error: "" });
      }
    }).catch(reason => { if (active) setState({ pending: false, result: null, error: reason instanceof Error ? reason.message : "预测研判失败" }); });
    return () => { active = false; abort.abort(); };
  }, [result, alertId, attempt, question, targetWindow, handoff, alert?.title]);
  const reply = state.result?.reply.trim() ?? "";
  const firstParagraph = reply.split(/\n\s*\n/)[0] ?? "";
  const hasMore = reply !== firstParagraph || firstParagraph.length > 180;
  // 摘录去掉成对的加粗标记再截取，避免截断后露出半个 Markdown 标记。
  const plainExcerpt = firstParagraph.replace(/\*\*([^*]+)\*\*/g, "$1");
  const excerpt = hasMore ? (plainExcerpt.length > 180 ? `${plainExcerpt.slice(0, 180)}…` : plainExcerpt) : reply;
  return <article className="ain-forecast-review" aria-label="预测风险研判" aria-busy={state.pending}>
    <header><strong>{result.source === "demo" ? "示例 · " : ""}预测风险研判</strong><span>{alert ? (alert.title.includes(alert.target) ? alert.title : `${alert.target} · ${alert.title}`) : "整体预测态势"}</span></header>
    {question && <p className="ain-ai-user-question"><strong>本次未来提问</strong>{question}</p>}
    {targetWindow && <p className="ain-forecast-review-scope">关注时段：{targetWindow.start} — {targetWindow.end}（北京时间）</p>}
    <p className="ain-forecast-review-scope">基准：{result.baseTime} · 预测至：{result.forecast.end}（北京时间）</p>
    {state.pending && <p role="status">正在研判这份预测结果…</p>}
    {state.error && <div role="alert"><p>{state.error}</p><button type="button" onClick={() => { setState({ pending: true, result: null, error: "" }); setAttempt(n => n + 1); }}>重试预测研判</button></div>}
    {state.result && <div className="ain-forecast-verdict">
      <p className="ain-forecast-review-source">{state.result.source === "demo" ? "界面示例 · 未调用大模型" : state.result.source === "rule" ? "规则研判" : "AI研判"}{hasMore ? " · 研判摘录" : ""}</p>
      <ReplyText text={excerpt} />
      {hasMore && <details className="ain-forecast-details"><summary>查看完整研判</summary><ReplyText text={reply} /></details>}
      {result.integrationNotice && <p className="ain-forecast-brief-note">依据预警摘要与历史指标，尚未结合完整频谱数值。</p>}
    </div>}
    {/* 预测风险的处置方案：与「处理建议与效果」里现存问题的方案分开（审计第 6 条） */}
    <ForecastPlans handoff={handoff} parentId={reviewRecordId || undefined} />
  </article>;
}
