import type { AnomalySpan, QueryScope } from '../../types/backend';
export interface AnalysisContext {
  kind: 'current' | 'history' | 'forecast' | 'mixed';
  history: QueryScope;
  question: string;
  baseTime?: string;
  future?: { start: string; end: string };
  anomalySpans?: AnomalySpan[];
  anomalyScope?: 'window' | 'hour' | 'none';
  selectedRisk?: string;
}
export function AnalysisContextBanner({ context, section }: { context: AnalysisContext; section: '问题研判' | '方案效果' }) {
  const label = { current: '现存问题分析', history: '现存问题分析 · 历史回顾', forecast: '预测分析', mixed: '现存研判 + 预测分析' }[context.kind];
  return <header className="ain-workspace-context" data-kind={context.kind}>
    <div><span>{label}</span><h2>{section}</h2><small>{context.history.scenario} · 北京时间</small></div>
    {context.question && <p className="ain-workspace-question">{context.question}</p>}
    <dl>
      <div><dt>{context.kind === 'forecast' ? '参考历史' : '分析数据'}</dt><dd>{context.history.start} — {context.history.end}</dd></div>
      {context.baseTime && <div><dt>预测基准</dt><dd>{context.baseTime}</dd></div>}
      {context.future && <div><dt>预测关注</dt><dd>{context.future.start} — {context.future.end}</dd></div>}
      {context.selectedRisk && <div><dt>所选风险</dt><dd>{context.selectedRisk}</dd></div>}
      {context.anomalyScope === 'hour' && <div><dt>异常范围</dt><dd>整窗均值正常，仅个别片段越阈</dd></div>}
    </dl>
    {Boolean(context.anomalySpans?.length) && <details className="ain-workspace-events">
      <summary>异常发生区间（{context.anomalySpans!.length} 段）</summary>
      <ul>{context.anomalySpans!.map((span, i) => <li key={`${span.apId}-${span.start}-${i}`}>{span.apId} · {span.start} — {span.end}（{span.minutes} 分钟）</li>)}</ul>
    </details>}
    {section === '方案效果' && context.future && <p className="ain-workspace-context-note">针对上述预测风险制定方案；效果为历史数据基线上的仿真估算。</p>}
  </header>;
}
