import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { listRecords, deleteRecords, downloadRecords, retryRecords, historyProblem, MAX_HISTORY_QUESTIONS } from './store';
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
      {content.plans.map(plan => <article className="history-plan" key={plan.id}>
        {/* 来源标签从 h4 移出：标题保持简短（a11y 可及名不被 37 字说明撑长），标签单独一行 */}
        <h4>{plan.name}</h4>
        <p className="history-plan-source">
          {plan.source === 'system' ? '系统建议方案（模板生成；AI 仅负责文字研判，不参与选方案）' : 'AI 选择方案'}
        </p>
        {/* 10-30 清单第 3 点：默认顺序固定为"方案 → 本次评估参数 → 效果"。
            独立审计 P2：若本次参数还没有对应效果，下面的效果就不是"对应当前参数"的，
            必须在文案上明确区分，不能用同一句话糊过去。 */}
        {plan.deviated && <p className="history-draft">
          本次评估参数<strong>已偏离</strong>原始建议值：
          {/* 按参数名配对，避免快照顺序不同导致"建议 X → 本次 Y"张冠李戴 */}
          {plan.parameters.map(p => {
            const sug = plan.suggested.find(s => s.label === p.label)?.value;
            if (sug === undefined || sug === p.value) return null;
            return <span key={p.label}>{p.label} 建议 {sug} → 本次 {p.value}；</span>;
          })}
          {plan.latestMatched
            ? '下面的效果对应"本次评估参数"。'
            : plan.latest
              ? '本次参数尚无对应效果，下面的结果是其它参数版本的。'
              : '本次参数尚无对应效果。'}
        </p>}
        <div className="history-section-block">
          <h5>本次评估参数</h5>
          {plan.parameters.length ? <Parameters values={plan.parameters}/> : <p className="history-muted">未记录参数。</p>}
        </div>
        {plan.latest ? <div className="history-section-block">
          <h5>{plan.latestMatched ? '仿真预计效果（对应上方参数）' : '最近一次评估的效果（参数与上方不同）'}</h5>
          <Effect effect={plan.latest}/>
        </div> : (!plan.others.length ? <p className="history-muted">尚未保存仿真效果。</p> : null)}
        {plan.draft && <p className="history-draft">参数已修改，尚无对应的新效果；上面的结果只适用于它自己记录的参数。</p>}
        {/* 唯一一次仿真失败时，失败原因只在折叠区里 → 可见区域必须留一句提示
            （独立审计 P3：不能因为修"两提示并存"而把失败提示整个藏起来） */}
        {!plan.latest && plan.others.length > 0 && <p className="history-draft">
          本次参数还没有成功的效果记录；失败原因见下方"其他评估版本"。
        </p>}
        {plan.suggested.length > 0 && plan.deviated && <details>
          <summary>建议参数（原始模板值）</summary>
          <Parameters values={plan.suggested}/>
          <p className="history-muted">方案描述里的"下调/调整"等措辞描述的是模板建议，不代表本次评估实际使用的参数。</p>
        </details>}
        {plan.others.length > 0 && <details>
          <summary>其他评估版本（{plan.others.length}）</summary>
          {plan.others.map((e, i) => <Effect key={`${e.id}-${i}`} effect={e}/>)}
        </details>}
        {(plan.tasks.length > 0 || plan.conditions.length > 0 || plan.tradeoffs.length > 0) && <details>
          <summary>人工操作与适用条件</summary>
          {([{ title: '人工操作', values: plan.tasks }, { title: '适用条件', values: plan.conditions }, { title: '影响与取舍', values: plan.tradeoffs }]).map(group => group.values.length > 0 && <div key={group.title}><strong>{group.title}</strong><ul>{group.values.map((v, i) => <li key={i}>{v}</li>)}</ul></div>)}
        </details>}
        {plan.description && <details><summary>方案说明（原始建议措辞）</summary><p className="history-plan-desc">{plan.description}</p></details>}
      </article>)}
      {content.plans.some(p => p.effects.length) && <p className="history-muted">以上是仿真预计效果，不代表真实网络配置或实际验证结果。</p>}
    </section>
  </>;
}
function HistoryContent() {
  const [items, setItems] = useState<HistoryRecord[]>([]);
  const [selected, setSelected] = useState(''), [q, setQ] = useState(''), [notice, setNotice] = useState('');
  const [loading, setLoading] = useState(true);
  const [managing, setManaging] = useState(false), [checked, setChecked] = useState<Set<string>>(() => new Set());
  const [deleting, setDeleting] = useState(false);
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
  const picked = entries.filter(e => checked.has(e.record.id));
  const allShownChecked = shown.length > 0 && shown.every(e => checked.has(e.record.id));
  const groupRecords = (groups: QuestionEntry[]) => [...new Map(groups.flatMap(e => [e.record, ...e.members]).map(r => [r.id, r])).values()];
  const toggle = (id: string, value: boolean) => setChecked(prev => { const next = new Set(prev); if (value) next.add(id); else next.delete(id); return next; });
  const toggleShown = (value: boolean) => setChecked(prev => { const next = new Set(prev); for (const entry of shown) { if (value) next.add(entry.record.id); else next.delete(entry.record.id); } return next; });
  const exportGroups = (groups: QuestionEntry[]) => {
    if (!groups.length) return;
    try { downloadRecords(groupRecords(groups)); }
    catch { setNotice('导出失败，请重试。原记录仍保留在当前浏览器。'); }
  };
  const remove = async (groups: QuestionEntry[]) => {
    if (deleting || !groups.length || !window.confirm(`删除所选 ${groups.length} 个问题及其研判、方案和仿真记录？删除后无法恢复。`)) return;
    setDeleting(true);
    try {
      const ids = new Set(groupRecords(groups).map(r => r.id));
      await deleteRecords([...ids]);
      setItems(prev => prev.filter(r => !ids.has(r.id)));
      setChecked(prev => new Set([...prev].filter(id => !ids.has(id))));
      setSelected(prev => ids.has(prev) ? '' : prev);
      setNotice(historyProblem());
    }
    catch { setNotice('删除失败，请重试。'); }
    finally { setDeleting(false); }
  };
  return <main className="history-page">
    <header className="history-heading"><div><h1>分析记录</h1><p>按提问保存研判、方案与预计效果。</p></div><Link className="ain-button" to="/workbench">返回工作台</Link></header>
    <div className="history-toolbar"><input aria-label="搜索问题或时间" placeholder="搜索问题或时间" value={q} onChange={e => setQ(e.target.value)}/><span>{q.trim() ? `匹配 ${shown.length} 个 · ` : ''}已保存 {entries.length} / {MAX_HISTORY_QUESTIONS} 个问题</span><button type="button" disabled={loading || deleting} onClick={() => { setManaging(prev => !prev); setChecked(new Set()); }}>{managing ? '完成管理' : '批量管理'}</button></div>
    {managing && <div className="history-batch-actions" aria-label="批量管理记录">
      <label><input type="checkbox" aria-label="全选当前搜索结果" checked={allShownChecked} disabled={!shown.length || deleting} onChange={e => toggleShown(e.target.checked)}/>全选当前结果</label>
      <span role="status">已选 {picked.length} 个问题{q.trim() && picked.some(e => !shown.includes(e)) ? '（含搜索结果以外的选择）' : ''}</span>
      <button type="button" disabled={!picked.length || deleting} onClick={() => setChecked(new Set())}>取消选择</button>
      <button type="button" disabled={!picked.length || deleting} onClick={() => exportGroups(picked)}>导出所选</button>
      <button type="button" className="history-delete-button" disabled={!picked.length || deleting} onClick={() => remove(picked)}>{deleting ? '正在删除…' : '删除所选'}</button>
    </div>}
    {notice && <div className="history-error" role="alert">{notice}<button type="button" onClick={() => void retryRecords().then(() => setNotice(historyProblem())).catch(() => setNotice('重试保存失败，请稍后再试。'))}>重试保存</button></div>}
    {loading ? <p role="status">正在读取记录…</p> : <div className="history-layout">
      <aside aria-label="问题记录">{shown.map(({ record: r }) => <div key={r.id} className={`history-record-row${managing ? ' managing' : ''}`}>
        {managing && <input className="history-record-check" type="checkbox" aria-label={`选择记录：${text(r.question) || '未记录问题'}`} checked={checked.has(r.id)} disabled={deleting} onChange={e => toggle(r.id, e.target.checked)}/>}
        <button type="button" className={`history-card${active?.record.id === r.id ? ' selected' : ''}`} aria-pressed={active?.record.id === r.id} onClick={() => setSelected(r.id)}>
        <time>{savedTime(r.createdAt)}</time><strong>{text(r.question) || '未记录问题'}</strong><span>分析至 {text(r.analysisTime) || text(object(r.scope).end) || '时间未记录'}</span>
        </button></div>)}{!shown.length && <p className="history-muted">{entries.length ? '没有匹配的问题。' : '暂无提问记录，发送分析问题后会自动保存。'}</p>}</aside>
      <section className="history-detail" aria-label="分析记录详情">{active ? <><div className="history-actions"><button type="button" disabled={deleting} onClick={() => exportGroups([active])}>导出本次记录</button><button type="button" disabled={deleting} onClick={() => remove([active])}>删除</button></div>
        <HistoryErrorBoundary key={active.record.id}><RecordDetails entry={active}/></HistoryErrorBoundary></> : <p className="history-muted">选择一个问题，查看研判与方案效果。</p>}</section>
    </div>}
    <p className="history-storage-note">仅保存在当前浏览器的当前网站地址下，不会同步给其他电脑或浏览器；共用同一浏览器时可见。最多保存 {MAX_HISTORY_QUESTIONS} 个问题及其研判、方案和仿真效果，满额后请先导出或删除，不自动清理旧记录。清除网站数据后记录会丢失。</p>
  </main>;
}
export default function HistoryPage() { return <HistoryErrorBoundary><HistoryContent/></HistoryErrorBoundary>; }
