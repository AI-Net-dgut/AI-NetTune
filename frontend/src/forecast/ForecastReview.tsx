import { useEffect, useState } from "react";
import { ReplyText } from "../chat/DiagnosisAnswer";
import { analyzeForecast } from "./api";
import type { ForecastAnalysis, ForecastHandoff } from "./types";

export default function ForecastReview({ handoff }: { handoff: ForecastHandoff }) {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{ pending: boolean; result: ForecastAnalysis | null; error: string }>({ pending: true, result: null, error: "" });
  const { result, alertId } = handoff, alert = result.alerts.find(a => a.id === alertId);
  useEffect(() => {
    const abort = new AbortController(); let active = true;
    // 组件以移交ID及重试次数为key重建；旧组件卸载后不能写入新结果。
    void analyzeForecast(result, alertId, abort.signal).then(reply => {
      if (active) setState({ pending: false, result: reply, error: "" });
    }).catch(reason => { if (active) setState({ pending: false, result: null, error: reason instanceof Error ? reason.message : "预测研判失败" }); });
    return () => { active = false; abort.abort(); };
  }, [result, alertId, attempt]);
  return <article className="ain-forecast-review" aria-label="预测风险研判" aria-busy={state.pending}>
    <header><strong>{result.source === "demo" ? "示例 · " : ""}预测风险研判</strong><span>{alert ? `${alert.target} · ${alert.title}` : "整体预测态势"}</span></header>
    <p className="ain-forecast-review-scope">基准：{result.baseTime} · 预测至：{result.forecast.end}（北京时间）</p>
    {state.pending && <p role="status">正在研判这份预测结果，主时钟继续运行…</p>}
    {state.error && <div role="alert"><p>{state.error}</p><button type="button" onClick={() => { setState({ pending: true, result: null, error: "" }); setAttempt(n => n + 1); }}>重试预测研判</button></div>}
    {state.result && <><p className="ain-forecast-review-source">{state.result.source === "demo" ? "界面示例 · 未调用大模型" : state.result.source === "rule" ? "规则研判" : "AI研判"}</p><ReplyText text={state.result.reply} /></>}
  </article>;
}
