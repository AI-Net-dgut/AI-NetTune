import { endStateLabels, goalLabels, windowAssessmentLabels } from '../../services/userExperience';
import type { UserAssessment } from '../../types/plan';
import type { ChatResult } from '../../types/backend';
import { METRIC_META } from '../../types/backend';
import type { Diagnosis, QueryScope } from '../../types/backend';
import type { TreatmentPlan } from '../../types/plan';
import type { SimConfigStatus } from '../../services/backend';
import type { BaselineSnapshot } from './managementDraft';
import { diagnosisParagraphs, sameQueryScope } from '../../chat/diagnosisModel';
import { candidateGroups, isSimulationCurrent } from './planWorkspace';

export interface PlanExportInput {
  scope: QueryScope;
  question: string;
  plan: TreatmentPlan;
  parameterValues: Record<string, string>;
  diagnosis: Diagnosis | null;
  userSubject?: ChatResult["subject"];
  userAssessment?: UserAssessment;
  historicalBaseline: BaselineSnapshot | null;
  exportedAt: string;
}
export interface ConfigurationSnapshot {
  status: SimConfigStatus | null;
  checkedAt: string;
}

/** 点击时冻结内容；报告与 JSON 共用同一份快照，不重新生成研判。 */
export function capturePlanExport(input: PlanExportInput) {
  if (!sameQueryScope(input.plan.scope, input.scope)) throw new Error('方案与分析范围不同，请重新研判。');
  const simulation = isSimulationCurrent(input.plan, input.parameterValues, input.scope)
    ? input.plan.simulation : null;
  const diagnosis = input.diagnosis && sameQueryScope(input.diagnosis.scope, input.scope)
    ? input.diagnosis : null;
  return structuredClone({
    version: 1, kind: 'simulation_plan_draft', ...input, diagnosis,
    plan: { ...input.plan, simulation }, simulation,
  });
}
export type PlanExportSnapshot = ReturnType<typeof capturePlanExport>;

