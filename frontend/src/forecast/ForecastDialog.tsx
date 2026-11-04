import { forecastRecordIds } from '../history/store';
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { ReplyText } from "../chat/DiagnosisAnswer";
import ChartPanel from "../components/charts/ChartPanel";
import SpectrumViewPanel from "./SpectrumViewPanel";
import SpatialFrameGrid from "./SpatialFrameGrid";
import { forecastTrendOption } from "./charts";
import type { ForecastRequest, ForecastResult, ForecastHandoff } from "./types";
import { FORECAST_HORIZONS } from './config';
import { fetchSimConfig, toEpoch } from '../services/backend';
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
  // 当前生效的仿真配置（闭环切片 4b）：仅用于在标题旁标注，取不到就不显示
  const [simActiveId, setSimActiveId] = useState("");
  useEffect(() => {
    let alive = true;
    fetchSimConfig()
      .then(status => { if (alive) setSimActiveId(status.active?.id ?? ""); })
      .catch(() => { if (alive) setSimActiveId(""); });
    return () => { alive = false; };
  }, []);
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
  // 关注时段是否仍落在当前预测范围内（复查 P1-2）：改成长度后范围若装不下原时段，
  // 必须明确告知用户"原关注时段已超出本次预测范围"，而不是静默换掉分析对象。
  const windowUncovered = Boolean(request.targetWindow && result
    && (result.forecast.end < request.targetWindow.end || result.forecast.start > request.targetWindow.start));
  const ageMinutes = currentTime && result ? Math.max(0, Math.floor((toEpoch(currentTime) - toEpoch(result.baseTime)) / 60_000)) : 0;
  /* 提前量：alert.start 是后端算出的"预计越阈起始时刻"（= 基准 + first_minute）。
     它相对基准时刻的差值只是预计越阈时间间隔，不能替代真实提前预警效果验证。
     之前只显示"预计时段"，用户看不出这是几十分钟后的预测而非事后确认。 */
  const leadMinutes = alert && result
    ? Math.round((toEpoch(alert.start) - toEpoch(result.baseTime)) / 60_000) : null;
  const leadText = leadMinutes == null ? ""
    : leadMinutes > 0 ? `按当前趋势约 ${leadMinutes} 分钟后越阈（预计 ${alert?.start?.slice(11, 16)}）`
    : "当前频谱风险与后续走势，是否确诊以本条观测依据为准";
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
        <div className="ain-forecast-title-row"><h2 id="ain-forecast-title">预测预警</h2><span className="ain-forecast-count">{pending ? "生成中" : error ? "更新失败" : result?.status === "ready" ? `${demo ? "示例 · " : ""}${scopedAlerts.length} 项需关注` : "暂不能预测"}</span>
          {/* 仿真配置生效标注（闭环切片 4b）：只读 /api/sim/config，失败静默不显示；
              不做任何数据计算，纯粹告诉用户"本轮预测是在 vN 配置下算的"。 */}
          {simActiveId && <span className="ain-forecast-sim-tag" title="该配置只影响生效时刻之后的数据，可在方案面板回滚">仿真配置 {simActiveId}</span>}
        </div></div>
        <div className="ain-forecast-dialog-actions">
          {onToggleFollow && !fixedView && <button type="button" aria-pressed={follow} onClick={onToggleFollow}
            title={`开启后，在主时钟播放中每 ${followStepMinutes} 分钟自动重新预测；暂停或关闭时固定在当前基准，需手动刷新`}>{follow ? "⏱ 跟随主时钟" : "已固定时刻"}</button>}
          <button type="button" disabled={pending} onClick={onRefresh}>↻ 刷新预测</button>
          <button type="button" aria-label="关闭预测预警" onClick={onClose}>×</button>
        </div></header>
      <div className="ain-forecast-dialog-body" aria-busy={pending}>
        <div className="ain-forecast-context" id="ain-forecast-context">
          <div className="ain-forecast-context-heading"><strong>结果基准：{shownBaseTime}</strong>
            <span>{fixedView ? "固定分析时刻" : follow ? `播放中每 ${followStepMinutes} 分钟更新` : "固定查看"}{ageMinutes > 0 ? ` · 距主时钟 ${ageMinutes} 分钟` : ""}</span></div>
          <dl className="ain-forecast-time-rows">
            <div><dt>参考历史</dt><dd>{shownHistory.start} — {shownHistory.end}</dd></div>
            <div><dt>预测范围</dt><dd>{result ? `${result.forecast.start} — ${result.forecast.end}` : `${request.baseTime}之后${request.horizonMinutes}分钟`}</dd></div>
          </dl>
          {/* 复查 P1-9：预计发生时间是**趋势外推**，误差可达数分钟，不能呈现为精确倒计时。 */}
          <p className="ain-ai-note">口径：预计越阈时刻为<strong>趋势估计</strong>（基于近期走势外推），
            与真实越阈时刻可能有数分钟偏差；提高刷新频率不会改变该误差，请按"提前量参考"使用。</p>
          {request.question && <p>本次提问：{request.question}</p>}
          {request.targetWindow && <p>关注时段：{request.targetWindow.start} — {request.targetWindow.end}</p>}
          {windowUncovered && <p className="ain-ai-warning" role="status">
            关注时段 {request.targetWindow!.start} — {request.targetWindow!.end} 已超出本次预测范围，
            请加长预测时长或重新选择关注时段；下方风险按当前预测范围展示，不代表原关注时段。
          </p>}
          <div className="ain-forecast-range-row">
            {onHorizonChange && <label className="ain-forecast-horizon">查看未来
              <select aria-label="查看未来范围" value={request.horizonMinutes} disabled={pending} onChange={event => onHorizonChange(Number(event.target.value))}>
                {horizons.map(minutes => <option key={minutes} value={minutes}>
                  {minutes}分钟{minutes === 1440 ? '（24小时）' : ''}{minutes > 360 ? ' · 长期外推' : ''}
                </option>)}
              </select></label>}
            <span>覆盖范围 ≠ 提前预警时间</span>
            <Link to="/#forecast-guide">使用说明</Link>
          </div>
        </div>
        {demo && <p className="ain-forecast-demo" role="status">示例预览：预测模型尚未接入，以下数值与阈值仅用于展示布局，不代表当前网络预测。</p>}
        {pending && <div className="ain-forecast-state" role="status"><strong>正在生成预测…</strong><p>请求基准：{request.baseTime}；仅使用该基准及以前的数据。{result && '下方保留上一轮有效结果，其基准以上方标识为准。'}</p></div>}
        {error && <div className="ain-forecast-state is-error" role="alert"><strong>预测暂未更新</strong><p>{error}{result && ' 下方是上一轮结果，尚未更新。'}</p><button type="button" onClick={onRefresh}>重试预测</button></div>}
        {result && <>
          <details className="ain-forecast-details ain-forecast-explanation">
            <summary>预测依据与限制{result.integrationNotice ? " · 当前采用趋势外推" : ""}</summary>
            {result.integrationNotice && <ReplyText text={result.integrationNotice} />}
            {result.method && <p>预测方法：{result.method}</p>}
            {result.limitations?.length ? <p>限制：{result.limitations.join('；')}</p> : null}
            <p>查看未来范围决定覆盖至哪个时刻，不保证提前同样时间发现故障。越阈时间按当前趋势估计，提前预警能力需历史回测验证；较长范围仅供趋势参考。</p>
            <p>按完整分钟取数，频谱历史最多12小时；修改范围后按当前基准重新预测。跟随更新仅在弹窗打开且主时钟播放时进行，暂停或关闭后停止自动更新。</p>
            {request.targetWindow && <p>本次仅展示与关注时段相交的风险。</p>}
            {result.integrationNotice && <p>频谱风险阈值：逐频段功率 &lt; −95 dBm，至少40/50个频段越阈且连续不少于3分钟，或占用率 &gt; 80%。网络 KPI 异常可另交给 AI 研判核对。</p>}
          </details>
          {result.status !== "ready" ? <div className="ain-forecast-state" role="status"><strong>{result.status === "insufficient_data" ? "参考数据不足" : "预测服务暂不可用"}</strong><p>{result.message || "当前不能生成可靠预测，请稍后重试。"}</p></div> : <>
            {scopedAlerts.length > 1 && <div className="ain-forecast-risk-tabs" aria-label="选择关注风险">{scopedAlerts.map(a => <button type="button" key={a.id} aria-label={`查看${a.target}的预测风险`} aria-pressed={a.id === alert?.id} onClick={() => setSelected(a.id)}>{a.target} · {a.title}</button>)}</div>}
            <section className={`ain-forecast-summary${alert ? "" : " is-normal"}`} aria-label="预警摘要">
              <div><h3>{alert ? (alert.title.includes(alert.target) ? alert.title : `${alert.target} · ${alert.title}`) : request.targetWindow ? '关注时段未触发风险提示' : result.integrationNotice ? "当前未触发风险提示" : "预测范围内未发现明显风险"}</h3>{alert && <span data-level={alert.severity}>{level[alert.severity]}风险</span>}</div>
              <p>{alert?.summary || (request.targetWindow ? '没有与关注时段相交的预警；这不表示未来一定没有故障。' : result.message) || "可继续查看频谱态势和指标变化，定期刷新预测。"}</p>
              {alert && <small>预计时段：{alert.start} — {alert.end} · {leadText} · 详细原因与建议可交给AI研判</small>}
              {!alert && <small>{result.integrationNotice ? "仅为频谱阈值提示，不能排除其他网络异常。" : "未触发提示不代表未来一定没有故障。"}</small>}
            </section>
            <div className="ain-forecast-chart-stack">
              {/* 区域电磁态势序列提到弹窗层级、放在频谱三视图之前：
                  它跳出"频谱曲线/功率时频/占用态势"的 tab 体系，所以点开预测预警
                  第一眼就能看到（含大图/时间轴/播放/预测时长）。
                  原来它挂在 SpectrumChart 内部，只有在切到"功率时频/占用态势"
                  tab 并向下滚动后才可见，首屏看不到。 */}
              <SpatialFrameGrid scenario={result.scenario} baseTime={result.baseTime}
                horizonMinutes={request.horizonMinutes} archiveParentId={forecastRecordIds.get(result)} />
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
      <footer className="ain-forecast-dialog-footer"><span>{demo ? "示例研判不会调用大模型" : noRiskToReview && result?.status === "ready" ? "预测窗口内无越阈风险，仍可交给 AI 说明趋势与不确定性" : "详细原因、证据与预防方案在AI区查看"}</span><div><button type="button" className="ain-forecast-primary" disabled={!canAnalyze || pending || Boolean(error) || result?.status !== "ready"} onClick={() => { if (result) onAnalyze({ result, alertId: alert?.id ?? null, question: request.question, targetWindow: request.targetWindow }); }}>交给AI详细研判</button><button type="button" onClick={onClose}>关闭</button></div></footer>
    </section>
  </div>;
  return typeof document === "undefined" ? content : createPortal(content, document.body);
}
