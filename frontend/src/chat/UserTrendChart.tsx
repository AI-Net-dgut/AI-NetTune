import { useState } from 'react';
import type { ChatResult } from '../types/backend';
import ChartPanel from '../components/charts/ChartPanel';
import { userTrendMetrics, userTrendOption, type UserTrendMetric } from './userTrend';
export default function UserTrendChart({ result, initialMetric = 'throughput' }: { result: ChatResult; initialMetric?: UserTrendMetric }) {
  const [metric, setMetric] = useState<UserTrendMetric>(initialMetric);
  const data = result.userTrend, subject = result.subject;
  if (!subject) return null;
  const valid = data?.points.filter(p => p[metric] !== null).length ?? 0;
  return <section aria-label={`${subject.id} 用户指标趋势`} className="ain-user-trend">
    <div className="ain-ai-chart-actions"><strong>{subject.id} · 用户指标趋势</strong>
      <label>查看指标 <select value={metric} onChange={event => setMetric(event.target.value as UserTrendMetric)}>
        {Object.entries(userTrendMetrics).map(([key, meta]) => <option key={key} value={key}>{meta.label}</option>)}
      </select></label></div>
    <p className="ain-ai-result-scope">{result.scope.start} — {result.scope.end}（北京时间）；
      {subject.apConstraint ? `仅 ${subject.apConstraint} 下的采样` : `服务 AP：${subject.servingAps.join('、') || '未知'}`}；
      当前指标有效 {valid} / {data?.points.length ?? 0} 条。缺失及时间断档不插值。</p>
    {data && valid > 0 ? <ChartPanel title={`${subject.id} · ${userTrendMetrics[metric].label}观测趋势`}
      description="实际采样；阈值来自本次判据，吞吐目标绑定本次基线均值。线条断开表示缺失或采样间断。"
      option={userTrendOption(data, metric, result.userAssessment)} height={230} />
      : <p role="status" className="ain-ai-note">该指标暂无有效趋势采样，无法绘图；缺失数据不视为正常。</p>}
    {metric === 'throughput' && data?.goal?.target != null && <p className="ain-ai-note">
      目标 {data.goal.target.toFixed(2)} Mbps，基线 {data.goal.baseline?.toFixed(2)} Mbps；
      目标用于比较方案预计均值，单次采样低于目标不等于体验故障。</p>}
  </section>;
}
