import { useEffect, useRef, useState } from "react";
import { fetchForecastPlans, simulatePlan } from "../services/backend";
import { record } from "../history/store";
import type { SimulationResult, TreatmentPlan } from "../types/plan";
import { METRIC_META } from "../types/backend";
import type { ForecastHandoff } from "./types";

/**
 * 预测风险 → 处置方案（审计第 6 条 / 10-29 审计第 1、2、6 项）。
 *
 * 三条硬约束（对应审计验收标准）：
 *  1. **版本隔离**：结果必须绑定"发起时的方案 + 参数"；期间改参数或换方案，
 *     旧响应直接丢弃（不是"清空界面再被旧响应覆盖"）。
 *  2. **必须归档**：生成方案、每次仿真都写入分析记录，挂在预测研判记录下，
 *     刷新/导出后仍能对上"问题—方案—参数—效果"。
 *  3. **不许含糊**：工参基线标"默认模板（非实测）"，指标用 METRIC_META 的中文与单位，
 *     枚举参数用下拉框，方法/假设/限制原样展示。
 */
const sameValues = (a: Record<string, string>, b: Record<string, string>) =>
  Object.keys({ ...a, ...b }).every(k => String(a[k] ?? "") === String(b[k] ?? ""));

type SimState = {
  pending: boolean;
  // 结果连同"发起时"的输入一起存，用于判断是否仍对应当前方案/参数
  result: SimulationResult | null;
  forPlanId: string;
  inputValues: Record<string, string>;
  error: string;
};

const EMPTY_SIM: SimState = { pending: false, result: null, forPlanId: "", inputValues: {}, error: "" };

