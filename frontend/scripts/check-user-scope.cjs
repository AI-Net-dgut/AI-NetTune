// 验证用户范围跨解析、状态保存、重评估和报告导出后仍保持一致。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const ts = require('typescript');
const root = path.resolve(__dirname, '..');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'nettune-user-scope-'));
function compile(relative) {
  const destination = path.join(temporary, relative + '.js');
  if (fs.existsSync(destination)) return;
  const source = fs.readFileSync(path.join(root, 'src', relative + '.ts'), 'utf8');
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2023 } }).outputText;
  fs.mkdirSync(path.dirname(destination), { recursive: true });
  fs.writeFileSync(destination, compiled);
  for (const match of compiled.matchAll(/require\("(\.[^"]+)"\)/g)) compile(path.normalize(path.join(path.dirname(relative), match[1])));
}
try {
  for (const name of ['services/planContract', 'components/management/planReport', 'chat/queryState']) compile(name);
  const { parsePlans, parseSimulation } = require(path.join(temporary, 'services/planContract.js'));
  const { capturePlanExport, renderPlanReport } = require(path.join(temporary, 'components/management/planReport.js'));
  const { initialQueryState, queryReducer } = require(path.join(temporary, 'chat/queryState.js'));
  const scope = { scenario: '校园', start: '2026-01-02 14:00:00', end: '2026-01-02 14:59:00' };
  const subject = { type: 'user', id: 'UE-03', primaryAp: 'AP-03', servingAps: ['AP-03'], samples: 60,
    coverageScope: 'ap', trendScope: 'ap', note: '指标为用户，空间与趋势为 AP' };
  const goal = { status: 'pending', baseline: 50, percent: 10, target: 55, predicted: null, gap: null, improvementPct: null };
  const assessment = { dataStatus: 'ready', windowAssessment: 'within_policy', endState: 'persistent_anomaly',
    policy: { version: 'test', source: '测试经验门槛，未实网标定', badShareLimit: .1, persistentMinutes: 5 },
    checks: [{ metric: 'sinr', value: 13.75, threshold: 5, share: 5/60, validSamples: 60, totalSamples: 60, ok: true, label: 'SINR<5 dB' }],
    anomalySpans: [{ metric: 'sinr', start: '2026-01-02 14:55:00', end: scope.end, minutes: 5 }] };
  const plan = { id: 'PLAN-instance-1', name: '用户方案', description: '', scope, objectIds: ['AP-03'],
    subject, goal, goals: ['throughputMbps'], conditions: [], tradeoffs: [], tasks: [],
    parameters: [{ id: 'tilt', objectId: 'AP-03', key: 'tilt', label: '倾角', type: 'number',
      currentValue: 8, suggestedValue: 5, min: 0, max: 15, step: 1, editable: true }],
    simulation: { id: 'SIM-instance-1', status: 'completed', scope, subject, method: '经验估计', summary: 'UE-03 维度',
      assumptions: [], limitations: [], parameterValues: { tilt: 5 }, metrics: [{ key: 'throughputMbps', objectId: 'UE-03', before: 50, after: 56 }],
      goalAssessment: { ...goal, status: 'estimated_met', predicted: 56, gap: 0, improvementPct: 12 }, trend: null, coverage: null } };
  const parsed = parsePlans([plan], scope)[0];
  assert.equal(parsed.subject.id, 'UE-03');
  assert.equal(parsed.simulation.subject.trendScope, 'ap');
  assert.equal(parsed.goal.target, 55);
  assert.equal(parseSimulation(plan.simulation, scope).goalAssessment.status, 'estimated_met');
  console.log('PASS 用户主体、AP 范围与提升目标跨前端解析保留');
  const configuration = { status: null, checkedAt: '2026-11-06T02:00:00Z' };
  const input = { scope, question: 'UE-03 提高吞吐10%', plan: parsed, parameterValues: { tilt: '5' }, diagnosis: null,
    userSubject: subject, userAssessment: assessment, historicalBaseline: null, exportedAt: configuration.checkedAt };
  let report = renderPlanReport(capturePlanExport(input), configuration);
  assert.ok(report.includes('UE-03') && report.includes('AP-03') && report.includes('连续异常'));
  assert.ok(report.includes('预计达到目标') && report.includes('55.00 Mbps'));
  const stale = capturePlanExport({ ...input, parameterValues: { tilt: '4' } });
  report = renderPlanReport(stale, configuration);
  assert.equal(stale.simulation, null);
  assert.ok(report.includes('待重新评估') && !report.includes('预计达到目标'));
  console.log('PASS 用户报告同时记录整窗与末端状态；改参数不沿用旧目标结论');
  const started = queryReducer(initialQueryState, { type: 'start', id: 1, question: 'UE-03 体验' });
  const response = { reply: '用户分析', intent: 'user_experience', scope, subject, userAssessment: assessment, plans: [parsed], diagnosis: null, chart: null };
  const done = queryReducer(started, { type: 'success', id: 1, result: response });
  const auxiliary = queryReducer(done, { type: 'start', id: 2, question: '查看指标', preserveDiagnosis: true });
  assert.equal(auxiliary.diagnosisResult.subject.id, 'UE-03');
  console.log('PASS 辅助查询保留用户分析与关联方案');
} finally {
  fs.rmSync(temporary, { recursive: true, force: true });
}
