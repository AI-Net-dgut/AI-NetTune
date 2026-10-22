import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import ChartPanel from "../components/charts/ChartPanel";
import SpectrumViewPanel from "./SpectrumViewPanel";
import SpatialFrameGrid from "./SpatialFrameGrid";
import { forecastTrendOption } from "./charts";
import type { ForecastRequest, ForecastResult, ForecastHandoff } from "./types";
import { FORECAST_HORIZONS } from './config';
import { toEpoch } from '../services/backend';
import "./ForecastDialog.css";

const level = { high: "高", medium: "中", low: "低" };
export default function ForecastDialog({ request, result, pending, error, onClose, onRefresh, onAnalyze, canAnalyze, fixedView = false,
  follow = false, onToggleFollow, followStepMinutes = 5, onHorizonChange, currentTime }: {
  request: ForecastRequest; result: ForecastResult | null; pending: boolean; error: string;
  onClose: () => void; onRefresh: () => void; onAnalyze: (value: ForecastHandoff) => void; canAnalyze: boolean; fixedView?: boolean;
  follow?: boolean; onToggleFollow?: () => void; followStepMinutes?: number;
  onHorizonChange?: (minutes: number) => void; currentTime?: string;
}) {
  const [selected, setSelected] = useState<string | null>(null), dialog = useRef<HTMLElement>(null);
  const [globalMetric, setGlobalMetric] = useState("");
  const scopedAlerts = (result?.alerts ?? []).filter(a => !request.targetWindow
    || (a.start <= request.targetWindow.end && a.end > request.targetWindow.start));
  const alert = scopedAlerts.find(a => a.id === selected) ?? scopedAlerts[0];
  const linkedImpact = alert ? result?.impacts.find(s => s.id === alert.impactId) : undefined;
  const globalImpacts = result?.integrationNotice ? result.impacts.filter(s => s.targetLabel === "全网") : [];
  const globalImpact = globalImpacts.find(s => s.id === globalMetric) ?? globalImpacts[0];
  const impact = linkedImpact ?? (alert ? globalImpact : globalImpact ?? result?.impacts[0]);
  const globalOnly = Boolean(result?.integrationNotice && !linkedImpact && impact?.targetLabel === "全网");
  // 这里必须保留手动 memo：弹窗每秒跟随主时钟重渲染（currentTime 变化），
  // 每次重建 ECharts option 会让图表反复 setOption。result/impact 来自 props，
  // 只会被整体替换、不会被就地修改，依赖是安全的。
  // 用区块禁用而不是 disable-next-line：该规则报在依赖数组那一行，不在 const 声明行。
  /* eslint-disable react-hooks/preserve-manual-memoization -- 依赖对象不会被就地 mutate，见上 */
  const trend = useMemo(() => result && impact && [...impact.history, ...impact.predicted].some(p => p.value !== null)
    ? forecastTrendOption(result, impact) : null, [result, impact]);
  /* eslint-enable react-hooks/preserve-manual-memoization */
  const demo = result?.source === "demo";
  const noRiskToReview = Boolean(result && (request.targetWindow || result.integrationNotice) && !scopedAlerts.length);
  const horizons = [...new Set<number>([...FORECAST_HORIZONS, request.horizonMinutes])].sort((a, b) => a - b);
  const shownBaseTime = result?.baseTime ?? request.baseTime;
  const shownHistory = result?.history ?? request.history;
  const ageMinutes = currentTime && result ? Math.max(0, Math.floor((toEpoch(currentTime) - toEpoch(result.baseTime)) / 60_000)) : 0;
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
        <div className="ain-forecast-title-row"><h2 id="ain-forecast-title">预测预警</h2><span className="ain-forecast-count">{pending ? "生成中" : error ? "更新失败" : result?.status === "ready" ? `${demo ? "示例 · " : ""}${scopedAlerts.length} 项需关注` : "暂不能预测"}</span></div></div>
        <div className="ain-forecast-dialog-actions">
          {onToggleFollow && !fixedView && <button type="button" aria-pressed={follow} onClick={onToggleFollow}
            title={`开启后，在主时钟播放中每 ${followStepMinutes} 分钟自动重新预测；暂停或关闭时固定在当前基准，需手动刷新`}>{follow ? "⏱ 跟随主时钟" : "已固定时刻"}</button>}
          <button type="button" disabled={pending} onClick={onRefresh}>↻ 刷新预测</button>
          <button type="button" aria-label="关闭预测预警" onClick={onClose}>×</button>
        </div></header>
      <div className="ain-forecast-dialog-body" aria-busy={pending}>
        <div className="ain-forecast-context" id="ain-forecast-context"><div><strong>当前结果基准：{shownBaseTime}</strong><span>北京时间 · {fixedView ? "沿用固定分析时刻，需手动刷新" : follow ? `主时钟播放中每 ${followStepMinutes} 分钟自动刷新；暂停时不刷新` : "结果固定，需手动刷新"}</span></div>
          <p>参考历史：{shownHistory.start} — {shownHistory.end}</p>
          <p>预测范围：{result ? `${result.forecast.start} — ${result.forecast.end}` : `${request.baseTime}之后${request.horizonMinutes}分钟`}</p>
          {request.question && <p>本次未来提问：{request.question}</p>}
          {request.targetWindow && <p>关注时段：{request.targetWindow.start} — {request.targetWindow.end}；下方风险仅显示与关注时段相交的项目。</p>}
          {ageMinutes > 0 && <p>结果基准距主时钟 {ageMinutes} 分钟{fixedView ? '，当前为固定查看，需手动刷新。' : follow ? '，主时钟播放中会自动更新；暂停时不刷新。' : '，可刷新获取最新结果。'}</p>}
          {onHorizonChange && <label className="ain-forecast-horizon">预测时长
            <select aria-label="预测时长" value={request.horizonMinutes} disabled={pending} onChange={event => onHorizonChange(Number(event.target.value))}>
              {horizons.map(minutes => <option key={minutes} value={minutes}>未来{minutes}分钟{minutes === 1440 ? '（24小时）' : ''}</option>)}
            </select></label>}
          <p>按完整分钟取数；频谱历史最多12小时。修改时长后按当前查看基准重新预测。</p></div>
        {demo && <p className="ain-forecast-demo" role="status">示例预览：预测模型尚未接入，以下数值与阈值仅用于展示布局，不代表当前网络预测。</p>}
        {pending && <div className="ain-forecast-state" role="status"><strong>正在生成预测…</strong><p>请求基准：{request.baseTime}；仅使用该基准及以前的数据。{result && '下方保留上一轮有效结果，其基准以上方标识为准。'}</p></div>}
        {error && <div className="ain-forecast-state is-error" role="alert"><strong>预测暂未更新</strong><p>{error}{result && ' 下方是上一轮结果，尚未更新。'}</p><button type="button" onClick={onRefresh}>重试预测</button></div>}
        {result && <>
          {result.integrationNotice && <p className="ain-forecast-chart-note" role="note">{result.integrationNotice}</p>}
          {!result.integrationNotice && result.method && <p className="ain-forecast-chart-note">预测方法：{result.method}{result.limitations?.length ? `；限制：${result.limitations.join('；')}` : ''}</p>}
          {result.status !== "ready" ? <div className="ain-forecast-state" role="status"><strong>{result.status === "insufficient_data" ? "参考数据不足" : "预测服务暂不可用"}</strong><p>{result.message || "当前不能生成可靠预测，请稍后重试。"}</p></div> : <>
            {scopedAlerts.length > 1 && <div className="ain-forecast-risk-tabs" aria-label="选择关注风险">{scopedAlerts.map(a => <button type="button" key={a.id} aria-label={`查看${a.target}的预测风险`} aria-pressed={a.id === alert?.id} onClick={() => setSelected(a.id)}>{a.target} · {a.title}</button>)}</div>}
            <section className={`ain-forecast-summary${alert ? "" : " is-normal"}`} aria-label="预警摘要">
              <div><h3>{alert ? `${alert.target} · ${alert.title}` : request.targetWindow ? '关注时段未触发风险提示' : result.integrationNotice ? "当前未触发风险提示" : "预测范围内未发现明显风险"}</h3>{alert && <span data-level={alert.severity}>{level[alert.severity]}风险</span>}</div>
              <p>{alert?.summary || (request.targetWindow ? '没有与关注时段相交的预警；这不表示未来一定没有故障。' : result.message) || "可继续查看频谱态势和指标变化，定期刷新预测。"}</p>
              {alert && <small>预计时段：{alert.start} — {alert.end} · 详细原因与建议可交给AI研判</small>}
            </section>
            <div className="ain-forecast-chart-stack">
              {/* 区域电磁态势序列提到弹窗层级、放在频谱三视图之前：
                  它跳出"频谱曲线/功率时频/占用态势"的 tab 体系，所以点开预测预警
                  第一眼就能看到（含大图/时间轴/播放/预测时长）。
                  原来它挂在 SpectrumChart 内部，只有在切到"功率时频/占用态势"
                  tab 并向下滚动后才可见，首屏看不到。 */}
              <SpatialFrameGrid scenario={result.scenario} baseTime={result.baseTime}
                horizonMinutes={request.horizonMinutes} />
              <SpectrumViewPanel result={result} alert={alert} focusTime={request.targetWindow?.start} />
              {/* 「全网参考 · SINR预测」块已下线：后端只给全网均值，单标量序列在
                  60 分钟窗口内无显著趋势（_gated_slope 判为 0），画出来是一条平线，
                  看着像 bug 又没有信息量。区域电磁态势图已按指标（含 SINR）提供
                  空间维度的预测，这里保留与告警联动的 per-AP 影响图。 */}
              {impact && trend && !globalOnly ? <section className="ain-forecast-impact"><div className="ain-forecast-impact-heading"><h3>可能影响 · {impact.label}预测</h3><span>{impact.targetLabel}{demo ? " · 示例数据" : ""}</span></div>
                {globalOnly && <><p className="ain-forecast-chart-note">后端仅提供全网均值，此图不是所选AP的专属影响预测。</p><label>参考指标 <select aria-label="选择全网参考指标" value={impact.id} onChange={event => setGlobalMetric(event.target.value)}>{globalImpacts.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}</select></label></>}
                <ChartPanel title={`${impact.label}预测`} description={`${impact.unit} · 实线为历史，虚线为预测`} height={144} option={trend} />
                <p className="ain-forecast-chart-legend"><span>━ 历史观测</span><span>┄ 未来预测</span>{impact.predicted.some(p => p.lower != null && p.upper != null) && <span>▰ 预测区间</span>}{impact.threshold && <span className="is-threshold">{impact.threshold.direction === "below" ? "低于" : "高于"}阈值需关注</span>}</p>
                {!impact.predicted.length && <p className="ain-forecast-chart-note">暂无该指标的未来预测，当前仅显示历史。</p>}</section>
                : null}
            </div>
            <p className="ain-forecast-result-note">{demo ? "设计示例" : result.source === "simulation" ? "仿真数据预测" : "监测数据预测"} · 结果生成于 {result.generatedAt} · 频段繁忙本身不能直接证明干扰</p>
          </>}
        </>}
      </div>
      <footer className="ain-forecast-dialog-footer"><span>{demo ? "示例研判不会调用大模型" : noRiskToReview && result?.status === "ready" ? "暂无风险可移交；当前网络问题可在AI区提问" : "详细原因、证据与预防方案在AI区查看"}</span><div><button type="button" className="ain-forecast-primary" disabled={!canAnalyze || pending || Boolean(error) || result?.status !== "ready" || noRiskToReview} onClick={() => { if (result && !noRiskToReview) onAnalyze({ result, alertId: alert?.id ?? null, question: request.question, targetWindow: request.targetWindow }); }}>交给AI详细研判</button><button type="button" onClick={onClose}>关闭</button></div></footer>
    </section>
  </div>;
  return typeof document === "undefined" ? content : createPortal(content, document.body);
}
