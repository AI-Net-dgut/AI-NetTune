import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { listRecords, deleteRecords, downloadRecords, retryRecords, historyProblem } from './store';
import type { HistoryRecord } from './store';
import { object, text, questionEntries, recordContent, searchable } from './historyModel';
import type { QuestionEntry, EffectView } from './historyModel';
import HistoryErrorBoundary from './HistoryErrorBoundary';
import './HistoryPage.css';

function savedTime(value: unknown) {
  const raw = text(value), date = new Date(raw);
  return Number.isNaN(date.getTime()) ? '时间未记录' : date.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false });
}
function Parameters({ values }: { values: { label: string; value: string }[] }) {
  return values.length ? <dl className="history-parameters">{values.map((v, i) => <div key={i}><dt>{v.label}</dt><dd>{v.value}</dd></div>)}</dl> : null;
}
function Effect({ effect }: { effect: EffectView }) {
  return <div className="history-effect"><p className="history-muted">{effect.label} · {savedTime(effect.time)}</p>
    {effect.error ? <p role="alert">{effect.error}</p> : <>{effect.summary && <p>{effect.summary}</p>}
      {effect.metrics.length ? <div className="history-table-wrap"><table><thead><tr><th>指标</th><th>原配置</th><th>采用方案</th></tr></thead>
        <tbody>{effect.metrics.map((m, i) => <tr key={i}><th>{m.label}</th><td>{m.before} {m.unit}</td><td>{m.after} {m.unit}</td></tr>)}</tbody></table></div>
        : <p className="history-muted">尚未返回可对比的效果指标。</p>}</>}
    {effect.parameters.length > 0 && <details><summary>本次效果对应的参数</summary><Parameters values={effect.parameters}/></details>}
  </div>;
}
export function RecordDetails({ entry }: { entry: QuestionEntry }) {
  const { record } = entry, content = recordContent(entry), scope = object(record.scope);
  return <>
    <div className="history-question-heading"><span>{record.kind === 'forecast-analysis' ? '预测问题' : '网络分析'}</span><h2>{text(record.question) || '未记录问题'}</h2>
      <p>提问时间：{savedTime(record.createdAt)}</p>
      <p>分析数据：{text(scope.start) || '未记录'} — {text(scope.end) || text(record.analysisTime) || '未记录'}（北京时间）</p>
      {Object.keys(content.future).length > 0 && <p>预测关注：{text(content.future.start)} — {text(content.future.end)}（北京时间）</p>}
    </div>
    <section className="history-section"><h3>分析与研判</h3>
      {content.error && <p className="history-error" role="alert">{record.status === 'cancelled' ? '本次分析已中断：' : '本次分析未成功：'}{content.error}</p>}
      {content.reply ? <div className="history-answer">{content.reply}</div>
        : <p className="history-muted">{record.status === 'pending' ? '本条尚未保存分析结果；页面关闭或请求中断时也可能停留在此状态。' : content.malformed ? '这条旧记录缺少可读内容，请重新分析。' : '尚未保存分析文字。'}</p>}
    </section>
    <section className="history-section"><h3>解决方案与预计效果</h3>
      {!content.plans.length && <p className="history-answer">{content.suggestion || '本次未生成处理方案或仿真效果。'}</p>}
      {content.plans.map(plan => <article className="history-plan" key={plan.id}><h4>{plan.name}</h4>{plan.description && <p>{plan.description}</p>}
        {plan.parameters.length > 0 && <details><summary>查看方案参数{plan.draft ? '（已修改，待重新仿真）' : ''}</summary><Parameters values={plan.parameters}/></details>}
        {plan.draft && <p className="history-draft">参数已修改，尚无对应的新效果；下方结果仅适用于各次仿真时的参数。</p>}
        {plan.effects[0] ? <Effect effect={plan.effects[0]}/> : <p className="history-muted">尚未保存仿真效果。</p>}
        {plan.effects.length > 1 && <details><summary>此前 {plan.effects.length - 1} 次预计效果</summary>{plan.effects.slice(1).map((e, i) => <Effect key={`${e.id}-${i}`} effect={e}/>)}</details>}
      </article>)}
      {content.plans.some(p => p.effects.length) && <p className="history-muted">以上是仿真预计效果，不代表真实网络配置或实际验证结果。</p>}
    </section>
  </>;
}
function HistoryContent() {
  const [items, setItems] = useState<HistoryRecord[]>([]);
  const [selected, setSelected] = useState(''), [q, setQ] = useState(''), [notice, setNotice] = useState('');
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let alive = true;
    const load = () => void listRecords().then(rows => {
      if (alive) { setItems(rows); setNotice(historyProblem()); setLoading(false); }
    }).catch(reason => { if (alive) { setNotice(`读取本地记录失败：${reason instanceof Error ? reason.message : '未知错误'}`); setLoading(false); } });
    load(); window.addEventListener('aintune-history', load);
    return () => { alive = false; window.removeEventListener('aintune-history', load); };
  }, []);
  const entries = questionEntries(items), shown = entries.filter(e => searchable(e).includes(q.trim().toLowerCase()));
  const active = shown.find(e => e.record.id === selected) ?? shown[0];
  const remove = async () => {
    if (!active || !window.confirm('删除这个问题及其保存的方案、仿真记录？')) return;
    try { await deleteRecords([active.record.id, ...active.members.map(r => r.id)]); setSelected(''); }
    catch { setNotice('删除失败，请重试。'); }
  };
  return <main className="history-page">
    <header className="history-heading"><div><h1>分析记录</h1><p>按提问保存研判、方案与预计效果。</p></div><Link className="ain-button" to="/workbench">返回工作台</Link></header>
    <div className="history-toolbar"><input aria-label="搜索问题或时间" placeholder="搜索问题或时间" value={q} onChange={e => setQ(e.target.value)}/><span>{shown.length} 个问题</span></div>
    {notice && <div className="history-error" role="alert">{notice}<button type="button" onClick={() => void retryRecords().then(() => setNotice(historyProblem())).catch(() => setNotice('重试保存失败，请稍后再试。'))}>重试保存</button></div>}
    {loading ? <p role="status">正在读取记录…</p> : <div className="history-layout">
      <aside aria-label="问题记录">{shown.map(({ record: r }) => <button type="button" key={r.id} className={`history-card${active?.record.id === r.id ? ' selected' : ''}`} aria-pressed={active?.record.id === r.id} onClick={() => setSelected(r.id)}>
        <time>{savedTime(r.createdAt)}</time><strong>{text(r.question) || '未记录问题'}</strong><span>分析至 {text(r.analysisTime) || text(object(r.scope).end) || '时间未记录'}</span>
      </button>)}{!shown.length && <p className="history-muted">{entries.length ? '没有匹配的问题。' : '暂无提问记录，发送分析问题后会自动保存。'}</p>}</aside>
      <section className="history-detail" aria-label="分析记录详情">{active ? <><div className="history-actions"><button type="button" onClick={() => downloadRecords([active.record, ...active.members])}>导出本次记录</button><button type="button" onClick={() => void remove()}>删除</button></div>
        <HistoryErrorBoundary key={active.record.id}><RecordDetails entry={active}/></HistoryErrorBoundary></> : <p className="history-muted">选择一个问题，查看研判与方案效果。</p>}</section>
    </div>}
    <p className="history-storage-note">记录保存在当前浏览器，清除网站数据后会丢失。</p>
  </main>;
}
export default function HistoryPage() { return <HistoryErrorBoundary><HistoryContent/></HistoryErrorBoundary>; }
