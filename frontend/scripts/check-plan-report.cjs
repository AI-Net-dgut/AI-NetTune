// 无需新增依赖：在 frontend 中执行 node scripts/check-plan-report.cjs。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const ts = require('typescript');
const root = path.resolve(__dirname, '..');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'nettune-report-'));
function compile(relative) {
  const source = path.join(root, 'src', relative + '.ts');
  const destination = path.join(temporary, relative + '.js');
  if (fs.existsSync(destination)) return;
  const compiled = ts.transpileModule(fs.readFileSync(source, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2023 },
  }).outputText;
  fs.mkdirSync(path.dirname(destination), { recursive: true });
  fs.writeFileSync(destination, compiled);
  for (const match of compiled.matchAll(/require\("(\.[^"]+)"\)/g)) {
    compile(path.normalize(path.join(path.dirname(relative), match[1])));
  }
}
try {
  compile('components/management/planReport');
  const { capturePlanExport, renderPlanReport, planExportFilename, downloadPlanFile } = require(path.join(temporary, 'components/management/planReport.js'));
  const scope = { scenario: '校园', start: '2026-01-01 20:00:00', end: '2026-01-01 22:00:00' };
  const plan = {
    id: 'PLAN-AP-01-offload-1', name: '邻区负载均衡', description: '将业务分流至邻区。', scope,
    objectIds: ['AP-01'], goals: ['resourceUtilizationPct', 'throughputMbps'],
    conditions: ['邻区有剩余容量'], tradeoffs: ['可能增加切换'], tasks: ['确认邻区 PRB 余量'],
    parameters: [{ id: 'offload_pct', objectId: 'AP-01', key: 'offload_pct', label: '向邻区分流比例',
      description: '', type: 'number', unit: '%', currentValue: 0, suggestedValue: 15, editable: true,
      unavailableReason: '', min: 0, max: 50, step: 1, options: [] }],
    simulation: { id: 'SIM-1', status: 'completed', scope, parameterValues: { offload_pct: 15 },
      method: '测试用经验模型', summary: '', assumptions: ['同窗口同采样'], limitations: ['非实测'],
      metrics: [{ objectId: 'AP-01', key: 'resourceUtilizationPct', before: 90, after: 78 },
        { objectId: 'AP-01', key: 'rsrpDbm', before: -90.9, after: -90.9 },
        { objectId: 'AP-01', key: 'throughputMbps', before: 30.4, after: 37.8 }], trend: null, coverage: null },
  };
  const diagnosis = {
    scope, state: 'anomaly', currentState: 'recovered_in_window', lastAnomalyAt: '',
    performanceHints: [], anomalyScope: 'window', anomalySpans: [], anomalyType: '拥塞', level: '高',
    score: 1, cause: '容量不足', suggestion: '核查邻区容量', affectedAps: ['AP-01'],
    evidence: [{ metric: 'congestion_ratio', value: .705, threshold: .25, status: '异常' }],
    candidates: [{ rank: 1, cause: '容量不足', score: 1, support: ['高负载'], against: ['根因未确定'], checks: ['核查活跃用户数'] }],
    apCandidates: [], comparison: [], alarms: [],
  };
  const input = { scope, question: '核查 AP-01 拥塞', plan, parameterValues: { offload_pct: '15' }, diagnosis,
    historicalBaseline: null, exportedAt: '2026-11-05T12:00:00Z' };
  const unknown = { status: null, checkedAt: input.exportedAt };
  const snapshot = capturePlanExport(input);
  const html = renderPlanReport(snapshot, unknown);
  assert.ok(html.startsWith('<!doctype html>'));
  assert.ok(html.includes('70.50 %') && html.includes('25.00 %'), '采样占比应转换为百分数');
  const signalCase = structuredClone(snapshot);
  signalCase.simulation.metrics = [{ objectId: 'AP-01', key: 'rsrpDbm', before: -115, after: -111.4 }];
  assert.ok(html.includes('-12.00 个百分点') && renderPlanReport(signalCase, unknown).includes('+3.60 dB'), '变化量应使用正确单位');
  assert.ok(html.includes('截至分析时刻已恢复'));
  assert.ok(html.includes('配置状态未核实'));
  assert.ok(html.includes('2026/11/05 20:00:00'), '导出时间统一使用北京时间');
  assert.ok(!html.includes('simulation_plan_draft'), '默认报告不能直接倾倒 JSON');
  console.log('PASS 完整报告、证据比例、变化单位、恢复状态、北京时间');

  input.parameterValues.offload_pct = '20';
  input.plan.name = '修改后的名字';
  assert.equal(snapshot.parameterValues.offload_pct, '15');
  assert.equal(snapshot.plan.name, '邻区负载均衡');
  const stale = capturePlanExport(input);
  assert.equal(stale.simulation, null); assert.equal(stale.plan.simulation, null);
  const staleHtml = renderPlanReport(stale, unknown);
  assert.ok(staleHtml.includes('待重新评估') && !staleHtml.includes('37.80'));
  const differentWindow = structuredClone(snapshot);
  differentWindow.plan.simulation.scope = { ...differentWindow.scope, end: '2026-01-01 23:00:00' };
  assert.equal(capturePlanExport(differentWindow).simulation, null);
  assert.throws(() => capturePlanExport({ ...input, scope: { ...scope, scenario: '其他' } }));
  console.log('PASS 快照隔离、参数过期、窗口不匹配、范围拦截');

  const missing = structuredClone(snapshot);
  missing.simulation.metrics = [{ objectId: 'AP-01', key: 'throughputMbps', before: null, after: NaN }];
  assert.ok(renderPlanReport(missing, unknown).includes('未提供'));
  missing.diagnosis = null;
  assert.ok(renderPlanReport(missing, unknown).includes('未取得与本次范围一致的研判快照'));
  console.log('PASS 缺失值与缺失研判不冒充零或有效结果');

  const malicious = structuredClone(snapshot);
  malicious.plan.name = '<img src=x onerror=alert(1)>';
  malicious.question = '</p><script>alert(1)</script>';
  const escaped = renderPlanReport(malicious, unknown);
  assert.ok(!escaped.includes('<script>') && !escaped.includes('<img src=x'));
  assert.ok(escaped.includes('&lt;script&gt;'));
  assert.ok(!/[<>:"/\\|?*]/.test(planExportFilename(malicious, 'html')));
  console.log('PASS HTML 转义与 Windows 文件名');

  const active = { id: 'CFG-1', scenario: '校园', apIds: ['AP-01'], params: { offload_pct: 15 },
    effectiveFrom: scope.end, appliedAt: input.exportedAt, planId: snapshot.plan.id };
  const config = { status: { active, versions: 1, revision: 1 }, checkedAt: input.exportedAt };
  assert.ok(renderPlanReport(snapshot, config).includes('所列参数与本次方案一致'));
  active.params = { capacity: 'one_carrier', offload_pct: 0 };
  assert.ok(renderPlanReport(snapshot, config).includes('未确认与本次所选方案一致'));
  assert.ok(renderPlanReport(snapshot, { ...config, status: { ...config.status, active: null } }).includes('无已生效仿真配置'));
  console.log('PASS 生效配置匹配、不匹配、未应用三种状态');

  let clicked = false, removed = false, revoked = false;
  const element = { click: () => { clicked = true; }, remove: () => { removed = true; } };
  global.document = { createElement: () => element, body: { appendChild() {} } };
  global.window = { setTimeout: fn => fn() };
  URL.createObjectURL = () => 'blob:test'; URL.revokeObjectURL = () => { revoked = true; };
  downloadPlanFile(html, 'report.html', 'text/html');
  assert.ok(clicked && removed && revoked); assert.equal(element.download, 'report.html');
  console.log('PASS 下载触发与临时资源清理');
  if (process.env.PLAN_REPORT_PREVIEW) {
    fs.writeFileSync(process.env.PLAN_REPORT_PREVIEW, html.replace('本次分析与配置快照', '排版测试示例（非实际评估结果）'));
  }
} finally {
  fs.rmSync(temporary, { recursive: true, force: true });
}