export default function ForecastPlans({ handoff, parentId }: { handoff: ForecastHandoff; parentId?: string }) {
  const { result, alertId, targetWindow } = handoff;
  const [state, setState] = useState<{
    pending: boolean;
    payload: Awaited<ReturnType<typeof fetchForecastPlans>> | null;
    error: string;
  }>({ pending: false, payload: null, error: "" });
  const [selectedId, setSelectedId] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [sim, setSim] = useState<SimState>(EMPTY_SIM);
  // 每次发起评估或修改输入都自增；响应回来时代号不一致即丢弃
  const simSeq = useRef(0);

  // 改动输入即作废在途请求与已有结果（审计第 1 项的核心）
  const invalidate = () => {
    simSeq.current += 1;
    setSim(EMPTY_SIM);
  };

  useEffect(() => () => { simSeq.current += 1; }, []);

  /** 归档：生成方案 / 每次仿真都写记录，挂在预测研判记录下（审计第 2 项）。
   *  必须在 load 之前定义：load 会自动调用它（自动接续后也要归档）。 */
  const archivePlan = (plan: TreatmentPlan, payload: NonNullable<typeof state.payload>,
    extra?: { values: Record<string, string>; sim?: SimulationResult; action: string; error?: string }) => {
    if (!parentId) return;                       // 没有父记录就不写，避免出现孤立条目
    const scope = payload.basisWindow ?? { start: result.history.start, end: result.history.end };
    record({
      kind: "parameters", parentId,
      question: `预测方案 · ${payload.risk?.ap ?? ""} ${payload.risk?.type ?? ""}`.trim(),
      scope: { scenario: result.scenario, start: scope.start, end: scope.end },
      analysisTime: result.baseTime, status: "completed",
      payload: { plan, action: extra?.action ?? "生成预测处置方案", values: extra?.values, forecast: payload.basis ?? payload.risk },
    });
    if (extra?.sim) {
      record({
        kind: "simulation", parentId,
        question: `预测方案效果 · ${plan.name}`,
        scope: { scenario: result.scenario, start: scope.start, end: scope.end },
        analysisTime: result.baseTime, status: extra.error ? "failed" : "completed",
        payload: { plan, values: extra.values, result: extra.sim, error: extra.error, inputAt: new Date().toISOString() },
      });
    }
  };

  const loadSeq = useRef(0);
  const load = async () => {
    // 请求序号（独立审计 P2）：换风险时旧请求后返回会覆盖新方案，
    // 还会按**当前** parentId 把旧方案归档到新风险下。
    const seq = ++loadSeq.current;
    setState({ pending: true, payload: null, error: "" });
    invalidate();
    try {
      const payload = await fetchForecastPlans({
        forecastId: result.id, scenario: result.scenario, baseTime: result.baseTime,
        history: result.history, forecast: result.forecast,
        alertId: alertId ?? undefined, targetWindow: targetWindow ?? undefined,
      });
      if (seq !== loadSeq.current) return;      // 已被更新的请求取代 → 丢弃
      setState({ pending: false, payload, error: "" });
      const first = payload.plans[0];
      if (first) {
        setSelectedId(first.id);
        setValues(Object.fromEntries(first.parameters.map(p => [p.id, String(p.suggestedValue ?? "")])));
        // 没有 parentId 时先记下来，等它到了再补归档
        if (parentId) archivePlan(first, payload);
        else pendingArchive.current = { plan: first, payload };
      }
    } catch (reason) {
      if (seq !== loadSeq.current) return;
      setState({ pending: false, payload: null, error: reason instanceof Error ? reason.message : "方案生成失败" });
    }
  };

  // parentId 晚到要补归档（独立审计 P3）：自动加载在挂载时发生，
  // 那时研判记录 id 可能还没回来 → 归档被跳过，且此后不会补。
  const archivedFor = useRef("");
  const pendingArchive = useRef<{ plan: TreatmentPlan; payload: NonNullable<typeof state.payload> } | null>(null);
  useEffect(() => {
    if (!parentId || !pendingArchive.current) return;
    const key = `${parentId}|${pendingArchive.current.plan.id}`;
    if (archivedFor.current === key) return;
    archivedFor.current = key;
    const { plan, payload } = pendingArchive.current;
    pendingArchive.current = null;
    archivePlan(plan, payload);
    // archivePlan 依赖 parentId/result，均由本次移交固定
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [parentId]);

  // 自动接续（10-30 清单第 4 点）：研判完成后直接加载方案，
  // 不再要求用户再点一次"生成处置方案"才知道有没有方案。
  // 守卫必须覆盖**全部**影响请求的输入：同一轮预测（id 由上下文确定性生成）
  // 换一条风险再进研判时，alertId/targetWindow 变了也必须重新加载
  // （独立审计 P2：只认 result.id 会把上一次风险的方案留在页面上）。
  const autoKey = `${result.id}|${alertId ?? ""}|${targetWindow?.start ?? ""}|${targetWindow?.end ?? ""}`;
  const autoLoaded = useRef("");
  useEffect(() => {
    if (autoLoaded.current === autoKey) return;
    autoLoaded.current = autoKey;
    void load();
    // load 只用本次移交的固定上下文（result/alertId/targetWindow），无需进依赖
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoKey]);

  const choose = (plan: TreatmentPlan) => {
    setSelectedId(plan.id);
    setValues(Object.fromEntries(plan.parameters.map(p => [p.id, String(p.suggestedValue ?? "")])));
    invalidate();
    if (state.payload) archivePlan(plan, state.payload, { values: Object.fromEntries(plan.parameters.map(p => [p.id, String(p.suggestedValue ?? "")])), action: "选择预测方案" });
  };

  const selected: TreatmentPlan | undefined = state.payload?.plans.find(p => p.id === selectedId);
  const runSim = async () => {
    if (!selected || !state.payload?.basisWindow) return;
    const seq = ++simSeq.current;
    // 闭包里的 values/selectedId 就是"点击这一下时的输入"，正是要绑定的版本
    const inputValues = { ...values };
    const planId = selected.id;
    setSim({ pending: true, result: null, forPlanId: planId, inputValues, error: "" });
    try {
      const scope = { scenario: result.scenario, start: state.payload.basisWindow.start, end: state.payload.basisWindow.end };
      const r = await simulatePlan(scope, selected, inputValues, selected.objectIds[0] ?? "");
      if (seq !== simSeq.current) return;       // 期间改过参数/换过方案 → 丢弃旧响应
      setSim({ pending: false, result: r, forPlanId: planId, inputValues, error: "" });
      if (r) archivePlan(selected, state.payload, { values: inputValues, sim: r, action: "预测方案仿真" });
    } catch (reason) {
      if (seq !== simSeq.current) return;
      const text = reason instanceof Error ? reason.message : "仿真失败";
      setSim({ pending: false, result: null, forPlanId: planId, inputValues, error: text });
      archivePlan(selected, state.payload, { values: inputValues, action: "预测方案仿真", error: text });
    }
  };

  // 只有"结果对应的方案与参数仍是当前值"才展示（旧结果明确标注作废）
  const resultIsCurrent = Boolean(sim.result) && sim.forPlanId === selectedId && sameValues(sim.inputValues, values);
  // 后端为**建议参数**已经算过一份默认效果：参数没改时直接显示它，
  // 不必等用户点"评估"（清单第 4 点：已有的默认结果不该被藏起来）。
  const suggestedValues = selected
    ? Object.fromEntries(selected.parameters.map(p => [p.id, String(p.suggestedValue ?? "")])) : {};
  const defaultEffect = selected?.simulation && sameValues(suggestedValues, values) ? selected.simulation : null;
  const shown = resultIsCurrent ? sim.result : defaultEffect;
  const shownIsDefault = !resultIsCurrent && Boolean(defaultEffect);
  const baseline = state.payload?.basisWindow;

  return <section className="ain-forecast-plans" aria-label="预测风险处置方案">
    <header><strong>预测风险的处置方案</strong><span>{result.alerts.find(a => a.id === alertId)?.target ?? "整体预测"}</span></header>
    <p className="ain-ai-note">
      这些方案**只来自本轮预测的风险**，与「处理建议与效果」里的现存问题方案相互独立。
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
                onChange={() => choose(p)} />
              <strong>{p.name}</strong>{p.description ? ` · ${p.description}` : ""}
            </label>
          </li>)}
        </ul>
        {selected && <div className="ain-forecast-plans-params">
          <p>
            参数基线：<strong>默认工参模板</strong>（倾角/功率为模板默认值，<strong>不是</strong> CSV 实测配置）；
            KPI 与效果评估取自 {baseline?.start} ~ {baseline?.end} 的历史实测窗口。
          </p>
          {selected.parameters.map(p => <label key={p.id}>
            <span>{p.label}{p.unit ? `（${p.unit}）` : ""}</span>
            {p.type === "enum" && p.options?.length
              ? <select value={values[p.id] ?? ""} onChange={e => { setValues(v => ({ ...v, [p.id]: e.target.value })); invalidate(); }}>
                {p.options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
              : <input value={values[p.id] ?? ""} onChange={e => { setValues(v => ({ ...v, [p.id]: e.target.value })); invalidate(); }} />}
            <small>当前 {String(p.currentValue ?? "-")}{p.unit ?? ""} · 建议 {String(p.suggestedValue ?? "-")}{p.unit ?? ""}</small>
          </label>)}
          <button type="button" className="ain-button" disabled={sim.pending} onClick={() => void runSim()}>
            {sim.pending ? "正在评估…" : "在预测基准前的实测窗口评估"}
          </button>
          <p className="ain-ai-note">评估用的是基准前的实测数据，**不是**预测时段的实测结果；改参数或换方案会作废上一次结果。</p>
        </div>}
        {sim.error && <p role="alert">{sim.error}</p>}
        {sim.result && !resultIsCurrent && <p className="ain-ai-note" role="status">
          参数或方案已变更，上一次评估结果（{sim.inputValues && Object.keys(sim.inputValues).length ? JSON.stringify(sim.inputValues) : "旧参数"}）已作废，请重新评估。
        </p>}
        {shown && <div className="ain-forecast-plans-sim">
          <p>
            <strong>{shown.summary || "评估完成"}</strong>
            <span className="ain-forecast-plans-tag">
              {shownIsDefault ? "默认建议参数（后端已计算）" : "本次参数评估结果"}
            </span>
          </p>
          <ul>{shown.metrics.map(m => {
            const meta = METRIC_META[m.key] ?? { label: String(m.key), unit: "" };
            const delta = m.before !== null && m.after !== null ? Number((m.after - m.before).toFixed(2)) : null;
            return <li key={`${m.key}-${m.objectId}`}>
              {meta.label}（{m.objectId}）：{m.before ?? "—"} → {m.after ?? "—"} {meta.unit}
              {delta !== null ? `（${delta > 0 ? "+" : ""}${delta}${meta.unit}）` : ""}
            </li>;
          })}</ul>
          {shown.method && <p className="ain-ai-note">方法：{shown.method}</p>}
          {shown.assumptions?.length ? <p className="ain-ai-note">假设：{shown.assumptions.join("；")}</p> : null}
          {shown.limitations?.length ? <p className="ain-ai-note">限制：{shown.limitations.join("；")}</p> : null}
        </div>}
      </>}
    </>}
  </section>;
}