const escapeHtml = (value: unknown) => String(value ?? '').replace(/[&<>"']/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!);
const number = (value: number | null | undefined) =>
  typeof value === 'number' && Number.isFinite(value) ? value.toFixed(2) : '未提供';
const paragraph = (value: string) => `<p>${escapeHtml(value || '未提供')}</p>`;
const list = (items: string[], empty = '未提供，需人工确认。') => items.length
  ? `<ul>${items.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul>` : paragraph(empty);
const table = (headers: string[], rows: string[][]) => `<table><thead><tr>${headers.map(h =>
  `<th scope="col">${escapeHtml(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(row =>
  `<tr>${row.map(cell => `<td>${escapeHtml(cell)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
const section = (title: string, content: string) => `<section><h2>${escapeHtml(title)}</h2>${content}</section>`;
const time = (value: string) => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(date);
};

function parameterText(plan: TreatmentPlan, id: string, value: unknown) {
  if (value == null || value === '') return '未提供';
  const p = plan.parameters.find(item => item.id === id);
  const label = p?.options.find(option => option.value === String(value))?.label ?? String(value);
  return `${label}${p?.unit ? ` ${p.unit}` : ''}`;
}

function configurationSection(snapshot: PlanExportSnapshot, configuration: ConfigurationSnapshot) {
  const { status } = configuration;
  let content = paragraph(`配置核实时间：${time(configuration.checkedAt)}（北京时间）。此处记录查询时状态，后续可能变更。`);
  if (!status) return content + paragraph('配置状态未核实：接口暂不可用。本报告不据此认定方案已应用或未应用。');
  if (!status.active) return content + paragraph('查询时无已生效仿真配置。本次方案尚不能认定已应用，导出也不会执行配置。');
  const active = status.active;
  const sameObjects = active.apIds.length === snapshot.plan.objectIds.length
    && snapshot.plan.objectIds.every(id => active.apIds.includes(id));
  // 生效配置通常含完整默认参数，选中方案可能仅调整其中几项。
  const sameParameters = snapshot.plan.parameters.length > 0 && snapshot.plan.parameters.every(p => {
    const value = active.params[p.id === 'power' ? 'tx_power' : p.id];
    return value != null && (p.type === 'number'
      ? Number(value) === Number(snapshot.parameterValues[p.id])
      : String(value) === snapshot.parameterValues[p.id]);
  });
  const matches = active.scenario === snapshot.scope.scenario && sameObjects && sameParameters
    && active.planId === snapshot.plan.id;
  content += paragraph(matches
    ? '查询时生效配置的方案标识、对象和所列参数与本次方案一致；生效时刻见下表。该状态仅限仿真网络。'
    : '当前存在已生效配置，但未确认与本次所选方案一致。请分别核对以下配置与第三节的目标参数。');
  content += table(['项目', '查询结果'], [
    ['配置版本', active.id], ['配置修订号', String(status.revision)],
    ['场景 / 对象', `${active.scenario} / ${active.apIds.join('、')}`],
    ['关联方案标识', active.planId || '未提供'], ['生效时刻（北京时间）', active.effectiveFrom],
    ...Object.entries(active.params).map(([key, value]) => [key, String(value)]),
  ]);
  return content + paragraph('如需撤销，请在页面“闭环：应用至仿真网络”中核对当前版本，再使用“恢复原配置”。本报告未执行应用或回滚。');
}

export function renderPlanReport(snapshot: PlanExportSnapshot, configuration: ConfigurationSnapshot): string {
  const { plan, diagnosis, simulation, scope, userSubject, userAssessment } = snapshot;
  const statusLabels: Record<Diagnosis['currentState'], string> = {
    fault_active: '截至分析时刻仍有故障', recovered_in_window: '窗口内发生过异常，截至分析时刻已恢复',
    performance_warning: '性能提示，未确诊故障', normal: '当前范围未发现明显异常',
  };
  const overview = table(['项目', '内容'], [
    ['场景 / 分析对象', `${scope.scenario} / ${userSubject?.id || plan.subject?.id || plan.objectIds.join('、') || '未指定'}`],
    ['配置执行对象', plan.objectIds.join('、') || '未指定'],
    ['分析时间（北京时间）', `${scope.start} — ${scope.end}`],
    ['对应问题', snapshot.question || '未记录原始提问'],
    ['报告生成时间（北京时间）', time(snapshot.exportedAt)],
    ['数据说明', '仿真场景数据；内容取自本次页面研判与方案快照，不代表实网采集或实网执行结果。'],
  ]);
  let findings = diagnosis
    ? `<div class="notice">${escapeHtml(diagnosis.state === 'empty' ? '当前范围没有可分析数据' : statusLabels[diagnosis.currentState] || '状态未提供')}</div>`
      + diagnosisParagraphs(diagnosis).filter(item => item.title !== '判断依据').map(item => `<h3>${escapeHtml(item.title)}</h3>${paragraph(item.text)}`).join('')
    : paragraph('未取得与本次范围一致的研判快照，请回到页面核查后重新导出。');
  if (userAssessment) {
    findings = paragraph(windowAssessmentLabels[userAssessment.windowAssessment])
      + paragraph(endStateLabels[userAssessment.endState]) + paragraph(userAssessment.policy.source)
      + table(['指标判据', '有效样本 / 总样本', '越阈占比', '整窗判断'], userAssessment.checks.map(c => [
        c.label, `${c.validSamples} / ${c.totalSamples}`, c.share == null ? '无法计算' : `${number(c.share*100)} %`,
        c.ok == null ? '有效数据不足' : c.ok ? '未超过暂定门槛' : '超过暂定门槛']))
      + (userAssessment.anomalySpans.length ? table(['指标', '开始', '结束', '连续分钟'], userAssessment.anomalySpans.map(a => [
        a.metric, a.start, a.end, String(a.minutes)])) : '');
  }
  if (diagnosis?.anomalySpans.length) findings += `<h3>异常区间</h3>` + table(
    ['对象', '异常类型', '开始（北京时间）', '结束（北京时间）', '时长（分钟）'],
    diagnosis.anomalySpans.map(span => [span.apId, span.type, span.start, span.end, number(span.minutes)]));
  if (diagnosis) {
    const labels: Record<string, [string, string]> = {
      rsrp: ['RSRP 均值', 'dBm'], sinr: ['SINR 均值', 'dB'], throughput: ['吞吐量均值', 'Mbps'],
      prb_usage: ['PRB 占用率均值', '%'], packet_loss: ['丢包率均值', '%'],
      weak_coverage_ratio: ['弱覆盖采样占比', '%'], congestion_ratio: ['拥塞采样占比', '%'],
      spectrum_low_share: ['低 SINR 采样占比', '%'],
      spectrum_power_bands: ['功率越阈频段数', '个'], spectrum_occupancy_bands: ['占用率越阈频段数', '个'],
    };
    const ratios = new Set(['weak_coverage_ratio', 'congestion_ratio', 'spectrum_low_share']);
    findings += `<h3>指标证据</h3>` + (diagnosis.evidence.length ? table(
      ['指标', '观测值', '参考阈值', '状态'], diagnosis.evidence.map(e => {
        const [label, unit] = labels[e.metric] ?? [e.metric || '未命名指标', '（单位未提供）'];
        const format = (v: number) => `${number(typeof v === 'number' && ratios.has(e.metric) ? v * 100 : v)} ${unit}`;
        return [label, format(e.value), format(e.threshold), e.status || '未提供'];
      })) : paragraph('未提供逐项指标证据。'));
    const groups = candidateGroups(diagnosis).filter(g => g.candidates.length);
    if (groups.length) findings += `<h3>候选原因与待核查事项</h3>${paragraph('以下为候选解释，需要结合支持证据、反证和现场检查确认。')}`
      + groups.map(group => `<h3>${escapeHtml(group.id)}</h3>` + group.candidates.map(c =>
        `<div class="candidate"><strong>${escapeHtml(c.cause)}</strong><h4>支持证据</h4>${list(c.support)}<h4>反证与限制</h4>${list(c.against, '未提供反证；不代表原因已被证实。')}<h4>待核查</h4>${list(c.checks)}</div>`).join('')).join('');
  }
  const parameters = `<h3>方案生成时说明</h3>${paragraph(plan.description)}${paragraph('说明中的数值可能来自初始建议，本次目标参数以下表为准。')}` + table(['项目', '内容'], [
    ['所选方案', plan.name], ['方案标识', plan.id],
    ['优化指标', plan.goals.map(key => METRIC_META[key]?.label ?? key).join('、') || '未提供'],
  ]) + (plan.parameters.length ? table(['对象', '参数', '参考当前值', '本次目标值'], plan.parameters.map(p => [
    p.objectId, p.label, parameterText(plan, p.id, p.currentValue),
    parameterText(plan, p.id, snapshot.parameterValues[p.id]),
  ])) + paragraph('“参考当前值”来自方案生成时的参数信息；当前已生效配置请以第五节核实结果为准。')
    : paragraph('此方案未提供可编辑参数。'));
  let effects = simulation
    ? paragraph('评估状态：已取得与本次参数、分析范围匹配的方案预估。以下是同一分析窗口内的原配置与候选配置对比。')
      + table(['对象', '指标', '原配置', '候选配置', '变化量'], simulation.metrics.map(m => {
        const meta = METRIC_META[m.key];
        const valid = typeof m.before === 'number' && Number.isFinite(m.before)
          && typeof m.after === 'number' && Number.isFinite(m.after);
        const delta = valid ? m.after! - m.before! : null;
        const unit = meta?.unit ?? '';
        return [m.objectId, `${meta?.label ?? m.key} / ${unit}`,
          number(m.before), number(m.after), delta == null ? '未提供'
            : `${delta > 0 ? '+' : ''}${number(delta)} ${unit === '%' ? '个百分点' : m.key === 'rsrpDbm' ? 'dB' : unit}`];
      })) + `<h3>评估方法</h3>${paragraph(simulation.method)}<h3>建模假设</h3>${list(simulation.assumptions)}<h3>评估限制</h3>${list(simulation.limitations)}`
    : `<div class="notice">待重新评估：未取得与当前参数和分析范围匹配的有效仿真结果。请在页面重新运行仿真后再导出。</div>`;
  if (simulation?.subject?.type === 'user') effects += paragraph(
    `指标对象为 ${simulation.subject.id}；覆盖与趋势图为执行 AP ${simulation.subject.primaryAp} 范围。仅改变用户连接该 AP 时的采样。`);
  const target = simulation?.goalAssessment ?? plan.goal;
  if (target) effects += `<h3>吞吐提升目标</h3>` + table(['项目', '内容'], [
    ['评估状态', simulation?.goalAssessment ? goalLabels[target.status] : '待重新评估'],
    ['固定基线', `${number(target.baseline)} Mbps`], ['提升目标', `${number(target.percent)} %`],
    ['目标吞吐', `${number(target.target)} Mbps`],
    ['方案后预计吞吐', simulation?.goalAssessment ? `${number(target.predicted)} Mbps` : '待重新评估'],
    ['距目标差距', simulation?.goalAssessment ? `${number(target.gap)} Mbps` : '待重新评估'],
  ]);
  effects += paragraph('方案预估采用经验模型；配置生效后的数据转换目前仅调整 RSRP 与 PRB，占用率变化不等于其他业务指标已经改善。频谱预警与预测仍基于原始频谱，本轮未模拟配置对频谱的影响。指标改善也不等于故障已消除。');
  const actions = `<h3>适用条件</h3>${list(plan.conditions)}<h3>代价与风险</h3>${list(plan.tradeoffs)}<h3>人工核查与交接</h3>${list(plan.tasks)}${paragraph('执行前核查适用条件，执行后在同一统计口径下复核指标与剩余风险。实际网络调整仍需现场验证。')}`;
  return `<!doctype html>
<html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'">
<title>${escapeHtml(plan.name)}｜AI-NetTune 方案报告</title><style>
*{box-sizing:border-box}body{margin:0;background:#eef3f8;color:#25344a;font:15px/1.75 system-ui,"Microsoft YaHei",sans-serif}
main{max-width:1000px;margin:32px auto;background:white;padding:48px 56px;box-shadow:0 6px 30px #243f6510}
header{border-top:5px solid #3267d6;padding-top:22px;border-bottom:1px solid #dce4ef;padding-bottom:24px}
.brand{font-size:14px;font-weight:700;letter-spacing:1px;color:#3267d6}h1{font-size:30px;line-height:1.4;margin:10px 0;color:#173465}
.subtitle,footer{color:#607187;font-size:13px}h2{font-size:20px;color:#173465;border-bottom:1px solid #dce4ef;padding-bottom:10px;margin:32px 0 16px}
h3{font-size:16px;margin:20px 0 8px}h4{font-size:14px;margin:12px 0 4px;color:#526881}p{margin:10px 0;white-space:pre-wrap;overflow-wrap:anywhere}ul{padding-left:22px}
table{border-collapse:collapse;width:100%;font-size:14px;margin:14px 0;table-layout:fixed}th,td{border:1px solid #dce4ef;text-align:left;padding:10px 12px;vertical-align:top;overflow-wrap:anywhere}
th{background:#f0f5fc;color:#284b7b}tr:nth-child(even) td{background:#fafcff}.notice{padding:14px 18px;border-left:4px solid #c38b28;background:#fff7e8;margin:16px 0;overflow-wrap:anywhere}
th:first-child:nth-last-child(2){width:27%}
.candidate{border:1px solid #dce4ef;border-radius:8px;padding:16px 20px;margin:12px 0}footer{border-top:1px solid #dce4ef;margin-top:32px;padding-top:18px}
@media(max-width:700px){main{margin:0;padding:24px 16px}h1{font-size:24px}th,td{padding:6px;font-size:12px}}
@media print{@page{size:A4;margin:16mm}body{background:white;font-size:11pt}main{max-width:none;margin:0;padding:0;box-shadow:none}h1{font-size:23pt}h2{font-size:15pt;break-after:avoid}h3,h4{break-after:avoid}thead{display:table-header-group}tr{break-inside:avoid}th,td{font-size:10pt;padding:7px}header{break-inside:avoid}.screen-note{display:none}}
</style></head><body><main><header><div class="brand">AI-NetTune · 无线网络优化</div><h1>网络优化方案报告</h1><p>${escapeHtml(plan.name)}</p><div class="subtitle">本次分析与配置快照 · 仿真评估</div></header>
<p class="screen-note subtitle">本报告可离线阅读。需要 PDF 时，使用浏览器“打印 → 另存为 PDF”。</p>
${section('一、分析范围与数据说明', overview)}${section('二、研判结论与证据', findings)}
${section('三、所选方案与目标参数', parameters)}${section('四、方案仿真评估', effects)}
${section('五、当前仿真配置状态', configurationSection(snapshot, configuration))}${section('六、实施条件与交接事项', actions)}
<footer>AI-NetTune · 导出内容用于方案核查与交接；本报告未执行任何网络配置操作。</footer></main></body></html>`;
}

export function planExportFilename(snapshot: PlanExportSnapshot, extension: 'html' | 'json') {
  const stem = `AI-NetTune_${snapshot.plan.name}_${snapshot.plan.objectIds.join('-')}_${snapshot.scope.end}`;
  // 复查（ESLint no-control-regex）：原先正则里带 \u0000-\u001f，规则直接报错。
  // 改成逐字符判断，行为等价：非法文件名字符与控制字符都替换为 '-'。
  const safe = [...stem]
    .map((ch) => (/[<>:"/\\|?*]/.test(ch) || ch.charCodeAt(0) < 32 ? '-' : ch))
    .join('')
    .replace(/\s+/g, '_')
    .slice(0, 140);
  return `${safe}.${extension}`;
}

export function downloadPlanFile(content: string, filename: string, mime: string) {
  const url = URL.createObjectURL(new Blob([content], { type: `${mime};charset=utf-8` }));
  const a = document.createElement('a');
  try {
    a.href = url; a.download = filename; document.body.appendChild(a); a.click();
  } finally {
    a.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}
