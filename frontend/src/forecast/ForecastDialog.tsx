import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import ChartPanel from "../components/charts/ChartPanel";
import SpectrumViewPanel from "./SpectrumViewPanel";
import { forecastTrendOption } from "./charts";
import type { ForecastRequest, ForecastResult, ForecastHandoff } from "./types";
import "./ForecastDialog.css";

const level = { high: "高", medium: "中", low: "低" };
export default function ForecastDialog({ request, result, pending, error, onClose, onRefresh, onAnalyze, canAnalyze, fixedView = false }: {
  request: ForecastRequest; result: ForecastResult | null; pending: boolean; error: string;
  onClose: () => void; onRefresh: () => void; onAnalyze: (value: ForecastHandoff) => void; canAnalyze: boolean; fixedView?: boolean;
}) {
  const [selected, setSelected] = useState<string | null>(null), dialog = useRef<HTMLElement>(null);
  const alert = result?.alerts.find(a => a.id === selected) ?? result?.alerts[0];
  const impact = alert ? result?.impacts.find(s => s.id === alert.impactId) : result?.impacts[0];
  const trend = useMemo(() => result && impact ? forecastTrendOption(result, impact) : null, [result, impact]);
  const demo = result?.source === "demo";
  useEffect(() => {
    if (typeof document === "undefined") return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const overflow = document.body.style.overflow; document.body.style.overflow = "hidden";
    dialog.current?.focus();
    return () => { document.body.style.overflow = overflow; if (previous?.isConnected) previous.focus(); };
  }, []);
  const content = <div className="ain-forecast-overlay" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <section className="ain-forecast-dialog" ref={dialog} role="dialog" aria-modal="true" aria-labelledby="ain-forecast-title" aria-describedby="ain-forecast-context" tabIndex={-1}
      onKeyDown={event => {
        if (event.key === "Escape") { event.preventDefault(); onClose(); }
        if (event.key !== "Tab") return;
        const controls = dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled),select:not(:disabled),a[href],textarea:not(:disabled),[tabindex="0"]');
        const first = controls?.[0], last = controls?.[controls.length - 1];
        if (!first || !last) { event.preventDefault(); return; }
        if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog.current)) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && (document.activeElement === last || document.activeElement === dialog.current)) { event.preventDefault(); first.focus(); }
      }}>
      <header className="ain-forecast-dialog-heading"><div><span className="ain-forecast-eyebrow">AI-NetTune · 未来风险</span>
        <div className="ain-forecast-title-row"><h2 id="ain-forecast-title">预测预警</h2><span className="ain-forecast-count">{pending ? "生成中" : error ? "加载失败" : result?.status === "ready" ? `${demo ? "示例 · " : ""}${result.alerts.length} 项需关注` : "暂不能预测"}</span></div></div>
        <div className="ain-forecast-dialog-actions"><button type="button" disabled={pending} onClick={onRefresh}>↻ 刷新预测</button><button type="button" aria-label="关闭预测预警" onClick={onClose}>×</button></div></header>
      <div className="ain-forecast-dialog-body" aria-busy={pending}>
        <div className="ain-forecast-context" id="ain-forecast-context"><div><strong>预测基准：{request.baseTime}</strong><span>北京时间 · 结果固定，主时钟继续运行</span></div>
          <p>参考历史：{request.history.start} — {request.history.end}</p><p>预测未来：{request.baseTime}之后 {request.horizonMinutes} 分钟 · {fixedView ? "刷新沿用固定分析时刻；返回当前后可预测最新时刻" : "点击刷新获取最新主时钟时刻的预测"}</p></div>
        {demo && <p className="ain-forecast-demo" role="status">示例预览：预测模型尚未接入，以下数值与阈值仅用于展示布局，不代表当前网络预测。</p>}
        {pending && <div className="ain-forecast-state" role="status"><strong>正在生成预测…</strong><p>仅使用预测基准时刻及以前的数据。</p></div>}
        {error && <div className="ain-forecast-state is-error" role="alert"><strong>预测暂未返回</strong><p>{error}</p><button type="button" onClick={onRefresh}>重试预测</button></div>}
        {result && !pending && !error && <>
          {result.status !== "ready" ? <div className="ain-forecast-state" role="status"><strong>{result.status === "insufficient_data" ? "参考数据不足" : "预测服务暂不可用"}</strong><p>{result.message || "当前不能生成可靠预测，请稍后重试。"}</p></div> : <>
            {result.alerts.length > 1 && <div className="ain-forecast-risk-tabs" aria-label="选择关注风险">{result.alerts.map(a => <button type="button" key={a.id} aria-label={`查看${a.target}的预测风险`} aria-pressed={a.id === alert?.id} onClick={() => setSelected(a.id)}>{a.target} · {a.title}</button>)}</div>}
            <section className={`ain-forecast-summary${alert ? "" : " is-normal"}`} aria-label="预警摘要">
              <div><h3>{alert ? `${alert.target} · ${alert.title}` : "预测范围内未发现明显风险"}</h3>{alert && <span data-level={alert.severity}>{level[alert.severity]}风险</span>}</div>
              <p>{alert?.summary || result.message || "可继续查看频谱态势和指标变化，定期刷新预测。"}</p>
              {alert && <small>预计时段：{alert.start} — {alert.end} · 详细原因与建议可交给AI研判</small>}
            </section>
            <div className="ain-forecast-chart-stack">
              <SpectrumViewPanel result={result} alert={alert} />
              {impact && trend ? <section className="ain-forecast-impact"><div className="ain-forecast-impact-heading"><h3>可能影响 · {impact.label}预测</h3><span>{impact.targetLabel}{demo ? " · 示例数据" : ""}</span></div>
                <ChartPanel title={`${impact.label}预测`} description={`${impact.unit} · 实线为历史，虚线为预测`} height={144} option={trend} />
                <p className="ain-forecast-chart-legend"><span>━ 历史观测</span><span>┄ 未来预测</span>{impact.predicted.some(p => p.lower != null && p.upper != null) && <span>▰ 预测区间</span>}{impact.threshold && <span className="is-threshold">{impact.threshold.direction === "below" ? "低于" : "高于"}阈值需关注</span>}</p>
                {!impact.predicted.length && <p className="ain-forecast-chart-note">暂无该指标的未来预测，当前仅显示历史。</p>}</section>
                : <section className="ain-forecast-chart-empty is-small"><h3>相关指标预测</h3><p>尚未提供与当前风险关联的指标预测。</p></section>}
            </div>
            <p className="ain-forecast-result-note">{demo ? "设计示例" : result.source === "simulation" ? "仿真数据预测" : "监测数据预测"} · 结果生成于 {result.generatedAt} · 频段繁忙本身不能直接证明干扰</p>
          </>}
        </>}
      </div>
      <footer className="ain-forecast-dialog-footer"><span>{demo ? "示例研判不会调用大模型" : "详细原因、证据与预防方案在AI区查看"}</span><div><button type="button" className="ain-forecast-primary" disabled={!canAnalyze || pending || Boolean(error) || result?.status !== "ready"} onClick={() => { if (result) onAnalyze({ result, alertId: alert?.id ?? null }); }}>交给AI详细研判</button><button type="button" onClick={onClose}>关闭</button></div></footer>
    </section>
  </div>;
  return typeof document === "undefined" ? content : createPortal(content, document.body);
}
