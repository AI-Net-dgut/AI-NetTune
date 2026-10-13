import { useState } from 'react';
import type { EChartsOption } from 'echarts';
import type { SimulationResult } from '../../types/plan';
import { METRIC_META } from '../../types/backend';
import ChartPanel, { LIGHT_AXIS_STYLE } from '../charts/ChartPanel';
import SpatialChart, { createProjection, SceneAxes, SCENE_VIEWBOX } from '../charts/SpatialChart';
import { displayMetric } from '../../services/metrics';
const colors = ['#67768a', '#2e69d2', '#36b7f3', '#27d7d2', '#58edc6'];
const clock = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
export default function PlanEffects({ result }: { result: SimulationResult }) {
  const [view, setView] = useState<'metrics' | 'trend' | 'coverage'>(result.coverage?.points.length ? 'coverage' : 'metrics');
  const [metricIndex, setMetricIndex] = useState(0);
  const m = result.metrics[metricIndex], meta = m ? METRIC_META[m.key] : null;
  const t = result.trend, tm = t ? METRIC_META[t.metric] : null;
  const option: EChartsOption = view === 'trend' && t && tm ? {
    animation: false, backgroundColor: '#fff', grid: { left: 8, right: 12, top: 24, bottom: 8, containLabel: true },
    tooltip: { trigger: 'axis', renderMode: 'richText' },
    xAxis: { ...LIGHT_AXIS_STYLE, type: 'time', axisLabel: { color: '#617c96', formatter: (n: number) => clock.format(n) } },
    yAxis: { ...LIGHT_AXIS_STYLE, type: 'value', name: tm.unit, scale: true },
    series: [ { type: 'line', name: '保持原配置（仿真）', connectNulls: false, showSymbol: false, itemStyle: { color: '#8da4be' }, data: t.points.map(p => [Date.parse(p.time), p.before]) },
      { type: 'line', name: '采用方案（仿真）', connectNulls: false, showSymbol: false, itemStyle: { color: '#238f90' }, lineStyle: { type: 'dashed' }, data: t.points.map(p => [Date.parse(p.time), p.after]) } ],
  } : { animation: false, backgroundColor: '#fff', grid: { left: 8, right: 12, top: 24, bottom: 8, containLabel: true }, tooltip: { trigger: 'axis', renderMode: 'richText' },
    xAxis: { ...LIGHT_AXIS_STYLE, type: 'category', data: ['保持原配置', '采用方案'] }, yAxis: { ...LIGHT_AXIS_STYLE, type: 'value', name: meta?.unit ?? '', scale: true },
    series: [{ type: 'bar', barMaxWidth: 38, data: [ { value: m?.before ?? null, itemStyle: { color: '#8da4be' } }, { value: m?.after ?? null, itemStyle: { color: '#238f90' } } ] }] };
  const c = result.coverage, projection = c ? createProjection(c.area) : null;
  return <>
    {result.summary && <p className="ain-effect-conclusion">{result.summary}</p>}
    <div className="ain-effect-values">{result.metrics.slice(0, 3).map((item, i) => <div key={`${item.objectId}-${item.key}-${i}`}>
      <span>{item.objectId} · {METRIC_META[item.key].label}</span><strong>{displayMetric(item.before ?? undefined)} <small>→</small> {displayMetric(item.after ?? undefined)}</strong>
      <small>{METRIC_META[item.key].unit} · 差值 {item.before !== null && item.after !== null ? displayMetric(item.after - item.before) : '—'}</small></div>)}</div>
    <div className="ain-ai-chart-actions ain-effect-tabs">
      <button type="button" className="ain-button" aria-pressed={view === 'metrics'} onClick={() => setView('metrics')}>指标对比</button>
      {t?.points.length ? <button type="button" className="ain-button" aria-pressed={view === 'trend'} onClick={() => setView('trend')}>趋势对比</button> : null}
      {c?.points.length && projection ? <button type="button" className="ain-button" aria-pressed={view === 'coverage'} onClick={() => setView('coverage')}>覆盖对比</button> : null}
    </div>
    {view === 'coverage' && c && projection ? <><div className="ain-effect-map-pair">{(['before', 'after'] as const).map(key =>
      <SpatialChart key={key} title={key === 'before' ? '保持原配置（仿真）' : '采用方案（仿真）'} description="RSRP / dBm · 相同坐标与色标" className="ain-effect-map" footer={null}>
        <svg className="ain-spatial-svg" viewBox={SCENE_VIEWBOX} role="img" aria-label={key === 'before' ? '原配置仿真覆盖图' : '新方案仿真覆盖图'}>
          {c.points.filter(p => projection.contains(p) && p[key] !== null).map(p => <rect key={`${p.x},${p.y}`} x={projection.x(p.x)} y={projection.y(p.y + c.cellSize.y)}
            width={projection.x(p.x + c.cellSize.x) - projection.x(p.x)} height={projection.y(p.y) - projection.y(p.y + c.cellSize.y)}
            fill={colors[p[key]! < -105 ? 0 : p[key]! < -95 ? 1 : p[key]! < -85 ? 2 : p[key]! < -75 ? 3 : 4]}><title>{displayMetric(p[key] ?? undefined)} dBm</title></rect>)}
          <SceneAxes area={c.area} projection={projection} /></svg>
      </SpatialChart>)}</div><p className="ain-management-muted">共同色标 / dBm：&lt; −105、−105～−95、−95～−85、−85～−75、≥ −75；空白为无数据。</p></>
      : view === 'trend' && t && tm ? <ChartPanel title={`${t.objectId} · ${tm.label}仿真趋势`} description="灰线：保持原配置；青色虚线：采用方案 · 北京时间" option={option} height={190} />
      : m && meta ? <><label className="ain-effect-metric">对比指标<select value={metricIndex} onChange={e => setMetricIndex(Number(e.target.value))}>
        {result.metrics.map((item, i) => <option key={i} value={i}>{item.objectId} · {METRIC_META[item.key].label}</option>)}</select></label>
        <ChartPanel title={`${m.objectId} · ${meta.label}仿真对比`} description="保持原配置 vs 采用方案 · 相同条件下的仿真结果" option={option} height={180} /></>
      : <p className="ain-management-muted">未返回指标对比数据。</p>}
    <details className="ain-ai-details"><summary>评估方法与条件</summary><p>方法：{result.method || '未提供'}</p>
      <p>假设：{result.assumptions.join('；') || '未提供'}</p><p>限制：{result.limitations.join('；') || '未提供'}</p></details>
  </>;
}
