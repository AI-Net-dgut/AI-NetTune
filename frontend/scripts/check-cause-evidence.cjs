// 检查原因入口选择正确的数据视图；AP 约束不在用户对照图中丢失。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const source = fs.readFileSync(path.resolve(__dirname, '../src/chat/causeEvidence.ts'), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2023 } }).outputText;
const sandbox = { exports: {} };vm.createContext(sandbox);vm.runInContext(compiled, sandbox);
const { causeEvidenceSelection, evidenceComparisonRows, userMetricForEvidence } = sandbox.exports;
for (const [cause, view, metric] of [
  ['天线倾角或遮挡导致弱覆盖', 'coverage', 'rsrpDbm'], ['用户连接分配不均', 'connections', undefined],
  ['负载过高引起拥塞', 'trend', 'resourceUtilizationPct'], ['SINR偏低，疑似同频干扰', 'trend', 'sinrDb'],
]) {
 const result = causeEvidenceSelection({ cause }, 'AP-03');
 assert.equal(result.view, view);assert.equal(result.metric, metric);assert.equal(result.objectId, 'AP-03');
}
const rows = [{ apId: 'AP-01', ueId: 'UE-03' }, { apId: 'AP-03', ueId: 'UE-03' }, { apId: 'AP-01', ueId: 'UE-02' }];
assert.equal(evidenceComparisonRows(rows, { apConstraint: 'AP-01' }).length, 2);
assert.ok(evidenceComparisonRows(rows, { apConstraint: 'AP-01' }).every(r => r.apId === 'AP-01'));
assert.equal(evidenceComparisonRows(rows, undefined), rows);
assert.equal(userMetricForEvidence('rsrpDbm'), 'rsrp');assert.equal(userMetricForEvidence('sinrDb'), 'sinr');
assert.equal(userMetricForEvidence('throughputMbps'), 'throughput');
console.log('PASS 候选原因到覆盖/连接/PRB/SINR证据图的导航，以及用户AP范围约束');
