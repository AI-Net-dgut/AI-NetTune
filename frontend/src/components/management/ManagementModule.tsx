import { record } from "../../history/store";
import { useCallback, useImperativeHandle, useMemo, useRef, useState } from 'react';
import type { Ref } from 'react';
import type { DashboardData, Diagnosis } from '../../types/backend';
import type { SimulationResult, TreatmentPlan } from '../../types/plan';
import type { DiagnosisPreparation, ManagementHandle } from './preparePlan';
import { fallbackPlan, initialParameterValues, isSimulationCurrent, validateParameter } from './planWorkspace';
import { sameQueryScope } from '../../chat/diagnosisModel';
import { buildBaseline } from './managementDraft';
import { METRIC_META } from '../../types/backend';
import { displayMetric } from '../../services/metrics';
import { fetchSimConfig, simulatePlan } from '../../services/backend';
import { capturePlanExport, downloadPlanFile, planExportFilename, renderPlanReport } from './planReport';
import type { ConfigurationSnapshot } from './planReport';
import SimConfigBar from './SimConfigBar';
import PlanEffects from './PlanEffects';
import './ManagementModule.css';

export default function ManagementModule({ data, diagnosis, chatDiagnosis, focusedApId, disabled = false, plans: providedPlans = [], ref, sourceQuestion = '', pending = false, archiveParentId }: {
  ref?: Ref<ManagementHandle>; data: DashboardData; diagnosis: Diagnosis | null; chatDiagnosis: Diagnosis | null;
  focusedApId: string | null; disabled?: boolean; plans?: TreatmentPlan[];
  sourceQuestion?: string; pending?: boolean; archiveParentId?: string;
}) {
  const panelRef = useRef<HTMLElement>(null);
  const active = chatDiagnosis || diagnosis;
  // 有 AI 回答（且已通过证据校验）时，方案描述与效果总结这类**模板文字**要收起：
  // AI 回答里已经写了【方案】【预计效果】，再显示一遍就是重复（用户反馈"太重复"）。
  // 模板原文仍保留在折叠区，AI 未通过校验时不做任何隐藏。
  const aiAnswerActive = Boolean((chatDiagnosis ?? diagnosis)?.answerCheck?.ok);
  const mismatch = Boolean(active && !sameQueryScope(active.scope, data.scope));
  const plans = useMemo(() => {
    if (active?.answerFocus?.planEligible === false) return [];
    const valid = providedPlans.filter(p => sameQueryScope(p.scope, data.scope));
    if (valid.length) return valid;
    const fallback = active ? fallbackPlan(active) : null; return fallback ? [fallback] : [];
  }, [providedPlans, data.scope, active]);
  const [selectedId, setSelectedId] = useState(plans[0]?.id ?? '');
  const selected = plans.find(p => p.id === selectedId) ?? plans[0] ?? null;
  const [values, setValues] = useState<Record<string, string>>(() => selected ? initialParameterValues(selected) : {});
  const [editorOpen, setEditorOpen] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  // "改参数后重算"的结果：按方案 id 存放，避免不同方案的仿真串台
  const [rerun, setRerun] = useState<{ planId: string; result: SimulationResult } | null>(null);
  const [simPending, setSimPending] = useState(false);
  const [exportPending, setExportPending] = useState(false);
  const exportingRef = useRef(false);
  const errors = selected ? selected.parameters.map(p => validateParameter(p, values[p.id] ?? '')) : [];
  const baselineAp = selected?.objectIds.length === 1 ? selected.objectIds[0] : focusedApId;
  const baseline = !mismatch && baselineAp ? buildBaseline(data, baselineAp) : null;
  // 优先使用"按当前参数重算"的结果；没有则用方案自带的建议值仿真
  const effectivePlan: TreatmentPlan | null = selected
    ? { ...selected, simulation: rerun?.planId === selected.id ? rerun.result : selected.simulation }
    : null;
  const currentEffect = effectivePlan && !mismatch && isSimulationCurrent(effectivePlan, values, data.scope)
    ? effectivePlan.simulation : null;
  const editable = selected?.parameters.some(p => p.editable) ?? false;

  function archivePlan(kind: 'parameters' | 'simulation', plan: TreatmentPlan, result?: SimulationResult, error?: string,
    captured?: { values: Record<string, string>; scope: DashboardData['scope']; parentId?: string; question: string; inputAt: string }) {
    const scope = captured?.scope ?? data.scope;
    record({kind,parentId:captured ? captured.parentId : archiveParentId,question:`${plan.name} · ${captured?.question || sourceQuestion || '方案配置'}`,
      scope,analysisTime:scope.end,status:error?'failed':'completed',
      payload:{plan,values:captured?.values ?? {...values},result,error,inputAt:captured?.inputAt,completedAt:kind === 'simulation' ? new Date().toISOString() : undefined}});
  }
  function choose(plan: TreatmentPlan) {
    if (disabled || mismatch) return;
    record({kind:'parameters',parentId:archiveParentId,question:`${plan.name} · ${sourceQuestion||'方案配置'}`,scope:data.scope,analysisTime:data.scope.end,status:'completed',payload:{plan,values:initialParameterValues(plan),action:'选择方案'}});
    setSelectedId(plan.id); setValues(initialParameterValues(plan)); setEditorOpen(false); setNotice(''); setError(''); setRerun(null);
  }
  async function runSimulation() {
    if (!selected || disabled || mismatch || simPending) return;
    const first = errors.find(Boolean);
    if (first) { setError(first); setEditorOpen(true); return; }   // 参数非法时不要拿脏值去算
    const captured = { values: { ...values }, scope: { ...data.scope }, parentId: archiveParentId, question: sourceQuestion, inputAt: new Date().toISOString() };
    const capturedPlan = selected;
    setSimPending(true); setError('');
    try {
      const result = await simulatePlan(captured.scope, capturedPlan, captured.values, capturedPlan.objectIds[0] ?? baselineAp ?? '');
      if (!result) throw new Error('后端未返回有效的仿真结果');
      setRerun({ planId: selected.id, result });
      archivePlan("simulation",capturedPlan,result,undefined,captured);
      setNotice('本次请求参数的仿真已保存；若已修改参数，请按新参数重新评估。');
    } catch (e) {
      archivePlan('simulation',capturedPlan,undefined,e instanceof Error?e.message:'仿真失败',captured);
      setError(e instanceof Error ? e.message : '方案仿真失败，请稍后重试');
    } finally {
      setSimPending(false);
    }
  }
  const prepare = useCallback((input: DiagnosisPreparation) => {
    if (disabled || !sameQueryScope(input.result.scope, data.scope)) return;
    const next = plans[0]; if (!next) return;
    setSelectedId(next.id); setValues(initialParameterValues(next)); setNotice('已定位此研判的处理方案。'); setError('');
    panelRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'nearest' });
    panelRef.current?.querySelector<HTMLElement>('h3')?.focus({ preventScroll: true });
  }, [disabled, data.scope, plans]);
  useImperativeHandle(ref, () => ({ prepare }), [prepare]);
  function saveParameters() {
    if (disabled || mismatch) return;
    const first = errors.find(Boolean); if (first) { setError(first); return; }
    if(selected)archivePlan('parameters',selected);
    setEditorOpen(false); setError(''); setNotice('参数已保留在当前方案中；变更后的预计效果需重新评估。');
  }
  async function exportPlan(format: 'html' | 'json' = 'html') {
    if (!effectivePlan || disabled || mismatch || exportingRef.current) return;
    const first = errors.find(Boolean); if (first) { setError(first); setEditorOpen(true); return; }
    exportingRef.current = true;
    setExportPending(true); setError(''); setNotice('正在整理方案报告…');
    try {
      // 在任何异步操作之前冻结，期间切换方案/修改参数也不会串入报告。
      const snapshot = capturePlanExport({ scope: data.scope, question: sourceQuestion, plan: effectivePlan,
        parameterValues: values, diagnosis: active, historicalBaseline: baseline, exportedAt: new Date().toISOString() });
      let configuration: ConfigurationSnapshot = { status: null, checkedAt: new Date().toISOString() };
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), 3000);
      try {
        configuration = { status: await fetchSimConfig(controller.signal), checkedAt: new Date().toISOString() };
      } catch { /* 只读状态查询失败不阻止导出；报告明确写“未核实”。 */ }
      finally { window.clearTimeout(timeout); }
      const content = format === 'html' ? renderPlanReport(snapshot, configuration)
        : JSON.stringify({ ...snapshot, configuration }, null, 2);
      downloadPlanFile(content, planExportFilename(snapshot, format), format === 'html' ? 'text/html' : 'application/json');
      setNotice(format === 'html' ? '方案报告下载已发起。用浏览器打开可阅读或打印为 PDF。' : 'JSON 数据下载已发起。');
    } catch { setNotice(''); setError('方案导出失败，请检查浏览器下载设置后重试。'); }
    finally { exportingRef.current = false; setExportPending(false); }
  }

  if (active?.answerFocus?.planEligible === false) return <section ref={panelRef} className="ain-management-integrated" id="ain-management-panel" aria-labelledby="ain-management-title">
    <header className="ain-ai-column-heading"><h3 id="ain-management-title" tabIndex={-1}>处理方案与仿真</h3><span>暂无对应方案</span></header>
    {sourceQuestion && <p className="ain-management-muted">对应研判：{sourceQuestion}</p>}
    <p>{active.answerFocus.sections.find(s => s.title === '处理与预估')?.text || '本次尚无对应问题的配置与仿真方案，请先核查专项证据。'}</p>
  </section>;

  return <section ref={panelRef} className="ain-management-integrated" id="ain-management-panel" aria-labelledby="ain-management-title">
    <header className="ain-ai-column-heading"><h3 id="ain-management-title" tabIndex={-1}>处理方案与仿真</h3><span>方案预估</span></header>
    {sourceQuestion && <p className="ain-management-muted">对应研判：{sourceQuestion}</p>}
    {pending && <p className="ain-management-muted" role="status">{active ? '辅助图查询中，以下方案来自关联研判，暂不可操作。' : '等待本次研判返回处理建议。'}</p>}
    {/* 仿真内闭环（申报书创新点④）：把选中方案的参数应用到仿真网络 / 回滚。
        纯新增组件，不接管下面既有的选择与仿真按钮。 */}
    <SimConfigBar
      scenario={data.scope?.scenario ?? ""}
      apIds={(() => {
        const ids = (selected as unknown as { objectIds?: unknown })?.objectIds;
        const list = Array.isArray(ids) ? ids.map(String) : [];
        if (list.length) return list;
        const fromParams = (selected?.parameters ?? []).map(p => String((p as unknown as { objectId?: string }).objectId ?? "")).filter(Boolean);
        return [...new Set(fromParams)];
      })()}
      params={(() => {
        // 支持生效的参数：数值（tilt / tx_power / beam_gain / offload_pct）+ 枚举（capacity）。
        // 枚举必须原样传字符串（"none"/"one_carrier"），统一 Number() 会变成 NaN 被丢弃，
        // 结果就是"载波方案按钮永远不可用"（复查 P1-7）。
        const out: Record<string, number | string> = {};
        for (const p of selected?.parameters ?? []) {
          if (p.id === "capacity") {
            const text = String(values[p.id] ?? p.currentValue ?? "");
            if (text === "none" || text === "one_carrier") out[p.id] = text;
            continue;
          }
          if (!["tilt", "tx_power", "power", "beam_gain", "offload_pct"].includes(p.id)) continue;
          const value = Number(values[p.id]);
          if (Number.isFinite(value)) out[p.id] = value;
        }
        return out;
      })()}
      baseline={(() => {
        const out: Record<string, number | string> = {};
        for (const p of selected?.parameters ?? []) {
          if (p.id === "capacity") {
            const text = String(p.currentValue ?? "none");
            if (text === "none" || text === "one_carrier") out[p.id] = text;
            continue;
          }
          if (!["tilt", "tx_power", "power", "beam_gain", "offload_pct"].includes(p.id)) continue;
          const value = Number(p.currentValue ?? p.suggestedValue);
          if (Number.isFinite(value)) out[p.id] = value;
        }
        return out;
      })()}
      effectiveFrom={String(active?.scope?.end ?? data.scope?.end ?? "")}
      note={`${sourceQuestion || "研判方案"} · ${selected?.name ?? ""}`.trim()}
      planId={selected?.id}
      disabled={disabled || mismatch || pending}
    />
    <section className="ain-plan-suggestions" aria-labelledby="ain-plan-heading">
      <div className="ain-ai-subheading"><h4 id="ain-plan-heading">处理建议</h4><span>{plans.length ? `${plans.length} 个方案` : '等待研判'}</span></div>
      {plans.length ? <div className="ain-plan-list">{plans.map(plan => <button type="button" key={plan.id} className={`ain-suggestion-card${selected?.id === plan.id ? ' is-selected' : ''}`}
        disabled={disabled || mismatch} aria-pressed={selected?.id === plan.id} onClick={() => choose(plan)}>
        <span className="ain-suggestion-card-heading"><strong>{plan.name}</strong><span>{selected?.id === plan.id ? '已选' : '选择方案'}</span></span>
        {/* 有 AI 回答时不再重复模板描述（AI 已写【方案】），保留对象与按钮 */}
        {!aiAnswerActive && <span className="ain-suggestion-text">{plan.description || '后端未提供方案说明'}</span>}
        <span className="ain-plan-object">对象：{plan.objectIds.join('、') || '待明确'}</span></button>)}</div>
        : <p className="ain-management-empty">{
          // 优先用后端给出的"为什么没有方案"（平稳/异常类型无参数方案），而不是笼统的"等待研判"
          (active as { planAbsentReason?: string } | null)?.planAbsentReason
          || (active?.state === 'normal' ? '未发现明显异常，暂不建议调整配置。'
            : active?.state === 'empty' ? '此范围没有可用研判。'
              : '提问后，这里展示对应的处理方案。')}</p>}
      {mismatch && <p className="ain-management-warning">研判范围与当前数据不同，请重新分析；此建议暂不可选择。</p>}
    </section>

    <section className="ain-config-summary" aria-labelledby="ain-config-title">
      <div className="ain-ai-subheading"><h4 id="ain-config-title">配置参数</h4>{editable && <button type="button" className="ain-ai-text-button" disabled={disabled || mismatch}
        aria-expanded={editorOpen} aria-controls="ain-parameter-editor" onClick={() => setEditorOpen(v => !v)}>调整方案参数</button>}</div>
      {selected ? <>
        {selected.parameters.length > 0 ? <>
          <p className="ain-management-muted">可修改建议值，比较不同配置的仿真效果；修改后需重新评估。</p>
          <div className="ain-ai-table-wrap"><table className="ain-parameter-table"><thead><tr><th>对象／参数</th><th>当前值</th><th>方案值</th></tr></thead><tbody>
            {selected.parameters.map(p => <tr key={p.id}><th>{p.objectId}<span>{p.label}</span></th><td>{p.currentValue === null ? '未获取' : `${p.currentValue} ${p.unit}`}</td>
              <td>{values[p.id] ? `${values[p.id]} ${p.unit}` : '待填写'}<small className="ain-parameter-note">{p.description || '后端未提供参数用途'}{!p.editable && ` · ${p.unavailableReason || '此参数只读'}`}</small></td></tr>)}</tbody></table></div>
          <div id="ain-parameter-editor" hidden={!editorOpen}><form className="ain-plan-form" onSubmit={e => { e.preventDefault(); saveParameters(); }}>
            <fieldset disabled={disabled || mismatch}>{selected.parameters.map((p, i) => <label key={p.id} className="ain-parameter-field">
              <span>{p.objectId} · {p.label} {p.unit && ` / ${p.unit}`}</span>
              {p.type === 'enum' ? <select value={values[p.id] ?? ''} disabled={!p.editable} onChange={e => { setValues(v => ({ ...v, [p.id]: e.target.value })); setNotice(''); }}>
                <option value="">请选择</option>{p.options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}</select>
                : <input type="number" value={values[p.id] ?? ''} min={p.min ?? undefined} max={p.max ?? undefined} step={p.step ?? 'any'} disabled={!p.editable}
                  onChange={e => { setValues(v => ({ ...v, [p.id]: e.target.value })); setNotice(''); }} />}
              <small>{p.description || '后端未提供参数用途'}{p.type === 'number' && ` · 范围 ${p.min ?? '不限'}～${p.max ?? '不限'} ${p.unit}`}
                {!p.editable && ` · ${p.unavailableReason || '此参数只读'}`}</small>{errors[i] && <small className="ain-field-error">{errors[i]}</small>}
            </label>)}<div className="ain-plan-actions"><button type="submit" className="ain-button ain-primary">保存参数</button>
              <button type="button" className="ain-button" onClick={() => { setValues(initialParameterValues(selected)); setError(''); setNotice('已恢复方案建议值。'); }}>恢复建议值</button></div></fieldset>
          </form></div>
        </> : <p className="ain-management-empty">{selected.tasks.length ? '此方案包含人工任务，暂没有可编辑配置参数。' : '后端未提供具体配置参数；当前只展示处理方向，不自动填写建议值。'}</p>}
        {selected.tasks.length > 0 && <div className="ain-manual-tasks"><strong>需要人工处理</strong><p>下列内容无法通过参数编辑完成：</p><ul>{selected.tasks.map((task, i) => <li key={i}>{task}</li>)}</ul></div>}
        {selected.id === 'legacy-suggestion' && active && <details className="ain-ai-details"><summary>需先核查的事项</summary>
          <p>检查天线、现场遮挡等需人工核查；配置调整需后端提供明确参数。</p>
          <ul>{[...new Set(active.candidates.flatMap(c => c.checks))].map(item => <li key={item}>{item}</li>)}</ul></details>}
        {(selected.conditions.length > 0 || selected.tradeoffs.length > 0) && <details className="ain-ai-details"><summary>适用条件与代价</summary>
          <p>条件：{selected.conditions.join('；') || '未提供'}</p><p>代价／限制：{selected.tradeoffs.join('；') || '未提供'}</p></details>}
        <div className="ain-plan-actions" aria-busy={exportPending}>
          <button type="button" className="ain-button ain-primary" disabled={disabled || mismatch || exportPending} onClick={() => void exportPlan()}>{exportPending ? '正在整理…' : '导出方案报告'}</button>
          <button type="button" className="ain-button" disabled={disabled || mismatch || exportPending} onClick={() => void exportPlan('json')}>导出 JSON 数据</button>
          <span className="ain-management-muted">报告可离线阅读、打印为 PDF</span></div>
      </> : <p className="ain-management-empty">选择方案后，自动展示配置或人工检查项。</p>}
      {error && <p className="ain-management-warning" role="alert">{error}</p>}{notice && <p className="ain-management-notice" role="status">{notice}</p>}
    </section>

    <section className="ain-effect-summary" aria-labelledby="ain-effect-heading">
      <div className="ain-ai-subheading"><h4 id="ain-effect-heading">仿真预计效果</h4><span>{
        // 徽标必须区分"没选""选了没跑""跑失败了""跑过但参数变了"（复审 P3：
        // 失败态曾显示"尚未评估"，把"已失败"说成"未评估"）
        currentEffect ? '仿真结果'
          : selected?.simulation?.status === 'failed' ? '评估失败'
            : selected?.simulation?.status === 'completed' ? '待重新评估'
              : selected ? '尚未评估' : '尚未选择方案'
      }</span></div>
      {currentEffect ? <PlanEffects key={currentEffect.id} result={currentEffect} hideSummary={aiAnswerActive} /> : <div className="ain-effect-pending" role="status">
        <strong>{
          // 正文与徽标、与上方"已选 N 个方案"必须自洽：已选中时不能再说"尚未选择方案"
          selected?.simulation?.status === 'failed' ? (selected.simulation.summary || '仿真失败，请调整参数后重试。')
            : selected?.simulation?.status === 'completed' ? '参数或范围已变化，原仿真结果不再展示。'
              : selected?.simulation ? (selected.simulation.summary || '此方案未返回可用仿真结果。')
                : selected ? '已选择方案，尚未运行仿真' : '尚未选择方案，暂无仿真结果'
        }</strong>
        <p>{selected
          ? '点下方「运行方案仿真」，这里会展示原配置与新方案的图表和指标对比。'
          : '这里只对接「现存网络异常」的研判：先完成研判并选择方案，才会出现参数与仿真。'
            + '预测预警里的风险已可在预测方案面板直接生成方案并查看默认效果（那里不经过本模块）。'}</p></div>}
      <div className="ain-plan-actions"><button type="button" className="ain-button"
        disabled={disabled || mismatch || simPending || !selected} onClick={runSimulation}>
        {simPending ? '正在仿真…' : currentEffect ? '重新运行仿真' : '运行方案仿真'}</button>
        <span className="ain-management-muted">{selected ? '按当前参数在同一窗口重算' : '先选择方案'}</span></div>
      {baseline && <details className="ain-ai-details"><summary>查看调整前历史观测 · {baseline.apId}</summary><p>{baseline.sampleCount} 条关联采样 · 仅为历史参照</p>
        <div className="ain-history-baseline">{(['rsrpDbm', 'sinrDb', 'throughputMbps'] as const).map(key => <span key={key}>{METRIC_META[key].label}：{displayMetric(baseline.metrics[key].mean ?? undefined)} {METRIC_META[key].unit}</span>)}</div></details>}
    </section>
  </section>;
}
