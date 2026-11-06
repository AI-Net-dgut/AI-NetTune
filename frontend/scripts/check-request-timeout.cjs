// 直接执行生产请求函数，检查分析余量、普通请求超时和用户取消语义。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const source = fs.readFileSync(path.resolve(__dirname, '../src/services/backend.ts'), 'utf8');
const parsed = ts.createSourceFile('backend.ts', source, ts.ScriptTarget.Latest, true);
const fn = parsed.statements.find(s => ts.isFunctionDeclaration(s) && s.name?.text === 'request');
assert.ok(fn, '生产请求函数存在');
let timer, delay, cleared = false;
const sandbox = { AbortController, DOMException, API_BASE: '', fetch: () => {},
  obj: v => v, str: v => typeof v === 'string' ? v : '',
  setTimeout: (f, ms) => { timer = f; delay = ms; return 1; },
  clearTimeout: () => { cleared = true; }, exports: {} };
vm.createContext(sandbox);
const code = ts.transpileModule(fn.getText(parsed) + '\nexports.request = request;',
  { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2023 } }).outputText;
vm.runInContext(code, sandbox);
const request = sandbox.exports.request;
const success = async () => ({ ok: true, json: async () => ({ code: 0, data: { ready: true } }) });
const pending = async (_url, options) => new Promise((_resolve, reject) => {
  if (options.signal.aborted) reject(new DOMException('取消', 'AbortError'));
  else options.signal.addEventListener('abort', () => reject(new DOMException('取消', 'AbortError')), { once: true });
});
(async () => {
  assert.equal((await request('/agent/chat', undefined, { message: '分析AP' }, success)).ready, true);
  assert.equal(delay, 90000); assert.equal(cleared, true);
  await request('/health', undefined, undefined, success); assert.equal(delay, 45000);
  cleared = false;
  const timeout = request('/agent/chat', undefined, {}, pending);
  timer();
  await assert.rejects(timeout, /请求超时/); assert.equal(cleared, true);
  const user = new AbortController();
  const cancelled = request('/agent/chat', user.signal, {}, pending); user.abort();
  await assert.rejects(cancelled, e => e.name === 'AbortError' && e.message === '请求已取消');
  console.log('PASS 分析请求90秒、普通请求45秒；超时与用户取消区分，计时器清理');
})().catch(error => { console.error(error); process.exitCode = 1; });
