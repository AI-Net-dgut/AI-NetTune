import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { fetchForecastPlans, simulatePlan } from "../services/backend";
import { record } from "../history/store";
import type { SimulationResult, TreatmentPlan } from "../types/plan";
import { ReplyText } from "../chat/DiagnosisAnswer";
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
    if (extra?.sim || extra?.error) {
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
  // parentId 用 ref 跟踪：它可能在请求**飞行途中**才到达，闭包里读到的仍是旧值
  // （独立审计 P2：这时方案既没归档、也不会补，因为 effect 已经跑过了）。
  const parentIdRef = useRef(parentId);
  const load = async () => {
    // 请求序号（独立审计 P2）：换风险时旧请求后返回会覆盖新方案，
    // 还会按**当前** parentId 把旧方案归档到新风险下。
    const seq = ++loadSeq.current;
    pendingArchive.current = null;               // 换轮次/换风险：丢弃旧的待归档项
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
        // 用 ref 读 parentId：请求飞行途中到达也能归档；
        // 仍为空才暂存，等 effect 补。
        const pid = parentIdRef.current;
        if (pid) archivePlan(first, payload);
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
    parentIdRef.current = parentId;              // 供在途请求读取最新值
    if (!parentId || !pendingArchive.current) return;
    // key 带上轮次与风险：同一挂载内换了 handoff 时，旧的待归档项不能被算作已归档
    const key = `${parentId}|${result.id}|${alertId ?? ""}|${pendingArchive.current.plan.id}`;
    if (archivedFor.current === key) return;
    archivedFor.current = key;
    const { plan, payload } = pendingArchive.current;
    pendingArchive.current = null;
    archivePlan(plan, payload);
    // archivePlan 依赖 parentId/result，均由本次移交固定
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [parentId, result.id, alertId]);

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
    <header><strong>处理方案与评估</strong><span>{result.alerts.find(a => a.id === alertId)?.target ?? "整体预测"} · 预测风险</span></header>
    {!state.payload && !state.pending && !state.error &&
      <button type="button" className="ain-button" onClick={() => void load()}>加载建议方案</button>}
    {state.pending && <p role="status">正在加载本次风险的建议方案…</p>}
    {state.error && <div role="alert"><p>{state.error}</p>
      <button type="button" onClick={() => void load()}>重试加载方案</button></div>}
    {state.payload && <>
      {state.payload.message && <p className="ain-forecast-brief-note" role="status">{state.payload.message}</p>}
      {state.payload.plans.length > 0 && <>
        <h4>建议方案 <small>系统模板 · 可调整参数</small></h4>
        <ul className="ain-forecast-plans-list">
          {state.payload.plans.map(p => <li key={p.id}>
            <label className={p.id === selectedId ? "is-selected" : ""}>
              <input type="radio" name={`forecast-plan-${result.id}`} checked={p.id === selectedId}
                onChange={() => choose(p)} />
              <span><strong>{p.name}</strong>{p.description && <small>{p.description}</small>}</span>
            </label>
          </li>)}
        </ul>
        {selected && <div className="ain-forecast-plans-params">
          <h4>配置参数 <small>默认工参模板，非实测配置</small></h4>
          <div className="ain-forecast-param-grid">
            {selected.parameters.map(p => <label key={p.id}>
              <span>{p.label}{p.unit ? `（${p.unit}）` : ""}</span>
              {p.type === "enum" && p.options?.length
                ? <select value={values[p.id] ?? ""} onChange={e => { setValues(v => ({ ...v, [p.id]: e.target.value })); invalidate(); }}>
                  {p.options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
                : <input value={values[p.id] ?? ""} onChange={e => { setValues(v => ({ ...v, [p.id]: e.target.value })); invalidate(); }} />}
              <small>模板 {String(p.currentValue ?? "—")} · 建议 {String(p.suggestedValue ?? "—")}</small>
            </label>)}
          </div>
          <div className="ain-forecast-evaluate-row">
            <button type="button" className="ain-button" disabled={sim.pending || !baseline} onClick={() => void runSim()}>
              {sim.pending ? "正在评估…" : "评估当前参数"}
            </button>
            <span>基于历史窗口估算，不执行网络配置。</span>
          </div>
          {!baseline && <p role="alert">缺少评估基准窗口，暂不能重新评估。</p>}
        </div>}
        {sim.error && <p role="alert">评估失败：{sim.error}</p>}
        {sim.result && !resultIsCurrent && <p className="ain-forecast-brief-note" role="status">
          参数已变更，请重新评估；原结果保留在分析记录中。
        </p>}
        {shown && <div className="ain-forecast-plans-sim">
          <h4>预计效果 <small>{shownIsDefault ? "建议参数" : "本次参数"} · 历史窗口评估</small></h4>
          {shown.status !== "completed" && <p role="status">{shown.status === "failed" ? "本次评估未成功" : "本方案暂不支持效果评估"}：{shown.summary}</p>}
          {shown.metrics.length > 0 ? <div className="ain-forecast-metrics-scroll"><table className="ain-forecast-metrics">
            <thead><tr><th scope="col">指标 / 对象</th><th scope="col">原配置</th><th scope="col">方案预计</th><th scope="col">变化</th></tr></thead>
            <tbody>{shown.metrics.map(m => {
              const meta = METRIC_META[m.key] ?? { label: String(m.key), unit: "" };
              const delta = m.before !== null && m.after !== null ? Number((m.after - m.before).toFixed(2)) : null;
              const deltaUnit = meta.unit === "dBm" ? "dB" : meta.unit;
              return <tr key={`${m.key}-${m.objectId}`}>
                <th scope="row">{meta.label}<small>{m.objectId}</small></th>
                <td>{m.before ?? "—"}{m.before !== null && <small>{meta.unit}</small>}</td>
                <td>{m.after ?? "—"}{m.after !== null && <small>{meta.unit}</small>}</td>
                <td>{delta === null ? "—" : `${delta > 0 ? "+" : ""}${delta}`}<small>{delta !== null ? deltaUnit : ""}</small></td>
              </tr>;
            })}</tbody>
          </table></div> : <p className="ain-forecast-brief-note">本次未返回可对比的指标。</p>}
        </div>}
        {selected && <details className="ain-forecast-details">
          <summary>评估依据与适用条件</summary>
          <p>实测参考窗口：{baseline ? `${baseline.start} — ${baseline.end}（北京时间）` : "未提供"}。此处为历史窗口的方案效果估算，不代表未来实测结果。</p>
          {state.payload.disclaimer && <ReplyText text={state.payload.disclaimer} />}
          {shown?.summary && <p>{shown.summary}</p>}
          {shown?.method && <p>方法：{shown.method}</p>}
          {shown?.assumptions?.length ? <p>假设：{shown.assumptions.join("；")}</p> : null}
          {shown?.limitations?.length ? <p>限制：{shown.limitations.join("；")}</p> : null}
          {selected.tasks.length > 0 && <p>人工检查：{selected.tasks.join("；")}</p>}
          {selected.conditions.length > 0 && <p>适用条件：{selected.conditions.join("；")}</p>}
          {selected.tradeoffs.length > 0 && <p>权衡：{selected.tradeoffs.join("；")}</p>}
          <Link to="/#simulation-guide">查看方案与评估说明</Link>
        </details>}
      </>}
      {!state.payload.plans.length && state.payload.disclaimer && <details className="ain-forecast-details">
        <summary>方案说明</summary><ReplyText text={state.payload.disclaimer} />
      </details>}
    </>}
  </section>;
}
