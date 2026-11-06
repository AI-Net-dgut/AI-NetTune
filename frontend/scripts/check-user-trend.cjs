// 观测序列、范围、断档与固定目标线的行为检查。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const ts = require('typescript');
const root = path.resolve(__dirname, '..');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'nettune-trend-'));
function compile(relative) {
  const destination = path.join(temporary, relative + '.js');
  if (fs.existsSync(destination)) return;
  const source = fs.readFileSync(path.join(root, 'src', relative + '.ts'), 'utf8');
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2023 } }).outputText;
  fs.mkdirSync(path.dirname(destination), { recursive: true }); fs.writeFileSync(destination, compiled);
  for (const match of compiled.matchAll(/require\("(\.[^"]+)"\)/g)) compile(path.normalize(path.join(path.dirname(relative), match[1])));
}
try {
  for (const source of ['services/userExperience', 'chat/userTrend', 'chat/queryState', 'history/store']) compile(source);
  const { parseUserTrend } = require(path.join(temporary, 'services/userExperience.js'));
  const { userTrendOption } = require(path.join(temporary, 'chat/userTrend.js'));
  const { initialQueryState, queryReducer } = require(path.join(temporary, 'chat/queryState.js'));
  const subject = { type: 'user', id: 'UE-03', apConstraint: 'AP-01' };
  const goal = { status: 'pending', baseline: 50, target: 55, percent: 10 };
  const trend = parseUserTrend({ subject, goal, series: [
    { time: '2026-01-02T14:05:00+08:00', apId: 'AP-01', throughput: 60, rsrp: -90 },
    { time: '2026-01-02T14:00:00+08:00', apId: 'AP-01', throughput: null, rsrp: -110 },
    { time: '2026-01-02T14:01:00+08:00', apId: 'AP-01', throughput: Infinity, rsrp: -100 },
    { time: '2026-01-02T14:02:00+08:00', apId: 'AP-03', throughput: 999 },
    { time: 'invalid', apId: 'AP-01', throughput: 999 },
  ] });
  assert.equal(trend.points.length, 3);
  assert.equal(trend.points[0].throughput, null); assert.equal(trend.points[1].throughput, null);
  assert.ok(trend.points.every(p => p.apId === 'AP-01'));
  let option = userTrendOption(trend, 'throughput');
  assert.equal(option.series[0].connectNulls, false);
  assert.equal(option.series[0].data.length, 4); // 时间断档加入空点。
  assert.ok(option.yAxis.min < 55 && option.yAxis.max > 55);
  assert.equal(option.series[0].markLine.data[0].yAxis, 55); // 固定均值目标，不随采样变化。
  option = userTrendOption(trend, 'rsrp', { checks: [{ metric: 'rsrp', threshold: -105 }] });
  assert.equal(option.series[0].markLine.data[0].yAxis, -105);
  assert.equal(userTrendOption({ points: trend.points }, 'throughput').series[0].markLine.data.length, 0);
  assert.equal(parseUserTrend({ series: [] }), undefined);
  console.log('PASS AP 约束、排序、null、非有限值、断档及固定目标/判据线');
  const scope = { scenario: '校园', start: '2026-01-02 14:00:00', end: '2026-01-02 14:59:00' };
  const result = { intent: 'user_experience', reply: '用户分析', scope, subject, userTrend: trend, diagnosis: null, chart: null };
  let state = queryReducer(initialQueryState, { type: 'start', id: 1, question: 'UE-03 体验' });
  state = queryReducer(state, { type: 'success', id: 1, result });
  assert.equal(state.diagnosisResult.userTrend, trend);
  state = queryReducer(state, { type: 'start', id: 2, question: '查看连接图', preserveDiagnosis: true });
  assert.equal(state.diagnosisResult.userTrend, trend);
  state = queryReducer(state, { type: 'start', id: 3, question: '分析 AP-03' });
  assert.equal(state.diagnosisResult, null);
  assert.equal(queryReducer(state, { type: 'success', id: 1, result }).result, null);
  console.log('PASS 辅助查询保留关联趋势，新任务清除旧图，迟到响应不恢复旧图');
  const channel = globalThis.BroadcastChannel;
  globalThis.BroadcastChannel = undefined;
  const { compactPayload } = require(path.join(temporary, 'history/store.js'));
  globalThis.BroadcastChannel = channel;
  const archived = JSON.parse(JSON.stringify(compactPayload('analysis', { response: result })));
  assert.equal(archived.response.subject.id, 'UE-03');
  assert.equal(archived.response.userTrend.points[0].throughput, null);
  assert.equal(archived.response.userTrend.goal.target, 55);
  assert.deepEqual(archived.response.scope, scope);
  console.log('PASS 归档和 JSON 导出保留用户范围、观测序列及固定目标');

} finally { fs.rmSync(temporary, { recursive: true, force: true }); }
