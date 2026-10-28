import { useState } from "react";
import { fetchForecastPlans, simulatePlan } from "../services/backend";
import type { SimulationResult, TreatmentPlan } from "../types/plan";
import type { ForecastHandoff } from "./types";

// 指标中文名/单位（与 diagnosisPresentation 保持一致的四个口径）
const METRIC_TEXT: Record<string, { label: string; unit: string }> = {
  rsrp: { label: "RSRP", unit: "dBm" },
  sinr: { label: "SINR", unit: "dB" },
  throughput: { label: "吞吐量", unit: "Mbps" },
  prb_usage: { label: "PRB 利用率", unit: "%" },
};

/**
 * 预测风险 → 处置方案（审计第 6 条）。
 *
 * 为什么单独做一块 UI 而不是塞进「处理建议与效果」：
 * 那块区域对应**现存网络异常**的研判，如果把预测方案放进去，用户会以为
 * 下面这套参数/仿真就是"当前故障的方案"，这正是审计指出的误导来源。
 *
 * 因此这里显式做三件事：
 *   1. 标题写清"来自预测风险"，并重复风险时段；
 *   2. 方案依据（basisWindow）与免责说明**必须显示**，不能折叠隐藏；
 *   3. 仿真按钮文案写明"在预测基准前的实测窗口评估"，不写"预测效果"。
 */
export default function ForecastPlans({ handoff }: { handoff: ForecastHandoff }) {
  const { result, alertId, targetWindow } = handoff;
  const [state, setState] = useState<{
    pending: boolean;
    payload: Awaited<ReturnType<typeof fetchForecastPlans>> | null;
    error: string;
  }>({ pending: false, payload: null, error: "" });
  const [selectedId, setSelectedId] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [sim, setSim] = useState<{ pending: boolean; result: SimulationResult | null; error: string }>(
    { pending: false, result: null, error: "" });

  const load = async () => {
    setState({ pending: true, payload: null, error: "" });
    setSim({ pending: false, result: null, error: "" });
    try {
      const payload = await fetchForecastPlans({
        forecastId: result.id, scenario: result.scenario, baseTime: result.baseTime,
        history: result.history, forecast: result.forecast,
        alertId: alertId ?? undefined, targetWindow: targetWindow ?? undefined,
      });
      setState({ pending: false, payload, error: "" });
      const first = payload.plans[0];
      if (first) {
        setSelectedId(first.id);
        setValues(Object.fromEntries(first.parameters.map(p => [p.id, String(p.suggestedValue ?? "")])));
      }
    } catch (reason) {
      setState({ pending: false, payload: null, error: reason instanceof Error ? reason.message : "方案生成失败" });
    }
  };

  const selected: TreatmentPlan | undefined = state.payload?.plans.find(p => p.id === selectedId);
  const runSim = async () => {
    if (!selected || !state.payload?.basisWindow) return;
    setSim({ pending: true, result: null, error: "" });
    try {
      // 关键：窗口用 basisWindow（预测基准前的实测窗口），不是预测目标时段
      const scope = { scenario: result.scenario, start: state.payload.basisWindow.start, end: state.payload.basisWindow.end };
      const r = await simulatePlan(scope, selected, values, selected.objectIds[0] ?? "");
      setSim({ pending: false, result: r, error: "" });
    } catch (reason) {
      setSim({ pending: false, result: null, error: reason instanceof Error ? reason.message : "仿真失败" });
    }
  };

  return <section className="ain-forecast-plans" aria-label="预测风险处置方案">
    <header><strong>预测风险的处置方案</strong><span>{result.alerts.find(a => a.id === alertId)?.target ?? "整体预测"}</span></header>
    <p className="ain-ai-note">
      这里的方案**只来自本轮预测的风险**，与「处理建议与效果」里的现存问题方案相互独立。
    </p>
    {!state.payload && !state.pending && !state.error &&
      <button type="button" className="ain-button" onClick={() => void load()}>生成处置方案</button>}
    {state.pending && <p role="status">正在按预测风险生成方案…</p>}
    {state.error && <div role="alert"><p>{state.error}</p>
      <button type="button" onClick={() => void load()}>重试生成方案</button></div>}
    {state.payload && <>
      {state.payload.message && <p className="ain-ai-note">{state.payload.message}</p>}
      {state.payload.disclaimer && <p className="ain-forecast-plans-basis">{state.payload.disclaimer}</p>}
      {state.payload.plans.length > 0 && <>
        <ul className="ain-forecast-plans-list">
          {state.payload.plans.map(p => <li key={p.id}>
            <label>
              <input type="radio" name={`forecast-plan-${result.id}`} checked={p.id === selectedId}
                onChange={() => { setSelectedId(p.id); setSim({ pending: false, result: null, error: "" });
                  setValues(Object.fromEntries(p.parameters.map(x => [x.id, String(x.suggestedValue ?? "")]))); }} />
              <strong>{p.name}</strong>{p.description ? ` · ${p.description}` : ""}
            </label>
          </li>)}
        </ul>
        {selected && <div className="ain-forecast-plans-params">
          <p>参数（基线来自 {state.payload.basisWindow?.start} ~ {state.payload.basisWindow?.end} 的实测）</p>
          {selected.parameters.map(p => <label key={p.id}>
            <span>{p.label}{p.unit ? `（${p.unit}）` : ""}</span>
            <input value={values[p.id] ?? ""} onChange={e => { setValues(v => ({ ...v, [p.id]: e.target.value })); setSim({ pending: false, result: null, error: "" }); }} />
            <small>当前 {String(p.currentValue ?? "-")} · 建议 {String(p.suggestedValue ?? "-")}</small>
          </label>)}
          <button type="button" className="ain-button" disabled={sim.pending} onClick={() => void runSim()}>
            {sim.pending ? "正在评估…" : "在预测基准前的实测窗口评估"}
          </button>
          <p className="ain-ai-note">评估用的是基准前的实测数据，**不是**预测时段的实测结果。</p>
        </div>}
        {sim.error && <p role="alert">{sim.error}</p>}
        {sim.result && <div className="ain-forecast-plans-sim">
          <p><strong>{sim.result.summary || "评估完成"}</strong></p>
          <ul>{sim.result.metrics.map(m => {
            const text = METRIC_TEXT[m.key] ?? { label: m.key, unit: "" };
            const delta = m.before !== null && m.after !== null ? Number((m.after - m.before).toFixed(2)) : null;
            return <li key={`${m.key}-${m.objectId}`}>
              {text.label}：{m.before ?? "—"} → {m.after ?? "—"} {text.unit}
              {delta !== null ? `（${delta > 0 ? "+" : ""}${delta}${text.unit}）` : ""}
            </li>;
          })}</ul>
        </div>}
      </>}
    </>}
  </section>;
}
